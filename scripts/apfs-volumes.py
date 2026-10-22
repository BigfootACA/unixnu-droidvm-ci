#!/usr/bin/env python3
"""List the APFS volume superblocks of a raw container image, or rewrite a volume UUID.

    apfs-volumes.py IMAGE [OFFSET]                      list every APSB block (oid, xid, role, uuid, name)
    apfs-volumes.py IMAGE OFFSET --set-uuid OLD NEW     rewrite the volume UUID OLD to NEW in every APSB copy

OFFSET is the byte offset of the container (default 0; a GPT image from udif-expand.py has it at the APFS partition).
"""
import mmap
import struct
import sys
import uuid

ROLES = {0: 'none', 1: 'System', 2: 'User', 4: 'Recovery', 8: 'VM', 16: 'Preboot', 32: 'Installer',
         64: 'Data', 128: 'Baseband', 192: 'Update', 256: 'xART', 320: 'Hardware', 384: 'Backup',
         448: 'Sidecar', 512: 'Reserved', 576: 'Enterprise', 640: 'Reserved', 704: 'Prelogin'}


def fletcher64(data):
    lo = hi = 0
    for (word,) in struct.iter_unpack('<I', data):
        lo = (lo + word) % 0xFFFFFFFF
        hi = (hi + lo) % 0xFFFFFFFF
    c0 = 0xFFFFFFFF - ((lo + hi) % 0xFFFFFFFF)
    c1 = 0xFFFFFFFF - ((lo + c0) % 0xFFFFFFFF)
    return struct.pack('<II', c0, c1)


def main():
    path = sys.argv[1]
    base = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0
    rewrite = len(sys.argv) == 6 and sys.argv[3] == '--set-uuid'
    with open(path, 'r+b' if rewrite else 'rb') as f:
        m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_WRITE if rewrite else mmap.ACCESS_READ)
        assert m[base + 32:base + 36] == b'NXSB', 'no container superblock'
        bs = struct.unpack_from('<I', m, base + 36)[0]
        old = uuid.UUID(sys.argv[4]).bytes if rewrite else None
        new = uuid.UUID(sys.argv[5]).bytes if rewrite else None
        changed = 0
        position = m.find(b'APSB', base)
        while position >= 0:
            block = position - 32
            if (block - base) % bs == 0 and fletcher64(m[block + 8:block + bs]) == m[block:block + 8]:
                oid, xid = struct.unpack_from('<QQ', m, block + 8)
                vol = bytes(m[block + 240:block + 256])
                role = struct.unpack_from('<H', m, block + 964)[0]
                name = bytes(m[block + 704:block + 960]).split(b'\0')[0].decode(errors='replace')
                group = uuid.UUID(bytes=bytes(m[block + 1008:block + 1024]))
                if rewrite and vol == old:
                    m[block + 240:block + 256] = new
                    m[block:block + 8] = fletcher64(m[block + 8:block + bs])
                    changed += 1
                    vol = new
                print(f'block {(block - base) // bs:#x} oid {oid:#x} xid {xid:#x} role {ROLES.get(role, role)} '
                      f'uuid {str(uuid.UUID(bytes=vol)).upper()} group {str(group).upper()} name {name!r}')
            position = m.find(b'APSB', position + 4)
        if rewrite:
            m.flush()
            print('rewritten copies:', changed)


if __name__ == '__main__':
    main()
