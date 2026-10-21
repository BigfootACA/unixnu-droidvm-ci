#!/usr/bin/env python3
"""Add the overlay's own archive builders to a copy of the DroidVM device repository.

    apply-overlay-board.py DEVICE DEFCONFIG

Appends to [kernel] archives of DEVICE/board.toml the builders listed below (overlay/droidvm/scripts, copied into
DEVICE/scripts by the workflow) and adds their archives to CONFIG_XNU_EXTRA_LD_ARCHIVES of DEVICE/configs/DEFCONFIG
(force-loaded: their static initialisers start the drivers).
"""
from pathlib import Path
import re
import sys

BUILDERS = {'build-virtio-pci-drivers.py': '{tree}/build/virtio-pci/libVirtioPciDrivers.a'}

device, defconfig = Path(sys.argv[1]), sys.argv[2]
board = device / 'board.toml'
text = board.read_text()
start = text.index('archives = [', text.index('[kernel]'))
end = text.index('\n]', start)
added = ''.join(f'\n  "{name}",' for name in BUILDERS if f'"{name}"' not in text[start:end])
board.write_text(text[:end] + added + text[end:])
config = device / 'configs' / defconfig
lines = config.read_text().splitlines()
current = ''
for line in lines:
    match = re.fullmatch(r'CONFIG_XNU_EXTRA_LD_ARCHIVES="(.*)"', line.strip())
    if match:
        current = match.group(1)
flags = ' '.join([current] + [f'-Wl,-force_load,{archive}' for archive in BUILDERS.values() if archive not in current]).strip()
lines = [line for line in lines if not line.strip().startswith('CONFIG_XNU_EXTRA_LD_ARCHIVES=')]
lines.append(f'CONFIG_XNU_EXTRA_LD_ARCHIVES="{flags}"')
config.write_text('\n'.join(lines) + '\n')
print('archives added:', added.strip() or 'none')
print('CONFIG_XNU_EXTRA_LD_ARCHIVES:', flags)
