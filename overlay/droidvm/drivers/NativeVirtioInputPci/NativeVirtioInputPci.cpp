// SPDX-License-Identifier: BSD-2-Clause-Patent
// virtio-input over virtio-pci for the DroidVM guest (stock crosvm; UniXNU-droidvm).
//
// The mainline board (kits/droidvm/DroidVMVirtioInput.c) drives virtio-input on the virtio-mmio transport of the crosvm
// branch wip/virtio-mmio. The crosvm of the DroidVM app has virtio-pci only: there the app's touch screen and the input
// devices of crosvm's VNC server (--vnc-server: an absolute pointer and a keyboard of its own) are virtio-input functions
// on bus 0 of the ECAM bridge. This driver finds them through /arm-io/pcie (NativeVirtioPci.h) and feeds the same
// consumers as the mainline driver, with the same semantics (its touch_frame/touch_publish are carried over):
//   - a direct multi-touch screen, and an absolute pointer as one finger while its button is down, become the finger set
//     NativeHidInput's digitizer reads every 8 ms (native_touch_*, a seqlock: odd while written) on 30480 x 20320 axes;
//   - Home, HomePage or F1 on a keyboard presses the Home button (HidInputProbe.cpp mailbox native_hid_input_command).
// A kernel thread started at C++ static initialisation polls every 4 ms. virtio-input-pci=0 leaves the devices alone.
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOService.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
#include "NativeVirtioPci.h"

extern "C" {
extern volatile uint32_t native_touch_seq, native_touch_count, native_touch_hid_reports;
extern volatile int native_touch_xs[12], native_touch_ys[12], native_touch_ids[12];
extern volatile uint32_t native_hid_input_command;
// Observation: [0] stage, [1] functions found, [2] devices started, [3] touch, [4] pointer, [5] keyboard, [6] events,
// [7] Home presses
volatile uint32_t native_virtio_input_pci_stats[8];
}

