#!/usr/bin/env python3
"""Qualify NativeLegacyDisplay against the original IOMobileFramebufferLegacy of a prelinked candidate and record the result.

  record-virt-display.py CANDIDATE.macho OUTDIR

Run in the build tree after `unixnu build kernel` with com.apple.iokit.IOMobileGraphicsFamily and com.apple.iokit.IOSurface
among the prelink roots (CI: the "Storage audit" step, CANDIDATE = ../kernel/kernel.macho, its plan CANDIDATE.json beside
it). It places the candidate as build/native-prelink/native-owner-ufs-cpu-desktop-ci-candidate.{macho,json}, runs UniXNU
scripts/audit-mobile-graphics-legacy.py on it (no QEMU observation exists here: the audit's analysis file is seeded with the
recorded runtime observation hash it accepts instead; every static check still runs on the candidate), generates
OriginalLegacyFramebufferABI.h, compiles drivers/NativeMobileDisplay/NativeLegacyDisplay.cpp and derives the original
bindings with all the checks of UniXNU-sheng scripts/build-native-legacy-display.py. OUTDIR receives the three files that
scripts/build-virt-display.py takes from scripts/data:
  OriginalLegacyFramebufferABI.h  legacy-original-bindings.S  mobile-graphics-legacy-abi.json
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import struct

from virt_display_abi import (OUT, ROOT, SURFACE_PREFIX, classify, compiled_object, header, require, run, undefined)
from native_prelink import backed_offset, exports, macho

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument('candidate', type=Path, help='prelinked kernel (kernel.macho); its plan is the same path with .json')
parser.add_argument('outdir', type=Path, help='directory that receives the recorded files')
args = parser.parse_args()

candidate = args.candidate.resolve()
plan_path = candidate.with_suffix('.json')
require(candidate.is_file() and plan_path.is_file(), f'{candidate} and {plan_path} are required')
plan = json.loads(plan_path.read_text())
for family in ('com.apple.iokit.IOMobileGraphicsFamily', 'com.apple.iokit.IOSurface'):
    require(family in plan.get('original_executable_sha256', {}),
            f'{family} is not prelinked in {candidate.name}: add it to the [kernel.prelink] roots')

# The audit takes the pinned sheng carrier first, otherwise the newest native-owner-ufs-cpu-desktop-*-candidate.
prelink = ROOT / 'build/native-prelink'
prelink.mkdir(parents=True, exist_ok=True)
require(not (prelink / 'native-owner-mobile-graphics-candidate.macho').exists(),
        'build/native-prelink/native-owner-mobile-graphics-candidate.macho would be audited instead of the candidate')
placed = prelink / 'native-owner-ufs-cpu-desktop-ci-candidate.macho'
if placed.resolve() != candidate:
    shutil.copyfile(candidate, placed)
    shutil.copyfile(plan_path, placed.with_suffix('.json'))
os.utime(placed)
newest = sorted(prelink.glob('native-owner-ufs-cpu-desktop-*-candidate.macho'), key=lambda f: f.stat().st_mtime)[-1]
require(newest.resolve() == placed.resolve(), f'{newest.name} is newer than the placed candidate')

audit_script = ROOT / 'scripts/audit-mobile-graphics-legacy.py'
found = re.search(r"^RUNTIME_SHA='([0-9a-f]{64})'$", audit_script.read_text(), re.M)
require(found, f'{audit_script} names no recorded runtime observation hash')
analysis = ROOT / 'build/display-analysis/mobile-graphics-legacy-abi.json'
if not (ROOT / 'build/darwinboot-owner-hvf-mobile-graphics-legacy-class/gdb-stop.json').exists():
    analysis.parent.mkdir(parents=True, exist_ok=True)
    try:
        seeded = json.loads(analysis.read_text()).get('runtime_metadata_checkpoint_sha256')
    except (OSError, ValueError, AttributeError):
        seeded = None
    if seeded != found.group(1):
        analysis.write_text(json.dumps({'runtime_metadata_checkpoint_sha256': found.group(1)}, indent=2) + '\n')
print(run(['python3', audit_script], text=True), end='')
audit = json.loads(analysis.read_text())
require(audit['candidate_sha256'] == plan['candidate_sha256'], 'The audit ran on the placed candidate')
pure = audit['abstract_slots']
methods = audit['methods']

OUT.mkdir(parents=True, exist_ok=True)
(OUT / 'OriginalLegacyFramebufferABI.h').write_text(header(pure))
print(run(['python3', ROOT / 'scripts/build-arm-driver.py', 'NativeLegacyDisplay'], text=True), end='')
raw, image, symbols, compiled = compiled_object(OUT / 'NativeLegacyDisplay.o')
owned, bindings, native = classify(compiled, audit)

old = placed.read_bytes()
old_image = macho(old)
names = {n: v for v, group in exports(old, old_image).items() for n in group}


def read(va, n):
    at = backed_offset(old_image['segments'], va, n, len(old))
    return old[at:at + n]


for i, name, expected in native:
    require(i < 168 and names.get(name) == expected, 'Actual inherited native base symbol ' + str(i))
legacy_imports = {n for n in undefined(symbols) if 'IOMobileFramebufferLegacy' in n}
require(legacy_imports == set(bindings), 'All and only actual original-class imports qualified')
# Exact original IOSurface getters, independently observed through the real original swap request. These aliases call
# original code; no object layout is substituted and no original method/vtable is patched.
for name, address, field, wide in [
        ('descriptor', 0xfffffff004b9ead0, 48, True), ('width', 0xfffffff004b9ebbc, 88, True),
        ('height', 0xfffffff004b9ebc4, 96, True), ('stride', 0xfffffff004b9ebd4, 104, True),
        ('allocation', 0xfffffff004b9ebfc, 144, True), ('format', 0xfffffff004b9ebac, 128, False),
        ('planes', 0xfffffff004b9ebf4, 152, False)]:
    expected = (0xf9400000 if wide else 0xb9400000) | ((field // (8 if wide else 4)) << 10)
    require(read(address, 8) == struct.pack('<2I', expected, 0xd65f03c0), 'Exact original surface getter ' + name)
    bindings[SURFACE_PREFIX + name] = address
surface_imports = {n for n in undefined(symbols) if n.startswith(SURFACE_PREFIX)}
require(surface_imports <= set(bindings), f'Unqualified IOSurface getters: {sorted(surface_imports - set(bindings))}')


# Both protected destructors/constructors must follow the exact original chain.
def branch(pc):
    word = struct.unpack('<I', read(pc, 4))[0]
    require(word & 0xfc000000 == 0x94000000, 'Actual protected base call')
    value = word & 0x3ffffff
    return pc + 4 * (value - (1 << 26) if value & (1 << 25) else value)


require(branch(0xfffffff0057865fc) == 0xfffffff00577b510 and branch(0xfffffff005786630) == 0xfffffff00577b55c,
        'Original protected Legacy C2/D2 really call their original Service bases')
# Actual caller passes only self at slot 288 and tests bit zero of w0.
require(read(0xfffffff005787ef8, 16) == bytes.fromhex('080040f9088144f900013fd6e0000037'),
        'Original bool start_hardware() call and result check')
getter = int(methods[249]['address'], 16)
for offset, field in [(0x30, 5808), (0x58, 5812)]:
    require(struct.unpack('<I', read(getter + offset, 4))[0] == 0xb9400000 | ((field // 4) << 10) | (8 << 5) | 8,
            'Actual original width/height getter fields')
require(int(methods[295]['address'], 16) == 0xfffffff005795b88 and
        read(0xfffffff005795b98, 16) == bytes.fromhex('e00740f9280080520101001272d3ff97'),
        'Actual original slot-295 passes true to the normal current completion body')
require(read(0xfffffff00578aa7c, 24) == bytes.fromhex('080040f9084545f9030080d2e20303aa0400805200013fd6'),
        'Original completion calls optional report slot 337 with null outputs and zero limit')
require(read(0xfffffff005777e9c, 16) == bytes.fromhex('080040f908e142f900013fd6c0000036'),
        'Original isMainDisplay dispatch uses only self and tests boolean bit zero')

bindings_text = ''.join(f'.globl {name}\n.set {name}, {va:#x}\n' for name, va in sorted(bindings.items()))
(OUT / 'original-bindings.S').write_text(bindings_text)
sdk = run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk, '-c', OUT / 'original-bindings.S',
     '-o', OUT / 'original-bindings.o'])
outdir = args.outdir.resolve()
outdir.mkdir(parents=True, exist_ok=True)
shutil.copyfile(OUT / 'OriginalLegacyFramebufferABI.h', outdir / 'OriginalLegacyFramebufferABI.h')
(outdir / 'legacy-original-bindings.S').write_text(bindings_text)
shutil.copyfile(analysis, outdir / 'mobile-graphics-legacy-abi.json')
print(f'Recorded NativeLegacyDisplay: {len(bindings)} original bindings, owned slots {owned}, '
      f'{len(native)} inherited native entries, candidate {plan["candidate_sha256"]} -> {outdir}')
