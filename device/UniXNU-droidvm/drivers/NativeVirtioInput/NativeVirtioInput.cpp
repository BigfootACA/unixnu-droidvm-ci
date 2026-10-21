// SPDX-License-Identifier: BSD-2-Clause-Patent
// VirtIO input (VirtIO 1.2 5.8) over virtio-pci for the virtual machine board (UniXNU-droidvm).
//
// DroidVM gives every screen with input a virtio multi-touch device, an absolute (tablet) mouse and a keyboard, and the VM
// a relative mouse (DroidVM-Daemon vm/crosvm_command.cpp, inputs()). A kernel thread started at C++ static initialisation
// waits for IOBSD, opens every virtio-input function of the ECAM bus (/arm-io/pcie), polls their event queues and turns the
// evdev events into the sequence-locked state that NativeHidInput publishes as HID reports:
//   multi-touch (ABS_MT slots) or tablet (ABS_X/ABS_Y + BTN_LEFT/BTN_TOUCH) -> native_touch_*  (touch-only digitizer)
//   keyboard (EV_KEY)                                                        -> native_key_*    (boot keyboard + consumer)
// The relative mouse is not used. It then loads IOHIDFamily and IOHIDEventDriver, runs the deferred static initialisation
// of NativeHidInput (its metaclass derives from the original IOHIDDevice, see scripts/build-virtio-input.py) and attaches
// the HID devices to IOHIDResource. Boot argument virtio-input=0 disables all of it.
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOService.h>
#include <libkern/OSKextLib.h>
#include <libkern/c++/OSKext.h>
#include <libkern/c++/OSMetaClass.h>
#include <libkern/c++/OSSymbol.h>
#include <mach/kmod.h>
#include <pexpert/pexpert.h>
#include <kern/thread.h>
#include "NativeVirtioPci.h"

extern kmod_info_t g_kernel_kmod_info;
// NativeHidInput, linked with its static initialiser removed (deferred until IOHIDFamily is loaded).
extern "C" void native_hid_static_init(void) __asm__("__GLOBAL__sub_I_NativeHidInput.cpp");
extern "C" IOService *native_hid_input_allocate(void);
extern "C" IOService *native_hid_touch_allocate(void);
extern "C" const uint32_t native_hid_input_available;

extern "C" {
// Touch state (NativeHidInput touch-only device). Coordinates are raw device units in 0..max-1.
volatile uint32_t native_touch_seq, native_touch_count, native_touch_hid_reports;
volatile int native_touch_xs[12], native_touch_ys[12], native_touch_ids[12];
volatile uint32_t native_touch_x_max, native_touch_y_max;
// Keyboard state: boot keyboard report (modifiers, reserved, six usages) and consumer bits (Menu, Vol+, Vol-, Power).
volatile uint32_t native_key_seq, native_key_hid_reports;
volatile uint8_t native_key_report[8];
volatile uint8_t native_key_consumer;
// Observation: [0] stage, [1] devices opened, [2] touch devices, [3] keyboards, [4] events, [5] HID devices started,
// [6] last failure (1 no ECAM, 2 IOHIDFamily load, 3 no IOHIDResource, 4 IOHIDDevice size, 5 metaclass registration)
volatile uint32_t native_virtio_input_stats[8];
}