namespace {

enum : uint16_t { evSyn = 0, evKey = 1, evAbs = 3, synReport = 0 };
enum : uint16_t {
  absX = 0x00, absY = 0x01, absMtSlot = 0x2f, absMtPositionX = 0x35, absMtPositionY = 0x36, absMtTrackingId = 0x39,
  keyEsc = 1, keyA = 30, keyF1 = 59, keyHome = 102, keyHomepage = 172, btnLeft = 0x110, btnTouch = 0x14a, propDirect = 1,
};
enum : uint8_t { cfgSelect = 0, cfgSubsel = 1, cfgSize = 2, cfgData = 8 };
enum : uint8_t { cfgIdName = 0x01, cfgPropBits = 0x10, cfgEvBits = 0x11, cfgAbsInfo = 0x12 };
enum class Kind : uint8_t { none, touch, pointer, keyboard };

constexpr unsigned maxDevices = 8, maxSlots = 10, maxQueue = 64, fingers = 10, pollMs = 4;
constexpr int32_t axisX = 30480, axisY = 20320;
// A finger that comes down within this share (percent) of the screen's bottom starts at the bottom edge, where iPadOS
// takes a swipe as the Home gesture (mainline DroidVMVirtioInput.c, DROIDVM_TOUCH_BOTTOM_SNAP).
constexpr int32_t bottomSnap = 4;

struct Event { uint16_t type, code; int32_t value; };
struct Descriptor { uint64_t address; uint32_t length; uint16_t flags, next; };
static_assert(sizeof(Event) == 8 && sizeof(Descriptor) == 16, "VirtIO wire layout");
inline void dmaBarrier() { __asm__ volatile("dmb osh" ::: "memory"); }

struct Device {
  virtio_pci::Transport transport;
  IOBufferMemoryDescriptor *memory = nullptr;   // event queue (page 0) and its buffers (page 1)
  IOBufferMemoryDescriptor *status = nullptr;   // status queue, never used: crosvm enables a device only with both
  unsigned char *base = nullptr;
  uint16_t size = 0, used = 0;
  Kind kind = Kind::none;
  int32_t minX = 0, spanX = 1, minY = 0, spanY = 1;
  unsigned slot = 0;
  struct { int32_t id = -1, x = 0, y = 0; } slots[maxSlots];
  int32_t pointerX = 0, pointerY = 0;
  bool pointerDown = false;
  Descriptor *descriptors() { return reinterpret_cast<Descriptor *>(base); }
  volatile uint16_t *available() { return reinterpret_cast<volatile uint16_t *>(base + 1024); }
  volatile uint16_t *usedRing() { return reinterpret_cast<volatile uint16_t *>(base + 2048); }
  const volatile uint32_t *usedElements() { return reinterpret_cast<const volatile uint32_t *>(base + 2052); }
  Event *events() { return reinterpret_cast<Event *>(base + 4096); }
};

Device devices[maxDevices];
unsigned deviceCount;

unsigned configBytes(Device &d, uint8_t select, uint8_t subsel, uint8_t *out, unsigned capacity)
{
  d.transport.setConfig<uint8_t>(cfgSelect, select);
  d.transport.setConfig<uint8_t>(cfgSubsel, subsel);
  unsigned size = d.transport.config<uint8_t>(cfgSize);
  if (size > capacity) size = capacity;
  for (unsigned i = 0; i < size; ++i) out[i] = d.transport.config<uint8_t>(cfgData + i);
  return size;
}

bool bit(const uint8_t *bits, unsigned size, unsigned index) { return index / 8 < size && (bits[index / 8] >> (index % 8) & 1); }

bool axis(Device &d, uint8_t code, int32_t *minimum, int32_t *span)
{
  uint8_t info[20] = {};
  if (configBytes(d, cfgAbsInfo, code, info, sizeof(info)) < 8) return false;
  int32_t low, high;
  memcpy(&low, info, 4);
  memcpy(&high, info + 4, 4);
  if (high <= low) return false;
  *minimum = low;
  *span = high - low + 1;
  return true;
}

// A direct multi-touch screen, an absolute pointer with a button, a keyboard, or none of these.
Kind identify(Device &d)
{
  uint8_t props[16] = {}, abs[16] = {}, keys[64] = {};
  unsigned nprops = configBytes(d, cfgPropBits, 0, props, sizeof(props));
  unsigned nabs = configBytes(d, cfgEvBits, evAbs, abs, sizeof(abs));
  unsigned nkeys = configBytes(d, cfgEvBits, evKey, keys, sizeof(keys));
  if (bit(abs, nabs, absMtPositionX) && bit(abs, nabs, absMtPositionY) && bit(props, nprops, propDirect) &&
      axis(d, absMtPositionX, &d.minX, &d.spanX) && axis(d, absMtPositionY, &d.minY, &d.spanY)) return Kind::touch;
  if (!bit(abs, nabs, absMtPositionX) && bit(abs, nabs, absX) && bit(abs, nabs, absY) &&
      (bit(keys, nkeys, btnLeft) || bit(keys, nkeys, btnTouch)) &&
      axis(d, absX, &d.minX, &d.spanX) && axis(d, absY, &d.minY, &d.spanY)) return Kind::pointer;
  if (bit(keys, nkeys, keyA) && bit(keys, nkeys, keyHome)) return Kind::keyboard;
  return Kind::none;
}

IOBufferMemoryDescriptor *contiguous(unsigned bytes, uint64_t *physical)
{
  IOBufferMemoryDescriptor *memory = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task,
    static_cast<IOOptionBits>(kIODirectionInOut) | static_cast<IOOptionBits>(kIOMemoryPhysicallyContiguous), bytes, 4096);
  IOByteCount length = 0;
  *physical = memory ? memory->getPhysicalSegment(0, &length) : 0;
  if (!*physical || length < bytes) { OSSafeReleaseNULL(memory); return nullptr; }
  bzero(memory->getBytesNoCopy(), bytes);
  return memory;
}

