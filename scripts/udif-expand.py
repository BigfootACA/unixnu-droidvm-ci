#!/usr/bin/env python3
"""Expand an Apple UDIF disk image (.dmg) into the raw disk it describes, on Linux.

  udif-expand.py IMAGE.dmg OUTPUT.raw [--sha256 HEX]

Replaces hdiutil attach for the IPSW OS image (GPT + APFS in LZFSE chunks). Handles the koly trailer, the blkx table
of the XML plist and the chunk types raw (1), zero/ignore (0, 2), zlib (0x80000005), bzip2 (0x80000006), LZFSE
(0x80000007, through liblzfse) and LZMA (0x80000008). Zero runs are left as holes of a sparse output file.
"""
import argparse
import base64
import bz2
import ctypes
import ctypes.util
import hashlib
import lzma
import plistlib
import struct
import sys
import zlib

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument('image')
parser.add_argument('output')
parser.add_argument('--sha256', help='expected SHA-256 of the raw disk')
args = parser.parse_args()

library = ctypes.util.find_library('lzfse') or 'liblzfse.so'
lzfse = ctypes.CDLL(library)
lzfse.lzfse_decode_buffer.restype = ctypes.c_size_t
lzfse.lzfse_decode_buffer.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
lzfse.lzfse_decode_scratch_size.restype = ctypes.c_size_t
scratch = ctypes.create_string_buffer(lzfse.lzfse_decode_scratch_size())


def decode_lzfse(data, size):
    out = ctypes.create_string_buffer(size)
    done = lzfse.lzfse_decode_buffer(out, size, data, len(data), scratch)
    if done != size:
        raise SystemExit(f'LZFSE chunk decoded to {done} bytes, expected {size}')
    return out.raw


with open(args.image, 'rb') as image:
    image.seek(-512, 2)
    trailer = image.read(512)
    if trailer[:4] != b'koly':
        raise SystemExit('no koly trailer: not a UDIF image')
    data_offset, = struct.unpack_from('>Q', trailer, 24)
    xml_offset, xml_length = struct.unpack_from('>QQ', trailer, 216)
    sectors, = struct.unpack_from('>Q', trailer, 492)
    image.seek(xml_offset)
    plist = plistlib.loads(image.read(xml_length))
    tables = [base64.b64decode(entry['Data']) if isinstance(entry['Data'], str) else entry['Data']
              for entry in plist['resource-fork']['blkx']]
    total = sectors * 512
    with open(args.output, 'wb') as out:
        out.truncate(total)
        for table in tables:
            if table[:4] != b'mish':
                raise SystemExit('bad blkx entry')
            first_sector, = struct.unpack_from('>Q', table, 8)
            table_data, = struct.unpack_from('>Q', table, 24)
            count, = struct.unpack_from('>I', table, 200)
            for index in range(count):
                kind, _, sector, length, offset, size = struct.unpack_from('>IIQQQQ', table, 204 + 40 * index)
                if kind in (0x7ffffffe, 0xffffffff, 0, 2):
                    continue
                at = (first_sector + sector) * 512
                want = length * 512
                image.seek(data_offset + table_data + offset)
                packed = image.read(size)
                if kind == 1:
                    chunk = packed
                elif kind == 0x80000005:
                    chunk = zlib.decompress(packed)
                elif kind == 0x80000006:
                    chunk = bz2.decompress(packed)
                elif kind == 0x80000007:
                    chunk = decode_lzfse(packed, want)
                elif kind == 0x80000008:
                    chunk = lzma.decompress(packed)
                else:
                    raise SystemExit(f'unsupported chunk type {kind:#x}')
                if len(chunk) != want:
                    raise SystemExit(f'chunk at sector {first_sector + sector} is {len(chunk)} bytes, expected {want}')
                if chunk.count(0) != len(chunk):
                    out.seek(at)
                    out.write(chunk)

digest = hashlib.sha256()
with open(args.output, 'rb') as raw:
    while block := raw.read(1 << 24):
        digest.update(block)
print(f'{args.output}: {total} bytes, sha256 {digest.hexdigest()}')
if args.sha256 and digest.hexdigest() != args.sha256.lower():
    sys.exit('SHA-256 mismatch')