namespace {

// virtio_input_config (VirtIO 1.2 5.8.5)
enum : unsigned { cfgSelect = 0, cfgSubsel = 1, cfgSize = 2, cfgData = 8 };
enum : uint8_t { cfgIdName = 0x01, cfgEvBits = 0x11, cfgAbsInfo = 0x12 };
// Linux input event codes (include/uapi/linux/input-event-codes.h)
enum : uint16_t { evSyn = 0, evKey = 1, evAbs = 3, synReport = 0, synDropped = 3 };
enum : uint16_t {
  absX = 0x00, absY = 0x01, absMtSlot = 0x2f, absMtPositionX = 0x35, absMtPositionY = 0x36, absMtTrackingId = 0x39,
  keyA = 30, keyVolumeDown = 114, keyVolumeUp = 115, keyPower = 116, keyHomepage = 172, btnLeft = 0x110, btnTouch = 0x14a,
};
constexpr unsigned maxDevices = 8, maxSlots = 10, maxQueue = 64;

// Linux key code -> HID keyboard usage (page 7), 0..127; 0 = not mapped. Modifiers and consumer keys are handled apart.
const uint8_t keyUsage[128] = {
  0x00, 0x29, 0x1e, 0x1f, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x2d, 0x2e, 0x2a, 0x2b,
  0x14, 0x1a, 0x08, 0x15, 0x17, 0x1c, 0x18, 0x0c, 0x12, 0x13, 0x2f, 0x30, 0x28, 0x00, 0x04, 0x16,
  0x07, 0x09, 0x0a, 0x0b, 0x0d, 0x0e, 0x0f, 0x33, 0x34, 0x35, 0x00, 0x31, 0x1d, 0x1b, 0x06, 0x19,
  0x05, 0x11, 0x10, 0x36, 0x37, 0x38, 0x00, 0x55, 0x00, 0x2c, 0x39, 0x3a, 0x3b, 0x3c, 0x3d, 0x3e,
  0x3f, 0x40, 0x41, 0x42, 0x43, 0x53, 0x47, 0x5f, 0x60, 0x61, 0x56, 0x5c, 0x5d, 0x5e, 0x57, 0x59,
  0x5a, 0x5b, 0x62, 0x63, 0x00, 0x00, 0x64, 0x44, 0x45, 0x87, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
  0x58, 0x00, 0x54, 0x46, 0x00, 0x00, 0x4a, 0x52, 0x4b, 0x50, 0x4f, 0x4d, 0x51, 0x4e, 0x49, 0x4c,
  0x00, 0x7f, 0x00, 0x00, 0x00, 0x67, 0x00, 0x48, 0x00, 0x85, 0x90, 0x91, 0x89, 0x00, 0x00, 0x65,
};

// Modifier bit of a Linux key code (HID 0xe0..0xe7 order), or -1.
int modifierBit(uint16_t code)
{
  switch (code) {
  case 29: return 0;   // KEY_LEFTCTRL
  case 42: return 1;   // KEY_LEFTSHIFT
  case 56: return 2;   // KEY_LEFTALT
  case 125: return 3;  // KEY_LEFTMETA
  case 97: return 4;   // KEY_RIGHTCTRL
  case 54: return 5;   // KEY_RIGHTSHIFT
  case 100: return 6;  // KEY_RIGHTALT
  case 126: return 7;  // KEY_RIGHTMETA
  default: return -1;
  }
}

// Consumer bit (NativeHidInput report 2) of a Linux key code, or -1.
int consumerBit(uint16_t code)
{
  switch (code) {
  case keyHomepage: return 0;
  case keyVolumeUp: return 1;
  case keyVolumeDown: return 2;
  case keyPower: return 3;
  default: return -1;
  }
}

struct Event { uint16_t type; uint16_t code; uint32_t value; };
struct Descriptor { uint64_t address; uint32_t length; uint16_t flags; uint16_t next; };
struct Used { uint32_t id; uint32_t length; };
static_assert(sizeof(Event) == 8 && sizeof(Descriptor) == 16 && sizeof(Used) == 8, "VirtIO wire layout");
inline void dmaBarrier() { __asm__ volatile("dmb osh" ::: "memory"); }

enum class Kind { none, touch, tablet, keyboard };

struct Slot { int id = -1; int x = 0; int y = 0; };

struct Device {
  virtio_pci::Transport transport;
  IOBufferMemoryDescriptor *memory = nullptr;
  unsigned char *base = nullptr;
  uint16_t size = 0, availableIndex = 0, usedIndex = 0;
  Kind kind = Kind::none;
  uint32_t xMax = 0, yMax = 0;
  // Touch and tablet state, published on SYN_REPORT.
  Slot slots[maxSlots];
  unsigned slot = 0;
  bool dirty = false;
  // Layout of the 8 KiB queue memory: descriptors, available ring, used ring, event buffers.
  Descriptor *descriptors() { return reinterpret_cast<Descriptor *>(base); }
  volatile uint16_t *available() { return reinterpret_cast<volatile uint16_t *>(base + 1024); }
  volatile uint16_t *used() { return reinterpret_cast<volatile uint16_t *>(base + 2048); }
  volatile Used *usedElements() { return reinterpret_cast<volatile Used *>(base + 2052); }
  Event *events() { return reinterpret_cast<Event *>(base + 4096); }
};

Device devices[maxDevices];
unsigned deviceCount;
uint8_t keyState[8];
uint8_t consumerState;

uint8_t configBytes(Device &device, uint8_t select, uint8_t subsel, uint8_t *out, unsigned capacity)
{
  device.transport.setConfig<uint8_t>(cfgSelect, select);
  device.transport.setConfig<uint8_t>(cfgSubsel, subsel);
  uint8_t size = device.transport.config<uint8_t>(cfgSize);
  if (size > capacity) size = static_cast<uint8_t>(capacity);
  for (unsigned i = 0; i < size; ++i) out[i] = device.transport.config<uint8_t>(cfgData + i);
  return size;
}

bool hasBit(Device &device, uint8_t type, unsigned bit)
{
  uint8_t bits[128] = {};
  uint8_t size = configBytes(device, cfgEvBits, type, bits, sizeof(bits));
  return bit / 8 < size && ((bits[bit / 8] >> (bit % 8)) & 1);
}

// The maximum of an absolute axis (struct virtio_input_absinfo: min, max, fuzz, flat, res), as an exclusive bound.
uint32_t axisRange(Device &device, uint8_t axis)
{
  uint8_t info[20] = {};
  if (configBytes(device, cfgAbsInfo, axis, info, sizeof(info)) < 8) return 0;
  uint32_t maximum = static_cast<uint32_t>(info[4]) | (static_cast<uint32_t>(info[5]) << 8) |
                     (static_cast<uint32_t>(info[6]) << 16) | (static_cast<uint32_t>(info[7]) << 24);
  return maximum < 0x7fffffff ? maximum + 1 : 0;
}

bool configure(Device &device)
{
  if (!device.transport.hasDeviceConfig() || !device.transport.negotiate(0)) return false;
  if (hasBit(device, evAbs, absMtPositionX)) {
    device.kind = Kind::touch;
    device.xMax = axisRange(device, absMtPositionX);
    device.yMax = axisRange(device, absMtPositionY);
  } else if (hasBit(device, evAbs, absX)) {
    device.kind = Kind::tablet;
    device.xMax = axisRange(device, absX);
    device.yMax = axisRange(device, absY);
  } else if (hasBit(device, evKey, keyA)) {
    device.kind = Kind::keyboard;
  } else {
    device.transport.reset();
    return false;   // relative mouse or anything else: leave it reset
  }
  uint16_t maximum = device.transport.queueMaximum(0);
  device.size = maximum < maxQueue ? maximum : maxQueue;
  if (device.size < 2) return false;
  device.memory = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task,
    static_cast<IOOptionBits>(kIODirectionInOut) | static_cast<IOOptionBits>(kIOMemoryPhysicallyContiguous), 8192, 4096);
  if (!device.memory) return false;
  IOByteCount length = 0;
  uint64_t physical = device.memory->getPhysicalSegment(0, &length);
  if (!physical || length < 8192) return false;
  device.base = static_cast<unsigned char *>(device.memory->getBytesNoCopy());
  bzero(device.base, 8192);
  for (uint16_t i = 0; i < device.size; ++i) {
    device.descriptors()[i] = {physical + 4096 + i * sizeof(Event), sizeof(Event), 2, 0};   // device-writable
    device.available()[2 + i] = i;
  }
  device.available()[0] = 1;   // VIRTQ_AVAIL_F_NO_INTERRUPT: polled
  device.availableIndex = device.size;
  device.available()[1] = device.availableIndex;
  dmaBarrier();
  if (!device.transport.enableQueue(0, device.size, physical, physical + 1024, physical + 2048)) return false;
  device.transport.setStatus(virtio_pci::statusAcknowledge | virtio_pci::statusDriver | virtio_pci::statusFeaturesOk |
                             virtio_pci::statusDriverOk);
  device.transport.notify(0);
  return (device.transport.status() & 15) == 15;
}

