#!/usr/bin/env python3
"""Create empty placeholder archives for the bootstrap pass of a from-scratch kernel build.

build-xnu-4k-baseline.sh checks `test -f "$ARCHIVE"` for every enabled archive before it runs make, but the archive
builders read the compile records (and link.filelist) that only a make pass leaves behind. On a clean tree the
bootstrap pass therefore exits before make. An empty archive passes the check; the bootstrap link may fail, which the
unixnu driver accepts once the records exist, and the archive builders then replace every placeholder.

  placeholder-archives.py TREE
"""
from pathlib import Path
import re
import sys

tree = Path(sys.argv[1]).resolve()
script = (tree / 'scripts/build-xnu-4k-baseline.sh').read_text()
record = tree / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/startup.o.json'
if record.is_file():
    print('compile records present; no placeholders needed')
    raise SystemExit(0)
checked = set(re.findall(r'test -f "\$(\w+)"', script))
created = []
for name, path in re.findall(r'^\s*(\w+)=(?:\$\{\w+:-)?\$ROOT/(build/[^\s}"]+\.a)', script, re.M):
    if name not in checked:
        continue
    archive = tree / path
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b'!<arch>\n')
        created.append(path)
print('placeholders:', *created, sep='\n  ')
