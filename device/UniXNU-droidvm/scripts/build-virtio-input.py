#!/usr/bin/env python3
"""Build build/virtio-input/libVirtioInput.a: NativeVirtioInput plus the NativeHidInput devices it starts.

NativeHidInput derives from the original IOHIDDevice of the pinned kernelcache. Its original bindings and the generated
OriginalHidDeviceABI.h are recorded in scripts/data (UniXNU scripts/build-native-hid-input.py run on a prelinked candidate
by CI, artifact storage-audit-droidvm). Its static initialiser is removed and run by NativeVirtioInput once IOHIDFamily is
loaded, as the sheng HID probe does. Without the recorded files the archive carries stubs: NativeVirtioInput still polls
the virtio devices but publishes no HID device.

Linked with CONFIG_XNU_EXTRA_LD_ARCHIVES="-Wl,-force_load,{tree}/build/virtio-input/libVirtioInput.a".
"""
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'kits/NativePrelink'))
from native_prelink import macho  # noqa: E402

OUT = ROOT / 'build/virtio-input'
HID = ROOT / 'build/native-hid-input'
DATA = ROOT / 'scripts/data'
recorded_header = DATA / 'OriginalHidDeviceABI.h'
recorded_bindings = DATA / 'hid-original-bindings.S'


def run(command, **kwargs):
    return subprocess.run([str(part) for part in command], check=True, **kwargs)


def undefined_symbols(path):
    data = path.read_bytes()
    image = macho(data)
    offset = next(at for command, at, _ in image['commands'] if command == 2)
    symoff, count, stroff, _ = struct.unpack_from('<4I', data, offset + 8)
    names = []
    for i in range(count):
        index, kind, _, _, _ = struct.unpack_from('<IBBHQ', data, symoff + i * 16)
        if kind & 0x0e == 0 and kind & 1:
            names.append(data[stroff + index:data.index(b'\0', stroff + index)].decode())
    return names


OUT.mkdir(parents=True, exist_ok=True)
run(['python3', ROOT / 'scripts/build-arm-driver.py', 'NativeVirtioInput'])
sdk = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
clang = ['xcrun', '--sdk', 'iphoneos', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk, '-mkernel',
         '-ffreestanding', '-Wall', '-Werror', '-c']
members = [OUT / 'NativeVirtioInput.o', OUT / 'VirtioHidLink.o']
if recorded_header.is_file() and recorded_bindings.is_file():
    HID.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(recorded_header, HID / 'OriginalHidDeviceABI.h')
    run(['python3', ROOT / 'scripts/build-arm-driver.py', 'NativeHidInput'])
    bound = set(re.findall(r'^\.globl (\S+)$', recorded_bindings.read_text(), re.M))
    imports = {name for name in undefined_symbols(HID / 'NativeHidInput.o') if '11IOHIDDevice' in name}
    if not imports or not imports <= bound:
        raise SystemExit(f'NativeHidInput imports outside the recorded original bindings: {sorted(imports - bound)}')
    shutil.copyfile(recorded_bindings, OUT / 'hid-original-bindings.S')
    run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk, '-Werror', '-Wno-incompatible-sysroot', '-c',
         OUT / 'hid-original-bindings.S', '-o', OUT / 'hid-original-bindings.o'])
    llvm = Path(subprocess.check_output(['brew', '--prefix', 'llvm'], text=True).strip()) / 'bin'
    run([llvm / 'llvm-objcopy', '--remove-section=__DATA,__mod_init_func',
         '--globalize-symbol=__GLOBAL__sub_I_NativeHidInput.cpp', HID / 'NativeHidInput.o', OUT / 'NativeHidInput.late.o'])
    (OUT / 'VirtioHidLink.c').write_text('const unsigned int native_hid_input_available = 1;\n')
    members += [OUT / 'NativeHidInput.late.o', OUT / 'hid-original-bindings.o']
    mode = 'with NativeHidInput'
else:
    (OUT / 'VirtioHidLink.c').write_text(
        '// No recorded original IOHIDDevice bindings: NativeVirtioInput polls but publishes no HID device.\n'
        'const unsigned int native_hid_input_available = 0;\n'
        'void native_hid_static_init_stub(void) __asm__("__GLOBAL__sub_I_NativeHidInput.cpp");\n'
        'void native_hid_static_init_stub(void) {}\n'
        'void *native_hid_input_allocate(void);\n'
        'void *native_hid_input_allocate(void) { return 0; }\n'
        'void *native_hid_touch_allocate(void);\n'
        'void *native_hid_touch_allocate(void) { return 0; }\n')
    mode = 'without HID (no recorded bindings in scripts/data)'
run([*clang, OUT / 'VirtioHidLink.c', '-o', OUT / 'VirtioHidLink.o'])
archive = OUT / 'libVirtioInput.a'
archive.unlink(missing_ok=True)
run(['xcrun', 'ar', 'rcs', archive, *members])
print(f'{archive}: NativeVirtioInput {mode}')
