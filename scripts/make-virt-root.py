#!/usr/bin/env python3
"""Owner policies of the root disks for the virtual machine board, on the Linux host.

  make-virt-root.py OS.raw SYSTEMOS.dmg KEYDIR OUTDIR
  make-virt-root.py hash OS.raw SYSTEMOS.dmg PUBLIC.pem OUTDIR    (on the host holding the images)
  make-virt-root.py sign KEYDIR OUTDIR                             (on the host holding the owner key)

OS.raw is the IPSW OS image expanded with udif-expand.py (GPT + APFS), SYSTEMOS.dmg the Cryptex1,SystemOS member as is
(a raw APFS container). Both are served read-only as virtio-blk disks. KEYDIR holds owner.pem / owner-public.pem
(RSA-3072, e65537); the private key never leaves the host. OUTDIR receives what build-virt-verified-root.py consumes
(os.manifest.bin, systemos.manifest.bin, trusted-key.bin, os.samples.bin, inputs.json) and verified-root-inputs.tar.enc,
the same files encrypted with the CI artifact key, to publish as a release asset of the CI repository.

The first form does both steps on one host. The split form hashes where the images are and signs where the key is; only
OUTDIR (the block hash tables, about 8 bytes per KiB of image) moves between them.

The volume UUIDs are the 21H461 ones that the upstream owner patches name; a macOS root has its volumes renamed to them
first (apfs-volumes.py --set-uuid). Override with OS_UUID / SYSTEMOS_UUID in the environment.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import uuid

sys.path.insert(0, os.environ.get('VERIFIEDROOT_TOOLS', '/home/bigfoot/unixnu-droidvm/UniXNU/VerifiedRoot/tools'))
from manifest import public_anchor  # noqa: E402

OS_UUID = os.environ.get('OS_UUID', '04769D26-2CA0-4825-B443-E2E5F88300F2')        # the 21H461 OS volume
SYSTEMOS_UUID = os.environ.get('SYSTEMOS_UUID', '8846FE78-2910-4297-AF97-D59BB6F8FC16')  # the 21H461 SystemOS volume
KEY_FILE = Path(os.environ.get('ARTIFACT_KEY_FILE', '/home/bigfoot/unixnu-droidvm/.ci-artifact-key'))
NAMES = (('os', OS_UUID), ('systemos', SYSTEMOS_UUID))


def hash_image(image, public_key, volume, work):
    """VerifiedRoot manifest.make() without the signature: header.bin, hashes.bin, record.json."""
    work.mkdir(parents=True, exist_ok=True)
    anchor = public_anchor(public_key)
    size = image.stat().st_size
    image_hash, table_hash = hashlib.sha256(), hashlib.sha256()
    count = 0
    with image.open('rb') as stream, (work / 'hashes.bin').open('wb') as hashes:
        remaining = size
        while remaining:
            data = stream.read(min(4096, remaining))
            if len(data) != min(4096, remaining):
                raise ValueError('Short image stream')
            digest = hashlib.sha256(data).digest()
            image_hash.update(data)
            table_hash.update(digest)
            hashes.write(digest)
            remaining -= len(data)
            count += 1
    header = struct.pack('<8s4I2Q16s32s32s32sQ', b'VROOT01\0', 1, 160, 4096, 0, size, count,
                         uuid.UUID(volume).bytes, image_hash.digest(), table_hash.digest(),
                         hashlib.sha256(anchor).digest(), 1)
    (work / 'header.bin').write_bytes(header)
    (work / 'trusted-key.bin').write_bytes(anchor)
    record = dict(version=1, block_bytes=4096, image_bytes=size, block_count=count, volume_uuid=volume.upper(),
                  generation=1, image_sha256=image_hash.hexdigest(), hash_table_sha256=table_hash.hexdigest(),
                  trusted_key_sha256=hashlib.sha256(anchor).hexdigest(),
                  signature_algorithm='RSA3072-PKCS1v1.5-SHA256', signature_verified_by_openssl=True,
                  image_modified=False, hardware_tested=False, boot_root_authenticated=False)
    (work / 'record.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def hash_step(os_image, systemos_image, public_key, out):
    out.mkdir(parents=True, exist_ok=True)
    records = {}
    for (name, volume), image in zip(NAMES, (os_image, systemos_image)):
        records[name] = hash_image(image, public_key, volume, out / f'{name}-manifest')
        print(f'{name}: {records[name]["block_count"]} blocks, image sha256 {records[name]["image_sha256"]}, '
              f'volume {volume}')
    count, size = records['os']['block_count'], records['os']['image_bytes']
    indices = [0, 1, count // 2, count - 1]
    lengths = [4096, 4096, 4096, size - (count - 1) * 4096]
    with os_image.open('rb') as stream, (out / 'os.samples.bin').open('wb') as samples:
        for index, length in zip(indices, lengths):
            stream.seek(index * 4096)
            samples.write(stream.read(length).ljust(4096, b'\0'))
    records['os'].update(sample_indices=indices, sample_lengths=lengths)
    (out / 'inputs.json').write_text(json.dumps(records, indent=2) + '\n')


def sign_step(keys, out):
    for name, _ in NAMES:
        work = out / f'{name}-manifest'
        signature = work / 'signature.bin'
        if (work / 'trusted-key.bin').read_bytes() != public_anchor(keys / 'owner-public.pem'):
            raise SystemExit(f'{name}: hashed for another owner key')
        subprocess.run(['openssl', 'dgst', '-sha256', '-sign', keys / 'owner.pem', '-out', signature,
                        work / 'header.bin'], check=True)
        subprocess.run(['openssl', 'dgst', '-sha256', '-verify', keys / 'owner-public.pem', '-signature', signature,
                        work / 'header.bin'], check=True, capture_output=True)
        if len(signature.read_bytes()) != 384:
            raise SystemExit('Unexpected RSA signature size')
        with (out / f'{name}.manifest.bin').open('wb') as envelope:
            envelope.write((work / 'header.bin').read_bytes())
            envelope.write(signature.read_bytes())
            with (work / 'hashes.bin').open('rb') as hashes:
                shutil.copyfileobj(hashes, envelope, 1 << 20)
        shutil.copyfile(work / 'trusted-key.bin', out / 'trusted-key.bin')
    members = ['os.manifest.bin', 'systemos.manifest.bin', 'trusted-key.bin', 'os.samples.bin', 'inputs.json']
    with tarfile.open(out / 'verified-root-inputs.tar.gz', 'w:gz') as archive:
        for member in members:
            archive.add(out / member, arcname=member)
    subprocess.run(['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-salt', '-pass', f'file:{KEY_FILE}',
                    '-in', out / 'verified-root-inputs.tar.gz', '-out', out / 'verified-root-inputs.tar.enc'], check=True)
    digest = hashlib.sha256((out / 'verified-root-inputs.tar.enc').read_bytes()).hexdigest()
    print(f'{out / "verified-root-inputs.tar.enc"}: sha256 {digest}')


if len(sys.argv) == 6 and sys.argv[1] == 'hash':
    hash_step(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), Path(sys.argv[5]))
elif len(sys.argv) == 4 and sys.argv[1] == 'sign':
    sign_step(Path(sys.argv[2]), Path(sys.argv[3]))
elif len(sys.argv) == 5:
    os_image, systemos_image, keys, out = map(Path, sys.argv[1:])
    hash_step(os_image, systemos_image, keys / 'owner-public.pem', out)
    sign_step(keys, out)
else:
    raise SystemExit(__doc__)
