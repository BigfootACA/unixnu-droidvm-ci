#!/usr/bin/env python3
"""Build build/virtio-pci/libVirtioPciDrivers.a: the drivers of the virtio-pci functions crosvm (DroidVM) adds beside the disks.

  NativeVirtioGpu        virtio-gpu scanout of the GOP framebuffer (crosvm --gpu)
  NativeVirtioInputPci   virtio-input: touch screen, VNC absolute pointer and keyboard (Home)

Compiled with the flags of the actual iokit/IOPlatformExpert.cpp compile record of the XNU build (as the board's display
probes are), and linked by CONFIG_XNU_EXTRA_LD_ARCHIVES with -force_load: their static initialisers start the driver threads.
Added to [kernel] archives of the device board by the CI overlay (ci/scripts/apply-overlay-board.py).
"""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/virtio-pci'
OUT.mkdir(parents=True, exist_ok=True)
DRIVERS = ('NativeVirtioGpu', 'NativeVirtioInputPci')
archive = OUT / 'libVirtioPciDrivers.a'
record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json')
                    .read_text().strip().rstrip(','))


def compile_driver(name):
    source = ROOT / 'drivers' / name / f'{name}.cpp'
    obj = OUT / f'{name}.o'
    command, arguments, index = [], record['arguments'], 0
    while index < len(arguments):
        value = arguments[index]
        if value == record['file']:
            command.append(str(source))
        elif value == '-o':
            command += ['-o', str(obj)]
            index += 1
        elif value in ('-MF', '-MJ', '-MT'):
            index += 1
        elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
            index += 1
        elif not value.startswith('-W'):
            command.append(value)
        index += 1
    command += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-c99-designator', '-Wno-reorder-init-list',
                '-Wno-deprecated-enum-enum-conversion', '-Wno-unnecessary-virtual-specifier',
                '-I', str(ROOT / '.cache/xnu-work/osfmk')]
    (OUT / f'{name}.compile-command.json').write_text(json.dumps(command, indent=2) + '\n')
    result = subprocess.run(command, cwd=record['directory'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (OUT / f'{name}.compile.log').write_text(result.stdout)
    if result.returncode:
        raise SystemExit(f'{name}:\n' + result.stdout[-4000:])
    return obj


objects = [compile_driver(name) for name in DRIVERS]
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', str(archive), *map(str, objects)], check=True)
print(f'{archive}:', *DRIVERS)
