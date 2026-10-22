#!/usr/bin/env python3
"""Read members of a remote IPSW (zip) with HTTP ranges, without downloading the whole archive.

    ipsw-member.py URL list                  members by uncompressed size
    ipsw-member.py URL get MEMBER OUTPUT     fetch one member (stored members resume an existing partial OUTPUT)
"""
import struct, sys, time, urllib.request, zlib, os
url = sys.argv[1]
def rng(a, b):
    for attempt in range(20):
        try:
            req = urllib.request.Request(url, headers={'Range': f'bytes={a}-{b}'})
            data = urllib.request.urlopen(req, timeout=120).read()
            if len(data) == b - a + 1:
                return data
        except OSError as error:
            print('retry', attempt, error, file=sys.stderr)
        time.sleep(3)
    raise SystemExit(f'range {a}-{b} failed')
size = int(urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=60).headers['Content-Length'])
tail = rng(size - 65536 - 22, size - 1)
i = tail.rfind(b'PK\x05\x06')
cd_size, cd_off = struct.unpack('<II', tail[i+12:i+20])
j = tail.rfind(b'PK\x06\x06')
if j >= 0:
    cd_size, cd_off = struct.unpack('<QQ', tail[j+40:j+56])
cd = rng(cd_off, cd_off + cd_size - 1)
entries, p = {}, 0
while p < len(cd) and cd[p:p+4] == b'PK\x01\x02':
    meth = struct.unpack('<H', cd[p+10:p+12])[0]
    csz, usz = struct.unpack('<II', cd[p+20:p+28])
    nl, xl, cl = struct.unpack('<HHH', cd[p+28:p+34])
    off = struct.unpack('<I', cd[p+42:p+46])[0]
    name = cd[p+46:p+46+nl].decode()
    x = cd[p+46+nl:p+46+nl+xl]; q = 0
    while q < len(x):
        tid, tl = struct.unpack('<HH', x[q:q+4]); d = x[q+4:q+4+tl]
        if tid == 1:
            vals = list(struct.unpack('<%dQ' % (tl // 8), d[:tl // 8 * 8])); k = 0
            if usz == 0xffffffff: usz = vals[k]; k += 1
            if csz == 0xffffffff: csz = vals[k]; k += 1
            if off == 0xffffffff: off = vals[k]
        q += 4 + tl
    entries[name] = (meth, csz, usz, off)
    p += 46 + nl + xl + cl
if sys.argv[2] == 'list':
    for n, (m, c, u, o) in sorted(entries.items(), key=lambda e: -e[1][2]):
        print(f'{u:>14} {m} {n}')
else:
    meth, csz, usz, off = entries[sys.argv[3]]
    h = rng(off, off + 29)
    nl, xl = struct.unpack('<HH', h[26:30])
    start = off + 30 + nl + xl
    out = sys.argv[4]
    have = os.path.getsize(out) if os.path.exists(out) and meth == 0 else 0
    with open(out, 'ab' if have else 'wb') as f:
        if meth == 0:
            a = start + have
            while a < start + csz:
                b = min(a + (64 << 20), start + csz) - 1
                f.write(rng(a, b)); f.flush(); a = b + 1
        else:
            z, a = zlib.decompressobj(-15), start
            while a < start + csz:
                b = min(a + (64 << 20), start + csz) - 1
                f.write(z.decompress(rng(a, b))); a = b + 1
            f.write(z.flush())
    print('ok', out, os.path.getsize(out), usz)
