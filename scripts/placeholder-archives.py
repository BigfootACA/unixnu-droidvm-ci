#!/usr/bin/env python3
"""Create empty placeholder archives (and objects) for the bootstrap pass of a from-scratch kernel build.

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
for name, path in re.findall(r'^\s*(\w+)=(?:\$\{\w+:-)?\$ROOT/(build/[^\s}"]+\.[ao])', script, re.M):
    if name not in checked:
        continue
    archive = tree / path
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b'!<arch>\n' if archive.suffix == '.a' else b'')
        created.append(path)
# The Legacy display archive is linked only if build-report.json carries its hash (build-xnu-4k-baseline.sh).
legacy = tree / 'build/native-legacy-display/libNativeLegacyDisplay.late.a'
if legacy.exists() and not legacy.with_name('build-report.json').exists():
    import hashlib, json
    legacy.with_name('build-report.json').write_text(json.dumps(
        dict(archive_sha256=hashlib.sha256(legacy.read_bytes()).hexdigest(), source_sha256={})) + '\n')
# Archive paths the board's defconfig overrides (CONFIG_XNU_*_ARCHIVE), checked the same way.
config = tree.parent / '.config'
if config.is_file():
    for path in re.findall(r'^CONFIG_XNU_\w+_ARCHIVE="([^"]+\.a)"', config.read_text(), re.M):
        archive = Path(path.replace('{tree}', str(tree)))
        if not archive.exists():
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_bytes(b'!<arch>\n')
            created.append(str(archive.relative_to(tree)) if archive.is_relative_to(tree) else str(archive))
# With both storage transports the baseline compares the bindings.json records of the two archives (and their objects)
# before make: give each placeholder storage archive an empty object and a matching empty record.
import hashlib, json
for path in created:
    archive = (tree / path) if not Path(path).is_absolute() else Path(path)
    if archive.suffix != '.a' or not archive.parent.name.startswith(('native-virtio-block', 'native-ram-block')):
        continue
    obj = archive.with_name(archive.stem.removeprefix('lib') + '.o')
    if not obj.exists():
        obj.write_bytes(b'')
    record = archive.with_name('bindings.json')
    if not record.exists():
        record.write_text(json.dumps(dict(compiled_object_sha256=hashlib.sha256(obj.read_bytes()).hexdigest(),
                                          bindings={})) + '\n')
print('placeholders:', *created, sep='\n  ')
