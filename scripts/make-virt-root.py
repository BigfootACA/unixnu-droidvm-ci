#!/usr/bin/env python3
"""Owner policies of the iOS root disks for the virtual machine board, on the Linux host.

  make-virt-root.py OS.raw SYSTEMOS.dmg KEYDIR OUTDIR

OS.raw is the IPSW OS image expanded with udif-expand.py (GPT + APFS), SYSTEMOS.dmg the Cryptex1,SystemOS member as is
(a raw APFS container). Both are served read-only as virtio-blk disks. KEYDIR holds owner.pem / owner-public.pem
(RSA-3072, e65537); the private key never leaves the host. OUTDIR receives what build-virt-verified-root.py consumes
(os.manifest.bin, systemos.manifest.bin, trusted-key.bin, os.samples.bin, inputs.json) and verified-root-inputs.tar.enc,
the same files encrypted with the CI artifact key, to publish as a release asset of the CI repository.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(Path('/home/bigfoot/unixnu-droidvm/UniXNU/VerifiedRoot/tools')))
from manifest import make  # noqa: E402

OS_UUID = '04769D26-2CA0-4825-B443-E2E5F88300F2'          # the 21H461 OS volume (UniXNU-sheng root_match_volume_uuid)
SYSTEMOS_UUID = '8846FE78-2910-4297-AF97-D59BB6F8FC16'    # the 21H461 SystemOS volume (offline-owner-systemos-mount.patch)
KEY_FILE = Path(os.environ.get('ARTIFACT_KEY_FILE', '/home/bigfoot/unixnu-droidvm/.ci-artifact-key'))

if len(sys.argv) != 5:
    raise SystemExit(__doc__)
os_image, systemos_image, keys, out = map(Path, sys.argv[1:])
out.mkdir(parents=True, exist_ok=True)
records = {}
for name, image, volume in (('os', os_image, OS_UUID), ('systemos', systemos_image, SYSTEMOS_UUID)):
    work = out / f'{name}-manifest'
    with image.open('rb') as stream:
        record = make(stream, image.stat().st_size, keys / 'owner.pem', keys / 'owner-public.pem', volume, 1, work)
    shutil.copyfile(work / 'manifest.bin', out / f'{name}.manifest.bin')
    shutil.copyfile(work / 'trusted-key.bin', out / 'trusted-key.bin')
    (work / 'hashes.bin').unlink()
    records[name] = record
    print(f'{name}: {record["block_count"]} blocks, image sha256 {record["image_sha256"]}, volume {volume}')
count, size = records['os']['block_count'], records['os']['image_bytes']
indices = [0, 1, count // 2, count - 1]
lengths = [4096, 4096, 4096, size - (count - 1) * 4096]
with os_image.open('rb') as stream, (out / 'os.samples.bin').open('wb') as samples:
    for index, length in zip(indices, lengths):
        stream.seek(index * 4096)
        samples.write(stream.read(length).ljust(4096, b'\0'))
records['os'].update(sample_indices=indices, sample_lengths=lengths)
(out / 'inputs.json').write_text(json.dumps(records, indent=2) + '\n')
members = ['os.manifest.bin', 'systemos.manifest.bin', 'trusted-key.bin', 'os.samples.bin', 'inputs.json']
with tarfile.open(out / 'verified-root-inputs.tar.gz', 'w:gz') as archive:
    for member in members:
        archive.add(out / member, arcname=member)
subprocess.run(['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-salt', '-pass', f'file:{KEY_FILE}',
                '-in', out / 'verified-root-inputs.tar.gz', '-out', out / 'verified-root-inputs.tar.enc'], check=True)
digest = hashlib.sha256((out / 'verified-root-inputs.tar.enc').read_bytes()).hexdigest()
print(f'{out / "verified-root-inputs.tar.enc"}: sha256 {digest}')
