#!/usr/bin/env python3
"""Reassemble the stackshots printed by the stackshot-dump boot-arg (patches/xnu/virt-stackshot-dump.patch).

    stackshot-from-serial.py SERIAL_LOG OUTPUT_PREFIX

Every complete stackshot <seq> of the log is written to OUTPUT_PREFIX-<seq>.kcdata, to be decoded with the kcdata.py of
the xnu tree (tools/lldbmacros/kcdata.py). Lines whose checksum fails (console output interleaved with kprintf) are
dropped; a stackshot that misses any line is reported and skipped.
"""
import base64
import re
import sys

LINE = re.compile(rb'SSHOT (\d+) ([0-9a-f]+) ([0-9a-f]+) ([0-9a-f]{6}) ([A-Za-z0-9+/=]+)')


def checksum(data):
    total = 0
    for byte in data:
        total = (total * 31 + byte) & 0xffffff
    return total


def main():
    log, prefix = sys.argv[1], sys.argv[2]
    shots = {}
    bad = 0
    with open(log, 'rb') as stream:
        for raw in stream:
            match = LINE.search(raw)
            if not match:
                continue
            seq, offset, total = int(match[1]), int(match[2], 16), int(match[3], 16)
            try:
                data = base64.b64decode(match[5], validate=True)
            except ValueError:
                bad += 1
                continue
            if checksum(data) != int(match[4], 16):
                bad += 1
                continue
            shot = shots.setdefault(seq, {'total': total, 'chunks': {}})
            shot['chunks'][offset] = data
    for seq, shot in sorted(shots.items()):
        buffer = bytearray(shot['total'])
        covered = 0
        for offset, data in shot['chunks'].items():
            buffer[offset:offset + len(data)] = data
            covered += len(data)
        if covered != shot['total']:
            print(f'stackshot {seq}: {covered}/{shot["total"]} bytes, incomplete', file=sys.stderr)
            continue
        path = f'{prefix}-{seq}.kcdata'
        with open(path, 'wb') as out:
            out.write(buffer)
        print(f'stackshot {seq}: {shot["total"]} bytes -> {path}')
    if bad:
        print(f'{bad} damaged lines dropped', file=sys.stderr)


if __name__ == '__main__':
    main()
