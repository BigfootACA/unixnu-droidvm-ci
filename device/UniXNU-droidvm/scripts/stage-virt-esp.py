#!/usr/bin/env python3
"""Stage an ESP directory for the virtual machine board.

  EFI/BOOT/BOOTAA64.EFI                UniXNU.efi
  EFI/UniXNU/unixnu.toml               one xnu entry, DarwinBoot loaded as a driver
  EFI/UniXNU/Payloads/DarwinBoot.efi
  EFI/UniXNU/Payloads/kernel.macho     and kernel.macho.adt (scripts/make-virt-adt.py), optional kernel.macho.trustcache

The directory is used by QEMU through vvfat, or packed into a FAT image for crosvm.
"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument('output', type=Path)
parser.add_argument('--unixnu', type=Path, required=True, help='UniXNU.efi')
parser.add_argument('--darwinboot', type=Path, required=True, help='DarwinBoot.efi')
parser.add_argument('--kernel', type=Path, required=True, help='kernel Mach-O')
parser.add_argument('--trust-cache', type=Path,
                    help='trust bundle (make-trust-bundle.py), staged as kernel.macho.trustcache; pass --trust-cache to the ADT too')
parser.add_argument('--initrd', type=Path,
                    help='Data seed (ci/scripts/make-data-seed.sh scratch-data.img) as the entry initrd; pass --data-ramdisk to the ADT')
parser.add_argument('--args', default='-v serial=3 debug=0x14e kt_fixed_zones=120', help='kernel command line')
parser.add_argument('--title', default='UniXNU virt')
parser.add_argument('--timeout', type=int, default=1, help='menu timeout in seconds')
parser.epilog = 'Arguments after -- go to make-virt-adt.py (without --output).'
split = sys.argv.index('--') if '--' in sys.argv else len(sys.argv)
args = parser.parse_args(sys.argv[1:split])
adt_args = sys.argv[split + 1:]
if not re.fullmatch(r'[A-Za-z0-9_=.,: +\-/]*', args.args):
    parser.error('kernel arguments contain unsupported characters')

esp = args.output
if esp.exists():
    raise SystemExit(f'{esp} exists: give a new directory (old ESPs are not deleted wholesale)')
payloads = esp / 'EFI/UniXNU/Payloads'
payloads.mkdir(parents=True)
(esp / 'EFI/BOOT').mkdir(parents=True)
shutil.copyfile(args.unixnu, esp / 'EFI/BOOT/BOOTAA64.EFI')
shutil.copyfile(args.darwinboot, payloads / 'DarwinBoot.efi')
shutil.copyfile(args.kernel, payloads / 'kernel.macho')
if args.trust_cache:
    shutil.copyfile(args.trust_cache, payloads / 'kernel.macho.trustcache')
if args.initrd:
    shutil.copyfile(args.initrd, payloads / 'scratch-data.img')
initrd = 'initrd = "Payloads/scratch-data.img"\n' if args.initrd else ''
subprocess.run([sys.executable, HERE / 'make-virt-adt.py', '--output', payloads / 'kernel.macho.adt', *adt_args], check=True)
(esp / 'EFI/UniXNU/unixnu.toml').write_text(f'''version = 1
[boot]
timeout = {args.timeout}
default = "xnu"
[kits]
autoload = false
[[driver]]
path = "Payloads/DarwinBoot.efi"
[[entry]]
id = "xnu"
title = "{args.title}"
type = "xnu"
path = "Payloads/kernel.macho"
{initrd}args = "{args.args}"
''')
print(f'ESP staged in {esp}')