bool configure(Device &d)
{
  if (!d.transport.hasDeviceConfig() || !d.transport.negotiate(0)) return false;
  d.kind = identify(d);
  if (d.kind == Kind::none) return false;
  uint16_t maximum = d.transport.queueMaximum(0), statusMaximum = d.transport.queueMaximum(1);
  d.size = maximum < maxQueue ? maximum : static_cast<uint16_t>(maxQueue);
  uint16_t statusSize = statusMaximum < 8 ? statusMaximum : 8;
  if (d.size < 2 || !statusSize) return false;
  uint64_t physical = 0, statusPhysical = 0;
  d.memory = contiguous(8192, &physical);
  d.status = contiguous(4096, &statusPhysical);
  if (!d.memory || !d.status) return false;
  d.base = static_cast<unsigned char *>(d.memory->getBytesNoCopy());
  for (uint16_t i = 0; i < d.size; ++i) {
    d.descriptors()[i] = {physical + 4096 + i * sizeof(Event), sizeof(Event), 2, 0};   // device-writable
    d.available()[2 + i] = i;
  }
  d.available()[0] = 1;   // VIRTQ_AVAIL_F_NO_INTERRUPT: polled
  d.available()[1] = d.size;
  reinterpret_cast<volatile uint16_t *>(static_cast<unsigned char *>(d.status->getBytesNoCopy()) + 1024)[0] = 1;
  dmaBarrier();
  if (!d.transport.enableQueue(0, d.size, physical, physical + 1024, physical + 2048) ||
      !d.transport.enableQueue(1, statusSize, statusPhysical, statusPhysical + 1024, statusPhysical + 2048)) return false;
  d.transport.setStatus(virtio_pci::statusAcknowledge | virtio_pci::statusDriver | virtio_pci::statusFeaturesOk |
                        virtio_pci::statusDriverOk);
  d.transport.notify(0);
  return (d.transport.status() & 15) == 15;
}

// --- The finger set (mainline DroidVMVirtioInput.c touch_frame / touch_publish) ---
struct Finger { uint8_t device, slot, id; bool fresh; int32_t x, y; };
Finger order[fingers];
unsigned orderCount;
uint32_t publishedAt;

int32_t scale(int32_t value, int32_t minimum, int32_t span, int32_t range)
{
  int64_t v = static_cast<int64_t>(value - minimum) * range / span;
  return v < 0 ? 0 : v >= range ? range - 1 : static_cast<int32_t>(v);
}

void publish(const Finger *set, unsigned count)
{
  publishedAt = native_touch_hid_reports;
  uint32_t seq = native_touch_seq & ~1u;
  native_touch_seq = seq + 1;
  __asm__ volatile("dmb ish" ::: "memory");
  for (unsigned i = 0; i < count; ++i) {
    native_touch_ids[i] = set[i].id;
    native_touch_xs[i] = set[i].x;
    native_touch_ys[i] = set[i].y;
  }
  native_touch_count = count;
  __asm__ volatile("dmb ish" ::: "memory");
  native_touch_seq = seq + 2;
}

// Waits (bounded) until the digitizer has turned the last publication into a report.
void waitReport(uint32_t before)
{
  for (unsigned waited = 0; native_touch_hid_reports == before && waited < 40; waited += 2) IOSleep(2);
}

bool down(const Finger &f)
{
  const Device &d = devices[f.device];
  return f.slot < maxSlots ? d.slots[f.slot].id >= 0 : d.pointerDown;
}

