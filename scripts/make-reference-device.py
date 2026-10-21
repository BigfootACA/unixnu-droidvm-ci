#!/usr/bin/env python3
"""Derive the device repository of the reference candidate kernel from the DroidVM device repository.

    make-reference-device.py DEVICE DEFCONFIG

DEVICE is a copy of Droid-VM/UniXNU-droidvm, changed in place. Its archive builders that bind to original prelinked classes
(storage audit, NativeVirtioBlock/NativeRamBlock and their bindings, the GPU and legacy display archive) read
build/reference-candidate/kernel.{macho,json}: "a prelinked kernel of this board built WITHOUT CONFIG_XNU_NATIVE_VIRTIO_BLOCK /
RAM_BLOCK / LEGACY_DISPLAY" (board.toml). This drops those builders and the owner-root chain that depends on them from
[kernel] archives, and appends the matching =n overrides to configs/DEFCONFIG (later assignments win). The original kexts
are prelinked at the same addresses, so the result serves as the reference candidate of the full build.
"""
from pathlib import Path
import re
import sys

DROPPED = ('audit-storage-interface-static.py', 'build-verified-root-probe.py', 'build-arm-driver.py NativeVirtioBlock',
           'build-native-virtio-bindings.py', 'build-arm-driver.py NativeRamBlock', 'build-droidvm-gpu.py',
           'build-native-legacy-display.py')
DISABLED = ('NATIVE_VIRTIO_BLOCK', 'NATIVE_RAM_BLOCK', 'NATIVE_LEGACY_DISPLAY', 'VERIFIED_ROOT_PROBE', 'OWNER_ROOT_AUDIT',
            'OWNER_ROOT_TRUST', 'OWNER_CACHE_MOUNT', 'OWNER_CACHE_STARTUP', 'OWNER_BOOT_MANIFEST', 'OWNER_DATA_MOUNT',
            'OWNER_UNION_SYSTEMOS', 'OWNER_CACHE_CARRIER', 'BOARD_LAUNCH_POLICY')

device, defconfig = Path(sys.argv[1]), sys.argv[2]
board = device / 'board.toml'
text = board.read_text()
start = text.index('archives = [', text.index('[kernel]'))
end = text.index('\n]', start)
lines = text[start:end].split('\n')
kept = [line for line in lines if not any(f'"{name}' in line for name in DROPPED)]
removed = [line.strip() for line in lines if line not in kept]
if len(removed) < len(DROPPED):
    raise SystemExit(f'expected archive builders not found: {removed}')
board.write_text(text[:start] + '\n'.join(kept) + text[end:])
config = device / 'configs' / defconfig
original = config.read_text()
# unixnu checks every *_ARCHIVE of the configuration after the builders ran, whether its option is on or not.
archives = sorted(set(re.findall(r'^CONFIG_(XNU_\w+_ARCHIVE)=', original, re.M)))
config.write_text(original.rstrip('\n') + '\n\n# Reference candidate (ci/scripts/make-reference-device.py)\n' +
                  ''.join(f'CONFIG_XNU_{name}=n\n' for name in DISABLED) +
                  ''.join(f'CONFIG_{name}=""\n' for name in archives) + 'CONFIG_XNU_EXTRA_LD_ARCHIVES=""\n')
print('dropped archive builders:', *removed, sep='\n  ')
print('kept:', *[re.sub(r'\s+', ' ', line).strip() for line in kept if line.strip().startswith('"')], sep='\n  ')
