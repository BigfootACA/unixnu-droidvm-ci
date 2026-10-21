#!/usr/bin/env python3
"""Build a tiny Mach-O "kernel" that DarwinBoot accepts and that prints on a PL011 at its entry.

It checks the DarwinBoot handoff without XNU: the entry runs with the MMU off, prints a banner, CurrentEL,
x0 (boot_args) and the boot_args revision/version to the UART, then waits. Layout as DarwinBoot/Test/make-fixtures.py.
"""
import argparse
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

BASE = 0xfffffff007004000

SOURCE = r'''
.section .text, "ax"
.global _start
_start:
  mov x19, x0
  ldr x20, =UART
  adr x0, banner
  bl puts
  mrs x0, CurrentEL
  lsr x0, x0, #2
  bl hex
  adr x0, args
  bl puts
  mov x0, x19
  bl hex
  adr x0, revision
  bl puts
  ldr w0, [x19]
  bl hex
  adr x0, cmdline
  bl puts
  add x0, x19, #0x6c
  bl puts
  adr x0, done
  bl puts
1:
  wfe
  b 1b

// x0: NUL-terminated string
puts:
  ldrb w1, [x0], #1
  cbz w1, 3f
2:
  ldr w2, [x20, #0x18]
  tbnz w2, #5, 2b
  str w1, [x20]
  b puts
3:
  ret

// x0: value printed as 16 hex digits
hex:
  mov x3, #60
4:
  lsr x1, x0, x3
  and x1, x1, #0xf
  cmp x1, #10
  add x2, x1, #'0'
  add x1, x1, #('a' - 10)
  csel x1, x2, x1, lo
5:
  ldr w2, [x20, #0x18]
  tbnz w2, #5, 5b
  str w1, [x20]
  subs x3, x3, #4
  b.pl 4b
  ret

banner:   .asciz "\r\nUNIXNU-HELLO: kernel entry, CurrentEL="
args:     .asciz "\r\nUNIXNU-HELLO: boot_args="
revision: .asciz " revision/version="
cmdline:  .asciz "\r\nUNIXNU-HELLO: cmdline="
done:     .asciz "\r\nUNIXNU-HELLO: PASS\r\n"
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='Mach-O file to write')
    parser.add_argument('--uart', type=lambda value: int(value, 0), default=0x09000000, help='PL011 physical address')
    args = parser.parse_args()
    tools = {name: shutil.which(name) for name in ('clang', 'ld.lld', 'llvm-objcopy')}
    if not all(tools.values()):
        parser.error('clang, ld.lld and llvm-objcopy are required on PATH')
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        (work / 'kernel.S').write_text(SOURCE)
        subprocess.run([tools['clang'], '--target=aarch64-none-elf', '-c', f'-DUART={args.uart:#x}',
                        work / 'kernel.S', '-o', work / 'kernel.o'], check=True)
        subprocess.run([tools['ld.lld'], f'-Ttext={BASE + 0x1000:#x}', '-e', '_start',
                        work / 'kernel.o', '-o', work / 'kernel.elf'], check=True)
        subprocess.run([tools['llvm-objcopy'], '-O', 'binary', work / 'kernel.elf', work / 'code.bin'], check=True)
        code = (work / 'code.bin').read_bytes()
    if len(code) > 0x1000:
        raise SystemExit('code exceeds the TEXT page')

    def segment(name, address, size, offset, length, protection):
        return struct.pack('<II16sQQQQiiII', 0x19, 72, name, address, size, offset, length,
                           protection, protection, 0, 0)

    state = bytearray(272)
    struct.pack_into('<Q', state, 256, BASE + 0x1000)
    commands = (segment(b'__TEXT', BASE, 0x2000, 0, 0x2000, 5)
                + segment(b'__DATA', BASE + 0x4000, 0x1000, 0, 0, 3)
                + struct.pack('<IIII', 5, 288, 6, 68) + state)
    image = bytearray(0x2000)
    image[:32] = struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 2, 3, len(commands), 0, 0)
    image[32:32 + len(commands)] = commands
    image[0x1000:0x1000 + len(code)] = code
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(image)
    print(f'{args.output}: {len(image)} bytes, PL011 {args.uart:#x}')


if __name__ == '__main__':
    main()
