"""Shared by build-virt-display.py and record-virt-display.py (UniXNU-droidvm).

The generated original-class header and the audit of the compiled NativeLegacyDisplay vtable, exactly as UniXNU-sheng
scripts/build-native-legacy-display.py does them.
"""
import hashlib
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'kits/NativePrelink'))
from native_prelink import macho  # noqa: E402

OUT = ROOT / 'build/native-legacy-display'
DATA = ROOT / 'scripts/data'
RECORDED_HEADER = DATA / 'OriginalLegacyFramebufferABI.h'
RECORDED_BINDINGS = DATA / 'legacy-original-bindings.S'
RECORDED_AUDIT = DATA / 'mobile-graphics-legacy-abi.json'
VIRTUAL_ENTRIES = 358
# Slots NativeLegacyDisplay itself implements, besides the original abstract leaves.
OWNED_SLOTS = (0, 1, 7, 14, 84, 86, 87, 141, 155, 184, 188, 288, 290, 296)
# The four protected/metadata imports of every subclass, besides the vtable entries.
FIXED_BINDINGS = {'__ZN25IOMobileFramebufferLegacyC2EPK11OSMetaClass': 0xfffffff0057865cc,
                  '__ZN25IOMobileFramebufferLegacyD2Ev': 0xfffffff005786618,
                  '__ZN25IOMobileFramebufferLegacy10gMetaClassE': 0xfffffff006b35940}
SURFACE_PREFIX = '_native_legacy_surface_'
# The original IOSurface getters bound by address; other _native_legacy_surface_* names (e.g. the rejection observer)
# are ordinary probe hooks.
SURFACE_GETTERS = frozenset(SURFACE_PREFIX + name for name in
                            ('descriptor', 'width', 'height', 'stride', 'allocation', 'format', 'planes'))


def require(value, why):
    if not value:
        raise SystemExit(why)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(command, **kwargs):
    return subprocess.check_output([str(part) for part in command], timeout=300, **kwargs)


def header(pure):
    """OriginalLegacyFramebufferABI.h for the audited abstract slots (byte-identical to the sheng generator)."""
    text = '''// SPDX-License-Identifier: BSD-2-Clause-Patent
// Opaque ABI storage for the exact original class, not source-level access
// to its private fields. The actual original metadata supplies its full
// Framebuffer/Service/IOService ancestry. Only qualified base prototypes may
// be called from source; opaque entries are original dispatch targets only.
#pragma once
#include <IOKit/IOService.h>
#include <libkern/libkern.h>
struct OriginalLegacySwapRequest;
class IOMobileFramebufferLegacy : public IOService {
 OSDeclareAbstractStructors(IOMobileFramebufferLegacy)
 unsigned char originalPrivate[0x18a0-sizeof(IOService)];
public:
 // Both fields are qualified by the complete original slot-249 getter.
 // This modifies this object's own mode state, never a vtable or executable.
 void installBootDimensions(uint32_t width,uint32_t height) {
  *reinterpret_cast<uint32_t *>(reinterpret_cast<unsigned char *>(this)+5808)=width;
  *reinterpret_cast<uint32_t *>(reinterpret_cast<unsigned char *>(this)+5812)=height;
 }
 const OriginalLegacySwapRequest *pendingSwapRequest() const {
  const OriginalLegacySwapRequest *request=nullptr;
  memcpy(&request,reinterpret_cast<const unsigned char *>(this)+5480,sizeof(request));return request;
 }
 // The real normal scanout backend removes its pending head before calling
 // slot 295. Slot 312 is a discard path (terminal state 8), not presentation.
 // The queue is a tail queue (head at +5480, tail pointer at +5488, each request's next at +2960 and previous-link at +2968): taking the head
 // moves the next request, if a client already queued one behind it (int-132: swap 3 arrived during swap 2's GPU copy and the old
 // single-entry form refused to retire swap 2, so every wait on it stayed busy), up to the head. The original queue's reference
 // moves to the current-swap slot for the original completion body.
 bool activateOnlyPendingSwap(const OriginalLegacySwapRequest *request) {
  auto self=reinterpret_cast<unsigned char *>(this);
  auto bytes=reinterpret_cast<const unsigned char *>(request);
  const void *current=nullptr,*head=nullptr,*tail=nullptr,*next=nullptr,*previous=nullptr;
  memcpy(&current,self+5472,8);memcpy(&head,self+5480,8);memcpy(&tail,self+5488,8);
  if(!request || current || head!=request)return false;
  memcpy(&next,bytes+2960,8);memcpy(&previous,bytes+2968,8);
  if(previous!=self+5480)return false;
  if(next) {
   const void *link=self+5480;
   memcpy(const_cast<unsigned char *>(reinterpret_cast<const unsigned char *>(next))+2968,&link,8);
  } else if(tail!=bytes+2960)return false;
  memcpy(self+5472,&request,8);
  if(next) {memcpy(self+5480,&next,8);}
  else {const void *empty=nullptr,*emptyTail=self+5480;memcpy(self+5480,&empty,8);memcpy(self+5488,&emptyTail,8);}
  return true;
 }
 void free() override;
 bool init(OSDictionary *properties=nullptr) override;
 IOReturn setProperties(OSObject *) override;
 void systemWillShutdown(IOOptionBits) override;
 IOService *probe(IOService *,SInt32 *) override;
 bool start(IOService *) override;
 void stop(IOService *) override;
 IOWorkLoop *getWorkLoop() const override;
 IOReturn newUserClient(task_t,void *,UInt32,IOUserClient **) override;
 IOReturn setPowerState(unsigned long,IOService *) override;
'''
    for i in range(168, 358):
        if i == 184:
            text += ' virtual bool isMainDisplay();\n'
        elif i == 188:
            text += ' virtual IOReturn getDotPitch(uint32_t *);\n'
        elif i == 213:
            text += ' virtual IOReturn supportedFrameInfo(uint32_t,uint32_t *,char *,uint32_t *)=0;\n'
        elif i == 249:
            text += ' virtual void getDisplayDimensions(uint32_t *,uint32_t *);\n'
        elif i == 290:
            text += ' virtual void kickSwapQueue();\n'
        elif i == 296:
            text += ' virtual bool validateSwap(const OriginalLegacySwapRequest *);\n'
        elif i == 295:
            text += ' virtual void completeCurrentSwap();\n'
        elif i == 337:
            text += ' virtual IOReturn collectCompletionReports(uint32_t,void *,uint32_t *,uint32_t)=0;\n'
        elif i == 288:
            text += ' virtual bool startHardware();\n'
        elif i in pure:
            text += f' virtual __attribute__((noreturn)) void opaque{i}()=0;\n'
        else:
            text += f' virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque{i}();\n'
    text += '};\nstatic_assert(sizeof(IOMobileFramebufferLegacy)==0x18a0,"Actual original instance extent");\n'
    return text


