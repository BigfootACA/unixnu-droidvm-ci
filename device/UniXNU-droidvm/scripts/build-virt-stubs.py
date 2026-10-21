#!/usr/bin/env python3
"""Archive of the board hooks that UniXNU drivers reference but this board does not provide.

NativeRamBlock persists Data pages through NativeUfs's store window (PersistOverlay.h) when native_ufs_store_mode() is
nonzero. The references are weak_import, which a static kernel link does not leave unresolved; these definitions
report no store (mode 0), so the Data RAM disk stays volatile. Linked with -Wl,-force_load (CONFIG_XNU_EXTRA_LD_ARCHIVES).
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/virt-stubs'
OUT.mkdir(parents=True, exist_ok=True)
(OUT / 'VirtStubs.c').write_text('''#include <stdint.h>
unsigned native_ufs_store_mode(void);
int native_ufs_store_open(uint64_t, uint64_t *, const char **);
int native_ufs_store_read(uint64_t, uint32_t, void *);
int native_ufs_store_write(uint64_t, uint32_t, const void *);
int native_ufs_store_sync(void);
unsigned native_ufs_store_gpt_check(void);
unsigned native_ufs_store_mode(void) { return 0; }
int native_ufs_store_open(uint64_t blocks, uint64_t *first, const char **why) { (void)blocks; (void)first; (void)why; return 0; }
int native_ufs_store_read(uint64_t block, uint32_t count, void *data) { (void)block; (void)count; (void)data; return 0; }
int native_ufs_store_write(uint64_t block, uint32_t count, const void *data) { (void)block; (void)count; (void)data; return 0; }
int native_ufs_store_sync(void) { return 0; }
unsigned native_ufs_store_gpt_check(void) { return 0; }
''')
subprocess.run(['xcrun', '--sdk', 'iphoneos', 'clang', '-target', 'arm64-apple-ios17.0', '-mkernel', '-ffreestanding',
                '-O2', '-Wall', '-Werror', '-c', OUT / 'VirtStubs.c', '-o', OUT / 'VirtStubs.o'], check=True)
archive = OUT / 'libVirtStubs.a'
if archive.exists():
    archive.unlink()
subprocess.run(['xcrun', 'ar', 'rcs', archive, OUT / 'VirtStubs.o'], check=True)
print(f'{archive}: board hook stubs')