// After a frame: drop the lifted fingers (each moved to the end first: NativeHidInput tells a lift by the list getting
// shorter), update the others, append the new ones.
void frame()
{
  Finger next[fingers];
  unsigned n = 0;
  bool lifting = false;
  for (unsigned i = 0; i < orderCount && !lifting; ++i) lifting = !down(order[i]);
  if (lifting && native_touch_hid_reports == publishedAt) waitReport(publishedAt);
  for (;;) {
    unsigned i = 0;
    while (i < orderCount && down(order[i])) ++i;
    if (i == orderCount) break;
    uint32_t before = native_touch_hid_reports;
    if (i + 1 < orderCount) {
      Finger lifted = order[i];
      memmove(&order[i], &order[i + 1], (orderCount - i - 1) * sizeof(lifted));
      order[orderCount - 1] = lifted;
      publish(order, orderCount);
      waitReport(before);
      before = native_touch_hid_reports;
    }
    publish(order, --orderCount);
    waitReport(before);
  }
  for (unsigned i = 0; i < orderCount; ++i) next[n++] = order[i];
  for (unsigned k = 0; k < deviceCount; ++k) {
    const Device &d = devices[k];
    for (unsigned s = 0; s <= maxSlots && n < fingers; ++s) {
      bool isDown = s < maxSlots ? d.kind == Kind::touch && d.slots[s].id >= 0 : d.kind == Kind::pointer && d.pointerDown;
      if (!isDown) continue;
      bool known = false;
      for (unsigned i = 0; i < n && !known; ++i) known = next[i].device == k && next[i].slot == s;
      if (!known) {
        // The lowest contact identifier 1..10 not held by another finger. Larger identifiers (the mainline driver's
        // 127 for a pointer) reach backboardd but move nothing; a lifted finger was already reported up (NativeHidInput
        // sends tip-up for a vanished identifier), so its identifier can be reused.
        uint8_t id = 1;
        for (bool taken = true; taken && id <= fingers;) {
          taken = false;
          for (unsigned i = 0; i < n && !taken; ++i) taken = next[i].id == id;
          if (taken) ++id;
        }
        next[n++] = {static_cast<uint8_t>(k), static_cast<uint8_t>(s), id, true, 0, 0};
      }
    }
  }
  for (unsigned i = 0; i < n; ++i) {
    const Device &d = devices[next[i].device];
    int32_t x = next[i].slot < maxSlots ? d.slots[next[i].slot].x : d.pointerX;
    int32_t y = next[i].slot < maxSlots ? d.slots[next[i].slot].y : d.pointerY;
    next[i].x = scale(x, d.minX, d.spanX, axisX);
    next[i].y = scale(y, d.minY, d.spanY, axisY);
    if (next[i].fresh && next[i].y >= axisY * (100 - bottomSnap) / 100) next[i].y = axisY - 1;
    next[i].fresh = false;
  }
  memcpy(order, next, n * sizeof(next[0]));
  orderCount = n;
  publish(order, orderCount);
}

// One event; true at the end of a frame that changed the finger set.
bool event(Device &d, const Event &e)
{
  native_virtio_input_pci_stats[6] = native_virtio_input_pci_stats[6] + 1;
  if (e.type == evSyn) return e.code == synReport && d.kind != Kind::keyboard;
  if (d.kind == Kind::touch && e.type == evAbs) {
    if (e.code == absMtSlot) d.slot = e.value >= 0 && e.value < static_cast<int32_t>(maxSlots) ? static_cast<unsigned>(e.value) : maxSlots;
    else if (d.slot < maxSlots) {
      if (e.code == absMtTrackingId) d.slots[d.slot].id = e.value < 0 ? -1 : e.value;
      else if (e.code == absMtPositionX) d.slots[d.slot].x = e.value;
      else if (e.code == absMtPositionY) d.slots[d.slot].y = e.value;
    }
  } else if (d.kind == Kind::pointer) {
    if (e.type == evAbs && e.code == absX) d.pointerX = e.value;
    else if (e.type == evAbs && e.code == absY) d.pointerY = e.value;
    else if (e.type == evKey && (e.code == btnLeft || e.code == btnTouch)) d.pointerDown = e.value != 0;
  } else if (d.kind == Kind::keyboard && e.type == evKey && e.value == 1 &&
             (e.code == keyHome || e.code == keyHomepage || e.code == keyF1)) {
    native_hid_input_command = 1;
    native_virtio_input_pci_stats[7] = native_virtio_input_pci_stats[7] + 1;
  }
  return false;
}