void publishTouch(Device &device)
{
  native_touch_seq = native_touch_seq + 1;   // odd: being written
  __asm__ volatile("dmb ish" ::: "memory");
  unsigned count = 0;
  for (unsigned i = 0; i < maxSlots; ++i) {
    if (device.slots[i].id < 0) continue;
    native_touch_ids[count] = device.slots[i].id;
    native_touch_xs[count] = device.slots[i].x;
    native_touch_ys[count] = device.slots[i].y;
    ++count;
  }
  native_touch_count = count;
  native_touch_x_max = device.xMax;
  native_touch_y_max = device.yMax;
  __asm__ volatile("dmb ish" ::: "memory");
  native_touch_seq = native_touch_seq + 1;
}

void publishKeys()
{
  native_key_seq = native_key_seq + 1;
  __asm__ volatile("dmb ish" ::: "memory");
  for (unsigned i = 0; i < 8; ++i) native_key_report[i] = keyState[i];
  native_key_consumer = consumerState;
  __asm__ volatile("dmb ish" ::: "memory");
  native_key_seq = native_key_seq + 1;
}

void keyEvent(uint16_t code, uint32_t value)
{
  if (value == 2) return;   // autorepeat: HID repeats from the held state
  bool down = value != 0;
  if (int bit = modifierBit(code); bit >= 0) {
    keyState[0] = static_cast<uint8_t>(down ? keyState[0] | (1U << bit) : keyState[0] & ~(1U << bit));
    return;
  }
  if (int bit = consumerBit(code); bit >= 0) {
    consumerState = static_cast<uint8_t>(down ? consumerState | (1U << bit) : consumerState & ~(1U << bit));
    return;
  }
  uint8_t usage = code < 128 ? keyUsage[code] : 0;
  if (!usage) return;
  unsigned at = 2;
  while (at < 8 && keyState[at] != usage) ++at;
  if (down && at == 8) {
    for (at = 2; at < 8 && keyState[at]; ++at) {}
    if (at < 8) keyState[at] = usage;
  } else if (!down && at < 8) {
    for (; at < 7; ++at) keyState[at] = keyState[at + 1];
    keyState[7] = 0;
  }
}

