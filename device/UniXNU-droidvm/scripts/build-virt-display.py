#!/usr/bin/env python3
"""Build build/native-legacy-display/libNativeLegacyDisplay.late.a: NativeLegacyDisplay and the VM display probe.

NativeLegacyDisplay (drivers/NativeMobileDisplay) derives from the original IOMobileFramebufferLegacy of the pinned
kernelcache. Its generated OriginalLegacyFramebufferABI.h, the original bindings and the audit they were qualified with are
recorded in scripts/data by scripts/record-virt-display.py (run by CI on a prelinked candidate, artifact
storage-audit-droidvm). With them the archive holds, as UniXNU-sheng scripts/build-native-legacy-display.py builds it:
  NativeLegacyDisplay.late.o   static initialiser removed and globalised, run by native_legacy_display_probe()
  original-bindings.o          the recorded original addresses (every original-class import of the object)
  VirtDisplayProbe.o           native_legacy_display_probe / native_legacy_display_late_probe and the observers
Without the recorded files the archive only holds empty native_legacy_display_probe / native_legacy_display_late_probe.

Linked by build-xnu-4k-baseline.sh with CONFIG_XNU_NATIVE_LEGACY_DISPLAY=y (force_load, checked against build-report.json).
"""
import json
import re
import shutil
import subprocess

from virt_display_abi import (OUT, RECORDED_AUDIT, RECORDED_BINDINGS, RECORDED_HEADER, ROOT, SURFACE_GETTERS, SURFACE_PREFIX, classify,
                              compiled_object, defined, late_object, macho, object_symbols, require, run, sha, undefined)

OUT.mkdir(parents=True, exist_ok=True)
archive = OUT / 'libNativeLegacyDisplay.late.a'
archive.unlink(missing_ok=True)
for stale in ('NativeLegacyDisplay.late.o', 'original-bindings.o', 'VirtDisplayProbe.o', 'VirtDisplayStubs.o', 'build-report.json'):
    (OUT / stale).unlink(missing_ok=True)
sdk = run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
sources = [ROOT / 'scripts/build-virt-display.py', ROOT / 'scripts/virt_display_abi.py']
recorded = [RECORDED_HEADER, RECORDED_BINDINGS, RECORDED_AUDIT]
report = {}

