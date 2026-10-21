#!/usr/bin/env python3
"""Symbolize the user backtraces printed by the crash-log boot-arg (patches/xnu/virt-crash-log.patch).

    crash-symbolize.py SERIAL_LOG DSC IPSW [PROCESS[,PROCESS...]]

DSC is the dyld_shared_cache_arm64 of the SystemOS volume, IPSW a build of github.com/blacktop/ipsw. Addresses inside
the shared cache are unslid with the printed slid cache base and named with `ipsw dyld a2s`; other addresses are shown
relative to the main image of the process. Each distinct backtrace is printed once with its count.
"""
import re
import subprocess
import sys

DSC_UNSLID_BASE = 0x180000000
DSC_SIZE = 0x100000000
HEADER = re.compile(r'crash: pid (\d+) (\S+) exc (\d+) code (0x[0-9a-f]+) (0x[0-9a-f]+) cache (0x[0-9a-f]+) main (0x[0-9a-f]+)')
REGISTERS = re.compile(r'crash: pid (\d+) pc (0x[0-9a-f]+) lr (0x[0-9a-f]+) .* far (0x[0-9a-f]+) esr (0x[0-9a-f]+)')
FRAMES = re.compile(r'crash: pid (\d+) #\d+ ((?:0x[0-9a-f]+ ?)+)')


def main():
    log, dsc, ipsw = sys.argv[1:4]
    wanted = set(sys.argv[4].split(',')) if len(sys.argv) > 4 else None
    crashes, current = [], {}
    with open(log, errors='replace') as stream:
        for line in stream:
            if match := HEADER.search(line):
                current[match[1]] = {'name': match[2], 'exc': int(match[3]), 'code': (match[4], match[5]),
                                     'cache': int(match[6], 16), 'main': int(match[7], 16), 'regs': None, 'frames': []}
                crashes.append(current[match[1]])
            elif (match := REGISTERS.search(line)) and match[1] in current:
                current[match[1]]['regs'] = (int(match[2], 16), int(match[3], 16), match[4], match[5])
            elif (match := FRAMES.search(line)) and match[1] in current:
                current[match[1]]['frames'] += [int(word, 16) for word in match[2].split() if int(word, 16)]
    known = {}

    def name(crash, address):
        cache = crash['cache']
        if cache and cache <= address < cache + DSC_SIZE:
            unslid = address - cache + DSC_UNSLID_BASE
            if unslid not in known:
                output = subprocess.run([ipsw, 'dyld', 'a2s', '--no-color', dsc, hex(unslid), '-i'],
                                        capture_output=True, text=True).stdout.strip().splitlines()
                image = next((entry.split('>', 1)[1].split()[0].rsplit('/', 1)[-1] for entry in output
                              if entry.lstrip().startswith('>')), '?')
                known[unslid] = f'{image}: {output[-1].split(": ", 1)[-1].strip()}' if output else hex(unslid)
            return known[unslid]
        if crash['main'] and address >= crash['main']:
            return f'{crash["name"]}+{address - crash["main"]:#x}'
        return hex(address)

    seen = {}
    for crash in crashes:
        if wanted and crash['name'] not in wanted:
            continue
        key = (crash['name'], crash['exc'], tuple(address - crash['cache'] for address in crash['frames']))
        seen.setdefault(key, [crash, 0])[1] += 1
    for (_, _, _), (crash, count) in seen.items():
        print(f'=== {crash["name"]} exc {crash["exc"]} code {crash["code"][0]} {crash["code"][1]} (x{count})')
        if crash['regs']:
            pc, lr, far, esr = crash['regs']
            print(f'  pc {name(crash, pc)}\n  lr {name(crash, lr)}\n  far {far} esr {esr}')
        for address in crash['frames']:
            print(f'    {name(crash, address)}')


if __name__ == '__main__':
    main()