def object_symbols(raw, image):
    """(name, value, n_type) of every symbol of a relocatable object."""
    st = next(p for c, p, _ in image['commands'] if c == 2)
    sy, count, strings, _ = struct.unpack_from('<4I', raw, st + 8)
    symbols = []
    for i in range(count):
        ix, kind, _, _, va = struct.unpack_from('<IBBHQ', raw, sy + i * 16)
        symbols.append((raw[strings + ix:raw.index(b'\0', strings + ix)].decode(), va, kind))
    return symbols


def undefined(symbols):
    return {name for name, _, kind in symbols if kind == 1}


def defined(symbols):
    return {name for name, _, kind in symbols if kind & 1 and kind & 0x0e == 0x0e}


def compiled_object(path):
    """The compiled NativeLegacyDisplay object: raw bytes, Mach-O image, symbols and the 358 vtable entry names."""
    raw = Path(path).read_bytes()
    image = macho(raw)
    symbols = object_symbols(raw, image)
    vtable = next(va for name, va, _ in symbols if name == '__ZTV19NativeLegacyDisplay')
    relocations = {}
    for segment in image['segments']:
        for sec in segment['sections']:
            if sec['va'] <= vtable < sec['va'] + sec['size']:
                ro, rc = struct.unpack_from('<2I', raw, sec['command_offset'] + 56)
                for i in range(rc):
                    offset, bits = struct.unpack_from('<2I', raw, ro + i * 8)
                    if bits >> 27 & 1 and bits >> 28 == 0 and bits >> 25 & 3 == 3:
                        relocations[sec['va'] + offset] = symbols[bits & 0xffffff][0]
    compiled = [relocations.get(vtable + 16 + i * 8) for i in range(VIRTUAL_ENTRIES)]
    require(all(compiled) and vtable + 16 + VIRTUAL_ENTRIES * 8 not in relocations, 'Exact owned 358-entry vtable')
    return raw, image, symbols, compiled


def classify(compiled, audit):
    """Split the compiled vtable into owned slots, original Legacy bindings and inherited native base entries."""
    pure = audit['abstract_slots']
    methods = audit['methods']
    require(len(methods) == VIRTUAL_ENTRIES and all(m['slot'] == i for i, m in enumerate(methods)), 'Audited 358-slot method table')
    owned, native = [], []
    bindings = dict(FIXED_BINDINGS)
    bindings['__ZTV25IOMobileFramebufferLegacy'] = int(audit['legacy_vptr'], 16) - 16
    for i, name in enumerate(compiled):
        expected = int(methods[i]['address'], 16)
        if name.startswith(('__ZN19NativeLegacyDisplay', '__ZNK19NativeLegacyDisplay')):
            owned.append(i)
            require(i in OWNED_SLOTS or i in pure, 'Only qualified own methods and guarded leaves override')
        elif '25IOMobileFramebufferLegacy' in name:
            require(i not in pure, 'No unresolved original abstract backend target')
            bindings[name] = expected
        else:
            native.append((i, name, expected))
    require(owned == sorted(list(OWNED_SLOTS) + list(pure)), 'Exact owned method set')
    return owned, bindings, native


def late_object(obj, late):
    """NativeLegacyDisplay.late.o: the static initialiser is no longer registered, but global, for the deferred probe."""
    llvm = Path(run(['brew', '--prefix', 'llvm'], text=True).strip()) / 'bin'
    Path(late).unlink(missing_ok=True)
    run([llvm / 'llvm-objcopy', '--remove-section=__DATA,__mod_init_func',
         '--globalize-symbol=__GLOBAL__sub_I_NativeLegacyDisplay.cpp', obj, late])

    def code(data):
        info = macho(data)
        sec = next(s for g in info['segments'] for s in g['sections'] if s['name'] == '__text')
        return data[sec['fileoff']:sec['fileoff'] + sec['size']]
    require(code(Path(obj).read_bytes()) == code(Path(late).read_bytes()), 'Only owned initializer section registration changes')