void handle(Device &device, const Event &event)
{
  native_virtio_input_stats[4] = native_virtio_input_stats[4] + 1;
  if (device.kind == Kind::keyboard) {
    if (event.type == evKey) keyEvent(event.code, event.value);
    else if (event.type == evSyn && event.code == synReport) publishKeys();
    return;
  }
  Slot &current = device.slots[device.kind == Kind::touch ? device.slot : 0];
  if (event.type == evAbs) {
    int value = static_cast<int>(event.value);
    switch (event.code) {
    case absMtSlot: if (device.kind == Kind::touch) device.slot = event.value < maxSlots ? event.value : maxSlots - 1; break;
    case absMtTrackingId: if (device.kind == Kind::touch) { current.id = value < 0 ? -1 : (value & 0xff); device.dirty = true; } break;
    case absMtPositionX: if (device.kind == Kind::touch) { current.x = value; device.dirty = true; } break;
    case absMtPositionY: if (device.kind == Kind::touch) { current.y = value; device.dirty = true; } break;
    case absX: if (device.kind == Kind::tablet) { current.x = value; device.dirty = true; } break;
    case absY: if (device.kind == Kind::tablet) { current.y = value; device.dirty = true; } break;
    default: break;
    }
  } else if (event.type == evKey && device.kind == Kind::tablet && (event.code == btnLeft || event.code == btnTouch)) {
    current.id = event.value ? 0 : -1;
    device.dirty = true;
  } else if (event.type == evSyn && event.code == synReport && device.dirty) {
    publishTouch(device);
    device.dirty = false;
  } else if (event.type == evSyn && event.code == synDropped) {
    for (auto &slot : device.slots) slot.id = -1;   // resynchronise: every contact is lifted
    device.dirty = true;
  }
}

void poll(Device &device)
{
  bool returned = false;
  dmaBarrier();
  while (device.used()[1] != device.usedIndex) {
    dmaBarrier();
    uint32_t id = device.usedElements()[device.usedIndex % device.size].id;
    ++device.usedIndex;
    if (id >= device.size) continue;
    Event event = device.events()[id];
    handle(device, event);
    device.available()[2 + device.availableIndex % device.size] = static_cast<uint16_t>(id);
    ++device.availableIndex;
    returned = true;
  }
  if (returned) {
    dmaBarrier();
    device.available()[1] = device.availableIndex;
    dmaBarrier();
    device.transport.notify(0);
  }
  device.transport.acknowledgeInterrupt();
}

IOService *waitFor(OSDictionary *matching, uint64_t seconds)
{
  if (!matching) return nullptr;
  IOService *service = IOService::waitForMatchingService(matching, seconds * 1000000000ULL);
  matching->release();
  return service;
}

