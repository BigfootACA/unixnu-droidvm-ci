#!/usr/bin/env python3
"""Build build/verified-root-os-xnu-bundle/libVerifiedRootProbe.a for the virtual machine board.

Slim replacement of UniXNU scripts/build-verified-root-probe.py --component os --include-systemos, whose inputs (macOS
hdiutil verification records of the sheng tree) do not exist here. Its inputs come from scripts/make-virt-root.py on the
host, as build/verified-root-inputs/ (CI decrypts them from the encrypted release asset of the CI repository):

  os.manifest.bin, systemos.manifest.bin   VerifiedRoot manifests (kits/VerifiedRoot/tools/manifest.py) of the raw OS
                                           disk and of the SystemOS image, signed with the owner key
  trusted-key.bin                          the owner anchor (VROOTKEY + RSA-3072 modulus + exponent)
  os.samples.bin, inputs.json              four OS blocks and their indices/lengths for the probe's self-check

The archive holds the VerifiedRoot verifier, its XNU crypto adapter, the probe (scripts/qemu-entry/verified-root-probe.c)
and the inputs: _verified_root_manifest/_key (OS policy, used by the probe) and _verified_root_policies/_policy_count
(OS and SystemOS, used by NativeVirtioBlock built with --verified-root --verified-root-bundle).
"""
import hashlib
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / 'build/verified-root-inputs'
OUT = ROOT / 'build/verified-root-os-xnu-bundle'


def require(value, message):
    if not value:
        raise SystemExit(message)


OUT.mkdir(parents=True, exist_ok=True)
meta = json.loads((INPUTS / 'inputs.json').read_text())
os_manifest, systemos_manifest = INPUTS / 'os.manifest.bin', INPUTS / 'systemos.manifest.bin'
key, samples = INPUTS / 'trusted-key.bin', INPUTS / 'os.samples.bin'
for manifest in (os_manifest, systemos_manifest):
    data = manifest.read_bytes()
    # Header '<8s4I2Q16s32s32s32sQ': the anchor digest sits at bytes 120..152.
    require(data[:8] == b'VROOT01\0' and data[120:152] == hashlib.sha256(key.read_bytes()).digest(),
            f'{manifest.name}: not a VerifiedRoot manifest of this anchor')
indices, lengths = meta['os']['sample_indices'], meta['os']['sample_lengths']
require(len(indices) == 4 and len(lengths) == 4 and samples.stat().st_size == 4 * 4096, 'Four OS sample blocks')
assembly = OUT / 'inputs.S'
assembly.write_text(f'''// Generated owner policy inputs (UniXNU-droidvm scripts/build-virt-verified-root.py).
.section __DATA_CONST,__const
.balign 8
.globl _verified_root_manifest, _verified_root_manifest_bytes
_verified_root_manifest:
.incbin "{os_manifest}"
.balign 8
_verified_root_manifest_bytes:
.quad {os_manifest.stat().st_size}
.globl _verified_root_key
_verified_root_key:
.incbin "{key}"
.balign 8
.globl _verified_root_sample_indices
_verified_root_sample_indices:
.quad {','.join(map(str, indices))}
.globl _verified_root_sample_lengths
_verified_root_sample_lengths:
.quad {','.join(map(str, lengths))}
.globl _verified_root_samples
_verified_root_samples:
.incbin "{samples}"
.balign 8
.globl _verified_root_systemos_manifest
_verified_root_systemos_manifest:
.incbin "{systemos_manifest}"
.balign 8
.globl _verified_root_policies, _verified_root_policy_count
_verified_root_policies:
.quad _verified_root_manifest, {os_manifest.stat().st_size}
.quad _verified_root_systemos_manifest, {systemos_manifest.stat().st_size}
_verified_root_policy_count:
.quad 2
''')
record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/bsd_init.o.json')
                    .read_text().strip().rstrip(','))
objects = []
for source in (ROOT / 'kits/VerifiedRoot/src/verified_root.c', ROOT / 'kits/VerifiedRoot/adapters/xnu_crypto.c',
               ROOT / 'scripts/qemu-entry/verified-root-probe.c'):
    destination = OUT / (source.stem + '.o')
    arguments, command, index = record['arguments'], [], 0
    while index < len(arguments):
        value = arguments[index]
        if value == record['file']:
            command.append(str(source))
        elif value == '-o':
            command += ['-o', str(destination)]
            index += 1
        elif value in ('-MF', '-MJ'):
            index += 1
        elif value not in ('-MD', '-MP'):
            command.append(value)
        index += 1
    # The bsd_init record names export directories a clean tree may not have created yet.
    command += ['-I' + str(ROOT / 'kits/VerifiedRoot/include'), '-Werror', '-Wno-missing-include-dirs']
    result = subprocess.run(command, cwd=record['directory'], capture_output=True, text=True)
    (OUT / (source.stem + '.log')).write_text(result.stdout + result.stderr)
    require(not result.returncode, result.stderr[-3500:])
    objects.append(destination)
sdk = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
subprocess.run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk, '-c', assembly, '-o', OUT / 'inputs.o'],
               check=True)
objects.append(OUT / 'inputs.o')
for obj in objects:
    data = obj.read_bytes()
    require(struct.unpack_from('<4I', data) == (0xfeedfacf, 0x100000c, 0, 1), f'{obj.name}: ordinary ARM64 object')
archive = OUT / 'libVerifiedRootProbe.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, *objects], check=True)
(OUT / 'inputs.json').write_text(json.dumps(dict(
    os_manifest_sha256=hashlib.sha256(os_manifest.read_bytes()).hexdigest(),
    systemos_manifest_sha256=hashlib.sha256(systemos_manifest.read_bytes()).hexdigest(),
    trusted_key_sha256=hashlib.sha256(key.read_bytes()).hexdigest(), os=meta['os'], systemos=meta['systemos'],
    archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest()), indent=2) + '\n')
print(f'{archive}: OS and SystemOS owner policies, probe samples {indices}')