if all(path.is_file() for path in recorded):
    audit = json.loads(RECORDED_AUDIT.read_text())
    shutil.copyfile(RECORDED_HEADER, OUT / 'OriginalLegacyFramebufferABI.h')
    print(run(['python3', ROOT / 'scripts/build-arm-driver.py', 'NativeLegacyDisplay'], text=True), end='')
    obj = OUT / 'NativeLegacyDisplay.o'
    raw, image, symbols, compiled = compiled_object(obj)
    owned, bindings, native = classify(compiled, audit)
    # Inherited native base entries resolve to this kernel's own IOService methods; the original Legacy class must not
    # override them (the candidate check of record-virt-display.py compares their addresses).
    changed = set(audit['inherited_generic_changes'])
    for i, name, _ in native:
        require(i < 168 and i not in changed, f'Inherited native base entry {i} ({name}) is an original override')
    text = RECORDED_BINDINGS.read_text()
    bound = {name: int(value, 16) for name, value in re.findall(r'^\.set (\S+), (0x[0-9a-f]+)$', text, re.M)}
    require(set(re.findall(r'^\.globl (\S+)$', text, re.M)) == set(bound), 'Every recorded binding is a global absolute symbol')
    for name, address in bindings.items():
        require(bound.get(name) == address, f'Recorded binding of {name} differs from the recorded audit')
    imports = undefined(symbols)
    legacy_imports = {name for name in imports if 'IOMobileFramebufferLegacy' in name}
    require(legacy_imports == set(bindings), f'Original-class imports outside the recorded bindings: {sorted(legacy_imports ^ set(bindings))}')
    surface_imports = {name for name in imports if name in SURFACE_GETTERS}
    surface_bound = {name for name in bound if name in SURFACE_GETTERS}
    require(surface_imports and surface_imports <= surface_bound, f'IOSurface getters not recorded: {sorted(surface_imports - surface_bound)}')
    require(set(bound) == set(bindings) | surface_bound, f'Unexpected recorded bindings: {sorted(set(bound) - set(bindings) - surface_bound)}')
    shutil.copyfile(RECORDED_BINDINGS, OUT / 'original-bindings.S')
    run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk, '-c', OUT / 'original-bindings.S',
         '-o', OUT / 'original-bindings.o'])
    late = OUT / 'NativeLegacyDisplay.late.o'
    late_object(obj, late)
    # The probe is compiled with the flags of the actual IOPlatformExpert compile record, as the sheng probe is.
    record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json')
                        .read_text().strip().rstrip(','))
    source = ROOT / 'drivers/NativeMobileDisplay/VirtDisplayProbe.cpp'
    probe = OUT / 'VirtDisplayProbe.o'
    command, arguments, i = [], record['arguments'], 0
    while i < len(arguments):
        value = arguments[i]
        if value == record['file']:
            command.append(str(source))
        elif value == '-o':
            command += ['-o', str(probe)]
            i += 1
        elif value == '-Xclang' and arguments[i + 1].startswith('-W'):
            i += 1
        elif not value.startswith('-W'):
            command.append(value)
        i += 1
    command += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unnecessary-virtual-specifier', '-Wno-c99-designator',
                '-Wno-reorder-init-list', '-Wno-deprecated-enum-enum-conversion', '-I', str(ROOT / '.cache/xnu-work/osfmk')]
    (OUT / 'probe-compile-command.json').write_text(json.dumps(command, indent=2) + '\n')
    result = subprocess.run(command, cwd=record['directory'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (OUT / 'probe-compile.log').write_text(result.stdout)
    if result.returncode:
        raise SystemExit(result.stdout[-3500:])
    # Every board hook the display object calls is defined by the probe, and the probe's deferred initialiser exists.
    probe_raw = probe.read_bytes()
    probe_symbols = object_symbols(probe_raw, macho(probe_raw))
    late_raw = late.read_bytes()
    late_symbols = object_symbols(late_raw, macho(late_raw))
    hooks = {name for name in imports if name.startswith('_native_legacy_') and name not in SURFACE_GETTERS}
    require(hooks <= defined(probe_symbols), f'Display hooks the probe does not define: {sorted(hooks - defined(probe_symbols))}')
    wanted = {name for name in undefined(probe_symbols) if name.startswith(('_native_legacy_', '__GLOBAL__sub_I_NativeLegacyDisplay'))}
    require(wanted <= defined(late_symbols), f'Probe imports NativeLegacyDisplay does not define: {sorted(wanted - defined(late_symbols))}')
    require({'_native_legacy_display_probe', '_native_legacy_display_late_probe'} <= defined(probe_symbols), 'Both kernel hooks defined')
    run(['xcrun', 'ar', 'rcs', archive, late, OUT / 'original-bindings.o', probe])
    sources += [ROOT / 'drivers/NativeMobileDisplay/NativeLegacyDisplay.cpp', ROOT / 'drivers/NativeMobileDisplay/NativeLegacyRequest.h',
                source, *recorded]
    report.update(mode='recorded', compiled_object_sha256=sha(obj), late_object_sha256=sha(late),
                  bindings={name: hex(value) for name, value in sorted(bound.items())}, owned_entries=owned,
                  inherited_native_entries=[i for i, _, _ in native], recorded_candidate_sha256=audit['candidate_sha256'])
    mode = 'NativeLegacyDisplay with the recorded original bindings'
else:
    missing = [str(path.relative_to(ROOT)) for path in recorded if not path.is_file()]
    stub = OUT / 'VirtDisplayStubs.c'
    stub.write_text('// No recorded original IOMobileFramebufferLegacy bindings (scripts/data): the display hooks do nothing.\n'
                    'void native_legacy_display_probe(void);\n'
                    'void native_legacy_display_probe(void) {}\n'
                    'void native_legacy_display_late_probe(void);\n'
                    'void native_legacy_display_late_probe(void) {}\n')
    run(['xcrun', '--sdk', 'iphoneos', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk, '-mkernel',
         '-ffreestanding', '-O2', '-Wall', '-Werror', '-c', stub, '-o', OUT / 'VirtDisplayStubs.o'])
    run(['xcrun', 'ar', 'rcs', archive, OUT / 'VirtDisplayStubs.o'])
    report.update(mode='stub', missing=missing)
    mode = 'empty display hooks (no recorded bindings in scripts/data)'

report.update(archive_sha256=sha(archive), source_sha256={str(p.relative_to(ROOT)): sha(p) for p in sources},
              original_instructions_modified=False)
(OUT / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
print(f'{archive}: {mode}')