IOService *startHidDevice(IOService *(*allocate)(void), IOService *resource)
{
  IOService *device = allocate();
  if (!device || !device->metaCast("IOHIDDevice") || !device->init()) { OSSafeReleaseNULL(device); return nullptr; }
  if (!device->attach(resource)) { device->release(); return nullptr; }
  if (!device->start(resource)) { device->detach(resource); device->release(); return nullptr; }
  native_virtio_input_stats[5] = native_virtio_input_stats[5] + 1;
  return device;   // lives for the boot
}

void startHid(bool keyboard, bool touch)
{
  if (!native_hid_input_available) return;
  if (OSKextLoadKextWithIdentifier("com.apple.iokit.IOHIDFamily")) { native_virtio_input_stats[6] = 2; return; }
  OSKext::loadKextWithIdentifier("com.apple.iokit.IOHIDEventDriver", true, false, kOSKextExcludeNone,
                                 kOSKextExcludeNone, nullptr);
  IOService *resource = waitFor(IOService::serviceMatching("IOHIDResource"), 10);
  if (!resource) { native_virtio_input_stats[6] = 3; return; }
  // NativeHidInput is laid out for the original 208-byte IOHIDDevice (OriginalHidDeviceABI.h).
  const OSSymbol *name = OSSymbol::withCString("IOHIDDevice");
  const OSMetaClass *meta = name ? OSMetaClass::getMetaClassWithName(name) : nullptr;
  OSSafeReleaseNULL(name);
  if (!meta || meta->getClassSize() != 208) { native_virtio_input_stats[6] = 4; resource->release(); return; }
  void *handle = OSMetaClass::preModLoad(g_kernel_kmod_info.name);
  if (!handle) { native_virtio_input_stats[6] = 5; resource->release(); return; }
  native_hid_static_init();
  if (OSMetaClass::postModLoad(handle)) { native_virtio_input_stats[6] = 5; resource->release(); return; }
  if (keyboard) startHidDevice(native_hid_input_allocate, resource);
  if (touch) startHidDevice(native_hid_touch_allocate, resource);
  resource->release();
}

void inputThread(void *, wait_result_t)
{
  native_virtio_input_stats[0] = 1;
  // At IOBSD the prelinked families can be loaded through OSKext.
  IOService *bsd = waitFor(IOService::resourceMatching("IOBSD"), 600);
  OSSafeReleaseNULL(bsd);
  native_virtio_input_stats[0] = 2;
  IOService *bridge = waitFor(IOService::nameMatching("pcie"), 30);
  IOMemoryMap *ecam = bridge ? bridge->mapDeviceMemoryWithIndex(0, kIOMapAnywhere | kIOMapInhibitCache) : nullptr;
  OSSafeReleaseNULL(bridge);
  if (!ecam) {
    native_virtio_input_stats[6] = 1;
  } else {
    bool keyboard = false, touch = false;
    for (unsigned index = 0; index < maxDevices && deviceCount < maxDevices; ++index) {
      Device &device = devices[deviceCount];
      if (!device.transport.open(static_cast<uintptr_t>(ecam->getVirtualAddress()), ecam->getLength(),
                                 virtio_pci::deviceInput, index)) break;
      if (!configure(device)) {
        device.transport.reset();
        device.transport.close();
        OSSafeReleaseNULL(device.memory);
        device = Device();
        continue;
      }
      keyboard |= device.kind == Kind::keyboard;
      touch |= device.kind == Kind::touch || device.kind == Kind::tablet;
      ++deviceCount;
    }
    native_virtio_input_stats[1] = deviceCount;
    for (unsigned i = 0; i < deviceCount; ++i) {
      if (devices[i].kind == Kind::keyboard) native_virtio_input_stats[3] = native_virtio_input_stats[3] + 1;
      else native_virtio_input_stats[2] = native_virtio_input_stats[2] + 1;
    }
    native_virtio_input_stats[0] = 3;
    if (deviceCount) startHid(keyboard, touch);
    native_virtio_input_stats[0] = 4;
  }
  // Poll every 4 ms; the queues suppress notifications. The thread lives for the boot.
  for (;;) {
    for (unsigned i = 0; i < deviceCount; ++i) poll(devices[i]);
    IOSleep(deviceCount ? 4 : 60000);
  }
}

struct Starter {
  Starter() {
    unsigned enabled = 1;
    PE_parse_boot_argn("virtio-input", &enabled, sizeof(enabled));
    if (!enabled) return;
    thread_t thread;
    if (kernel_thread_start(inputThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
  }
};
Starter starter;

}  // namespace