// Takes what the device wrote, frame by frame, and gives the buffers back.
void poll(Device &d)
{
  unsigned returned = 0;
  uint16_t head = d.available()[1];
  while (d.usedRing()[1] != d.used) {
    dmaBarrier();
    uint32_t id = d.usedElements()[(d.used % d.size) * 2];
    if (id < d.size) {
      if (event(d, d.events()[id])) frame();
      d.available()[2 + (head + returned) % d.size] = static_cast<uint16_t>(id);
      ++returned;
    }
    ++d.used;
  }
  if (returned) {
    dmaBarrier();
    d.available()[1] = static_cast<uint16_t>(head + returned);
    dmaBarrier();
    d.transport.notify(0);
  }
  d.transport.acknowledgeInterrupt();
}

IOService *waitFor(OSDictionary *matching, uint64_t seconds)
{
  if (!matching) return nullptr;
  IOService *service = IOService::waitForMatchingService(matching, seconds * 1000000000ULL);
  matching->release();
  return service;
}

const char *kindName(Kind kind)
{
  return kind == Kind::touch ? "multi-touch" : kind == Kind::pointer ? "absolute pointer" : "keyboard";
}

void inputThread(void *, wait_result_t)
{
  native_virtio_input_pci_stats[0] = 1;
  IOService *bridge = waitFor(IOService::nameMatching("pcie"), 600);
  IOMemoryMap *ecam = bridge ? bridge->mapDeviceMemoryWithIndex(0, kIOMapAnywhere | kIOMapInhibitCache) : nullptr;
  OSSafeReleaseNULL(bridge);
  if (!ecam) { IOLog("NativeVirtioInputPci: no ECAM bridge (/arm-io/pcie)\n"); return; }
  for (unsigned index = 0; index < maxDevices && deviceCount < maxDevices; ++index) {
    Device &d = devices[deviceCount];
    if (!d.transport.open(static_cast<uintptr_t>(ecam->getVirtualAddress()), ecam->getLength(), virtio_pci::deviceInput, index)) break;
    native_virtio_input_pci_stats[1] = native_virtio_input_pci_stats[1] + 1;
    if (!configure(d)) {
      // Unused devices are reset rather than left as the firmware's virtio drivers left them.
      d.transport.reset();
      d.transport.close();
      OSSafeReleaseNULL(d.memory);
      OSSafeReleaseNULL(d.status);
      d = Device();
      continue;
    }
    unsigned counter = d.kind == Kind::touch ? 3 : d.kind == Kind::pointer ? 4 : 5;
    native_virtio_input_pci_stats[counter] = native_virtio_input_pci_stats[counter] + 1;
    IOLog("NativeVirtioInputPci: function %u: %s, x %d+%d, y %d+%d\n", d.transport.pciSlot(), kindName(d.kind),
          d.minX, d.spanX, d.minY, d.spanY);
    ++deviceCount;
  }
  native_virtio_input_pci_stats[2] = deviceCount;
  native_virtio_input_pci_stats[0] = 2;
  IOLog("NativeVirtioInputPci: %u device(s) of %u virtio-input function(s)\n", deviceCount, native_virtio_input_pci_stats[1]);
  if (!deviceCount) return;
  for (;;) {
    for (unsigned i = 0; i < deviceCount; ++i) poll(devices[i]);
    IOSleep(pollMs);
  }
}

struct Starter {
  Starter() {
    unsigned enabled = 1;
    PE_parse_boot_argn("virtio-input-pci", &enabled, sizeof(enabled));
    if (!enabled) return;
    thread_t thread;
    if (kernel_thread_start(inputThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
  }
};
Starter starter;

}  // namespace
