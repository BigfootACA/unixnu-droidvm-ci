#!/usr/bin/env python3
"""Archive of empty probes that UniXNU patches call unconditionally but this board does not link.

offline-component-load-probes.patch calls native_legacy_display_probe() behind a boot argument whether or not
NativeLegacyDisplay is linked. The stub is linked through CONFIG_XNU_EXTRA_LD_ARCHIVES, so no UniXNU patch changes.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/virt-stubs'
OUT.mkdir(parents=True, exist_ok=True)
SOURCE = '''
void native_legacy_display_probe(void);
void native_legacy_display_probe(void) {}
'''
(OUT / 'VirtStubs.c').write_text(SOURCE)
subprocess.run(['xcrun', '--sdk', 'iphoneos', 'clang', '-target', 'arm64-apple-ios17.0', '-mkernel', '-ffreestanding',
                '-O2', '-Wall', '-Werror', '-c', OUT / 'VirtStubs.c', '-o', OUT / 'VirtStubs.o'], check=True)
archive = OUT / 'libVirtStubs.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, OUT / 'VirtStubs.o'], check=True)
print(f'{archive}: virt stubs')
