#!/usr/bin/env python3
"""Records the bootstrap pass of a clean mainline tree reads before any archive builder ran.

    mainline-bootstrap-placeholders.py TREE

UniXNU wip/droidvm lets the bootstrap pass stand in empty archives for missing link inputs (need_input in
build-xnu-4k-baseline.sh), but two checks before make read more than the archive:
  - the legacy display archive against build/native-legacy-display/build-report.json (sha of the archive);
  - with both storage transports, the bindings.json records of the NativeVirtioBlock and NativeRamBlock archives and
    the sha of their compiled objects.
On a tree without compile records this writes those records for the empty placeholders ("!<arch>\\n" archives, empty
objects, no bindings). The archive builders replace every one of them before the real link.
"""
import hashlib
import json
from pathlib import Path
import sys

tree = Path(sys.argv[1]).resolve()
if (tree / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/startup.o.json').is_file():
    print('compile records present; no placeholders needed')
    raise SystemExit(0)
ARCHIVE = b'!<arch>\n'
written = []


def place(path, data):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(str(path.relative_to(tree)))


legacy = tree / 'build/native-legacy-display'
place(legacy / 'libNativeLegacyDisplay.late.a', ARCHIVE)
place(legacy / 'build-report.json', json.dumps(dict(
    archive_sha256=hashlib.sha256((legacy / 'libNativeLegacyDisplay.late.a').read_bytes()).hexdigest(),
    source_sha256={}, placeholder=True)).encode())
for directory, name in (('native-virtio-block-verified-bundle', 'NativeVirtioBlock'), ('native-ram-block', 'NativeRamBlock')):
    base = tree / 'build' / directory
    place(base / f'lib{name}.a', ARCHIVE)
    place(base / f'{name}.o', b'')
    place(base / 'bindings.json', json.dumps(dict(
        compiled_object_sha256=hashlib.sha256((base / f'{name}.o').read_bytes()).hexdigest(), bindings={},
        placeholder=True)).encode())
print('bootstrap placeholders:', *written, sep='\n  ')
