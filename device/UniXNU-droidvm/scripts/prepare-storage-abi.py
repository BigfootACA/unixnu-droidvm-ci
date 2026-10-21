#!/usr/bin/env python3
"""Install the storage ABI inputs of NativeVirtioBlock from the public source and the recorded audit.

  build/storage-abi/include/IOKit/storage/*.h   IOStorageFamily-315 public headers (refer/IOStorageFamily)
  build/storage-abi/include/TargetConditionals.h
  build/storage-abi/interface-audit.json        scripts/data/storage-interface-audit.json, the result of
                                                scripts/audit-storage-interface-static.py on a prelinked candidate
                                                of the pinned kernelcache (CI artifact storage-audit-droidvm)
"""
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def storage_source(root):
    """refer/IOStorageFamily, or its clone in out/feeds: the build step links only the references it fetches itself."""
    linked = root / 'refer/IOStorageFamily'
    if linked.is_dir():
        return linked
    clones = sorted((root.parents[1] / 'feeds').glob('IOStorageFamily-*'))
    if len(clones) != 1:
        raise SystemExit('IOStorageFamily source not fetched: run "unixnu fetch"')
    return clones[0]


source = storage_source(ROOT)
recorded = ROOT / 'scripts/data/storage-interface-audit.json'
out = ROOT / 'build/storage-abi'
headers = out / 'include/IOKit/storage'
headers.mkdir(parents=True, exist_ok=True)
revision = subprocess.check_output(['git', '-C', source, 'rev-parse', 'HEAD'], text=True).strip()
if revision != 'e45fbe30a7879e0b16e26dddb78538f1b00e0bff':
    raise SystemExit('Unexpected public Darwin 23 IOStorageFamily source')
for path in source.glob('*.h'):
    shutil.copyfile(path, headers / path.name)
sdk = Path(subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip())
shutil.copyfile(sdk / 'usr/include/TargetConditionals.h', out / 'include/TargetConditionals.h')
audit = json.loads(recorded.read_text())
if audit['method_slots'] != 190 or audit['public_source_revision'] != revision:
    raise SystemExit('The recorded storage audit does not match this source')
(out / 'interface-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
print(f'storage ABI: {len(list(headers.glob("*.h")))} headers, recorded audit vtable {audit["vtable"]}')
