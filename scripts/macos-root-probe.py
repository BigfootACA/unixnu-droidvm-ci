#!/usr/bin/env python3
"""Write the macOS-root variant of UniXNU scripts/build-verified-root-probe.py.

    macos-root-probe.py SOURCE DESTINATION

The probe builder pins the 21H461 OS and SystemOS images that its policies must describe. For the macOS root (CI matrix
root=macos) the policies describe the 23G93 OS disk and the SystemOS disk of make-macos-systemos.sh instead (the same pins
as owner-policy-dirs.py). DESTINATION goes to the device repository's scripts/, which the unixnu tree copies over the
UniXNU one; the upstream file is left as is.
"""
from pathlib import Path
import sys

REPLACEMENTS = [
    ("'e5634d04a8ac35f024845f583394e0a35deb26a04cb0c00286a8674e25a9a4f2'",
     "'521945de060983ca09bd9a3f1541fe61d9e8c2fa385bc8a41b396936f19f1827'", 1),
    ("'b4908cde41d23e6bc05e6af51a2c9eeb00cf0f863a6a18b1600c96a3fd70414f'",
     "'2c1504f782ff8a9e43c0fad352b265b396692c0197fc971e3502cadc56a3816c'", 2),
    ("expected_count = 1483785 if", "expected_count = 2660361 if", 1),
    (" else 806400\n", " else 1835008\n", 1),
    ("extra_meta['blocks_verified'] != 806400", "extra_meta['blocks_verified'] != 1835008", 1),
    ("image_bytes=3303014400", "image_bytes=7516192768", 1),
]

source, destination = map(Path, sys.argv[1:3])
text = source.read_text()
for old, new, count in REPLACEMENTS:
    if text.count(old) != count:
        raise SystemExit(f'{source}: expected {count} of {old!r}, found {text.count(old)}')
    text = text.replace(old, new)
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(text)
print(f'{destination}: macOS root pins')
