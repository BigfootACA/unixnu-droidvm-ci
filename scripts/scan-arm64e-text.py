#!/usr/bin/env python3
"""Count the instructions of a dyld shared cache's executable mappings that ARMv8.2 (Kunpeng 920) lacks.

    scan-arm64e-text.py CACHE [CACHE.01 ...]

Each class is (mask, value) over the 32-bit words; the kernel's arm64e-compat (overlay patch virt-arm64e-compat) rewrites or
emulates them on first execution.
"""
import struct
import sys

import numpy as np

CLASSES = {
    'PAC/AUT/XPAC data-processing': [(0xFFFFC000, 0xDAC10000), (0xFFFFFBE0, 0xDAC143E0)],
    'PACGA': [(0xFFE0FC00, 0x9AC03000)],
    'BRA*/BLRA*/RETA*': [(0xFE9FF800, 0xD61F0800)],
    'LDRAA/LDRAB': [(0xFF200400, 0xF8200400)],
    'LDAPR': [(0x3FFFFC00, 0x38BFC000)],
    'LDAPUR (loads)': [(0x3F600C00, 0x19400000), (0x3F600C00, 0x19800000), (0x3F600C00, 0x19C00000)],
    'STLUR (stores)': [(0x3FE00C00, 0x19000000)],
    'CFINV/XAFLAG/AXFLAG': [(0xFFFFFFFF, 0xD500401F), (0xFFFFFFFF, 0xD500403F), (0xFFFFFFFF, 0xD500405F)],
    'RMIF/SETF': [(0xFFE07C10, 0xBA000400), (0xFFFFBC1F, 0x3A00080D)],
    'FRINT32/64': [(0xFFBE7C00, 0x1E284000)],
    'SB': [(0xFFFFFFFF, 0xD50330FF)],
    'DIT': [(0xFFFFF0FF, 0xD503405F), (0xFFFFFFC0, 0xD51B42A0)],
    'CNTxCTSS': [(0xFFFFFFC0, 0xD53BE0C0)],
    'BF16/I8MM': [(0xFFE0FC00, 0x6E40EC00), (0xBFE0FC00, 0x0E80A400)],
    'LD64B/ST64B': [(0xFFFFFC00, 0xF83FD000)],
    'CPYx/SETx (MOPS)': [(0x3F200C00, 0x19000400), (0x3F200C00, 0x1D000400)],
    'hint-space PACIBSP/AUTIBSP/BTI': [(0xFFFFFFFF, 0xD503237F), (0xFFFFFFFF, 0xD50323FF), (0xFFFFFF3F, 0xD503241F)],
}


def mappings(path):
    with open(path, 'rb') as f:
        head = f.read(0x200)
        offset, count = struct.unpack_from('<II', head, 16)
        f.seek(offset)
        return [struct.unpack('<QQQII', f.read(32)) for _ in range(count)], head


def main():
    """Only the __text sections of the cache images (the main cache lists them; subcaches hold their bytes)."""
    totals = {name: 0 for name in CLASSES}
    words = 0
    regions = []
    for path in sys.argv[1:]:
        maps, _ = mappings(path)
        regions += [(address, size, file_offset, path) for address, size, file_offset, _, prot in maps if prot & 4]
    _, head = mappings(sys.argv[1])
    images_offset, images_count = struct.unpack_from('<II', head, 0x1C0)
    if not images_count:
        images_offset, images_count = struct.unpack_from('<II', head, 0x18)

    def locate(address):
        for base, size, file_offset, path in regions:
            if base <= address < base + size:
                return path, file_offset + address - base
        return None, None

    with open(sys.argv[1], 'rb') as f:
        f.seek(images_offset)
        images = [struct.unpack('<QQQII', f.read(32))[0] for _ in range(images_count)]
    files = {path: open(path, 'rb') for path in sys.argv[1:]}
    sections = []
    for image in images:
        path, at = locate(image)
        if not path:
            continue
        handle = files[path]
        handle.seek(at)
        header = handle.read(0x8000)
        ncmds = struct.unpack_from('<I', header, 16)[0]
        cursor = 32
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', header, cursor)
            if cmd == 0x19:
                nsects = struct.unpack_from('<I', header, cursor + 64)[0]
                for index in range(nsects):
                    sect = cursor + 72 + index * 80
                    name = header[sect:sect + 16].split(b'\0')[0]
                    address, length = struct.unpack_from('<QQ', header, sect + 32)
                    flags = struct.unpack_from('<I', header, sect + 64)[0]
                    if flags & 0x80000000:          # S_ATTR_PURE_INSTRUCTIONS
                        sections.append((address, length))
            cursor += size
    for address, length in sections:
        path, at = locate(address)
        if not path or length < 4:
            continue
        data = np.memmap(path, dtype='<u4', mode='r', offset=at & ~3, shape=(length // 4,))
        words += data.size
        for name, patterns in CLASSES.items():
            for mask, value in patterns:
                totals[name] += int(np.count_nonzero((data & np.uint32(mask)) == np.uint32(value)))
    print(f'{len(images)} images, {len(sections)} instruction sections')
    print(f'{words} words')
    for name, total in totals.items():
        print(f'{total:>10}  {name}')


if __name__ == '__main__':
    main()
