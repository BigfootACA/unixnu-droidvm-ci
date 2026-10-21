// SPDX-License-Identifier: BSD-2-Clause-Patent
// virtio-gpu scanout of the GOP framebuffer for the DroidVM guest (crosvm --gpu, virtio-pci; UniXNU-droidvm).
//
// The UEFI firmware's virtio-gpu GOP is Blt-only, so DarwinBoot and the legacy display keep drawing into the linear
// framebuffer that crosvm's --simplefb provides. Once the kernel runs, this driver takes the virtio-gpu device over
// (VirtIO 1.2 5.7, 2D commands only): one B8G8R8X8 resource the size of the GOP mode, backed by guest pages, set as the
// scanout of the first enabled display, and every virtio-gpu-ms (default 50) milliseconds the GOP framebuffer is copied
// into the backing and transferred and flushed to the host. A kernel thread started at C++ static initialisation does
// the work, as NativeVirtioInput does; no IOKit personality is needed. virtio-gpu=0 leaves the device alone.
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOService.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
#include "NativeVirtioPci.h"

extern "C" {
// Observation: [0] stage, [1] last failure, [2] frames flushed, [3] last command type, [4] last response type,
// [5] width, [6] height, [7] scanout
volatile uint32_t native_virtio_gpu_stats[8];
}

namespace {

// struct virtio_gpu_ctrl_hdr and the 2D commands (VirtIO 1.2 5.7.6.7)
enum : uint32_t {
  cmdGetDisplayInfo = 0x0100, cmdResourceCreate2d = 0x0101, cmdSetScanout = 0x0103, cmdResourceFlush = 0x0104,
  cmdTransferToHost2d = 0x0105, cmdResourceAttachBacking = 0x0106,
  respOkNodata = 0x1100, respOkDisplayInfo = 0x1101,
};
enum : uint32_t { formatB8G8R8X8 = 2 };
constexpr uint32_t resourceId = 1;
constexpr unsigned queueEntries = 8, maxScanouts = 16;

struct Header { uint32_t type, flags; uint64_t fence; uint32_t context; uint8_t ring, padding[3]; };
struct Rect { uint32_t x, y, width, height; };
struct DisplayOne { Rect rect; uint32_t enabled, flags; };
struct DisplayInfo { Header header; DisplayOne modes[maxScanouts]; };
struct Create2d { Header header; uint32_t resource, format, width, height; };
struct AttachBacking { Header header; uint32_t resource, entries; };
struct MemEntry { uint64_t address; uint32_t length, padding; };
struct SetScanout { Header header; Rect rect; uint32_t scanout, resource; };
struct Transfer2d { Header header; Rect rect; uint64_t offset; uint32_t resource, padding; };
struct Flush { Header header; Rect rect; uint32_t resource, padding; };
struct Descriptor { uint64_t address; uint32_t length; uint16_t flags, next; };
struct Used { uint32_t id, length; };
static_assert(sizeof(Header) == 24 && sizeof(DisplayInfo) == 408 && sizeof(Create2d) == 40 && sizeof(AttachBacking) == 32 &&
              sizeof(MemEntry) == 16 && sizeof(SetScanout) == 48 && sizeof(Transfer2d) == 56 && sizeof(Flush) == 48 &&
              sizeof(Descriptor) == 16, "virtio-gpu wire layout");
inline void dmaBarrier() { __asm__ volatile("dmb osh" ::: "memory"); }

// Queue memory (one page): descriptors at 0, available ring at 1024, used ring at 2048. Requests at 4096, responses at
// 4096 + 32 KiB (the attach-backing request carries one 16-byte entry per backing page).
constexpr unsigned requestOffset = 4096, requestBytes = 32768, responseOffset = requestOffset + requestBytes,
                   responseBytes = 4096, queueMemoryBytes = responseOffset + responseBytes;

struct Gpu {
  virtio_pci::Transport transport;
  IOBufferMemoryDescriptor *queueMemory = nullptr;
  unsigned char *base = nullptr;
  uint64_t physical = 0;
  uint16_t size = 0, availableIndex = 0, usedIndex = 0;
  Descriptor *descriptors() { return reinterpret_cast<Descriptor *>(base); }
  volatile uint16_t *available() { return reinterpret_cast<volatile uint16_t *>(base + 1024); }
  volatile uint16_t *used() { return reinterpret_cast<volatile uint16_t *>(base + 2048); }
  void *request() { return base + requestOffset; }
  Header *response() { return reinterpret_cast<Header *>(base + responseOffset); }
};

Gpu gpu;
IOBufferMemoryDescriptor *backing;
IOMemoryMap *framebufferMap;

// One synchronous command: a device-readable request and a device-writable response, polled for completion.
bool command(uint32_t requestLength, uint32_t responseLength, uint32_t expected)
{
  uint32_t type = static_cast<Header *>(gpu.request())->type;
  native_virtio_gpu_stats[3] = type;
  bzero(gpu.response(), responseLength);
  Descriptor *d = gpu.descriptors();
  d[0] = {gpu.physical + requestOffset, requestLength, 1, 1};   // NEXT
  d[1] = {gpu.physical + responseOffset, responseLength, 2, 0};   // WRITE
  gpu.available()[2 + gpu.availableIndex % gpu.size] = 0;
  dmaBarrier();
  gpu.available()[1] = ++gpu.availableIndex;
  dmaBarrier();
  gpu.transport.notify(0);
  uint64_t deadline;
  clock_interval_to_deadline(1000, kMillisecondScale, &deadline);
  while (gpu.used()[1] == gpu.usedIndex) {
    if (mach_absolute_time() > deadline) { native_virtio_gpu_stats[1] = 10; return false; }
    IODelay(20);
  }
  dmaBarrier();
  ++gpu.usedIndex;
  gpu.transport.acknowledgeInterrupt();
  native_virtio_gpu_stats[4] = gpu.response()->type;
  return gpu.response()->type == expected;
}

template <typename T> T *prepare(uint32_t type)
{
  T *request = static_cast<T *>(gpu.request());
  bzero(request, sizeof(T));
  request->header.type = type;
  return request;
}

bool configure()
{
  if (!gpu.transport.negotiate(0)) return false;
  uint16_t maximum = gpu.transport.queueMaximum(0);
  gpu.size = maximum < queueEntries ? maximum : queueEntries;
  if (gpu.size < 2) return false;
  gpu.queueMemory = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task,
    static_cast<IOOptionBits>(kIODirectionInOut) | static_cast<IOOptionBits>(kIOMemoryPhysicallyContiguous), queueMemoryBytes, 4096);
  if (!gpu.queueMemory) return false;
  IOByteCount length = 0;
  gpu.physical = gpu.queueMemory->getPhysicalSegment(0, &length);
  if (!gpu.physical || length < queueMemoryBytes) return false;
  gpu.base = static_cast<unsigned char *>(gpu.queueMemory->getBytesNoCopy());
  bzero(gpu.base, queueMemoryBytes);
  gpu.available()[0] = 1;   // VIRTQ_AVAIL_F_NO_INTERRUPT: polled
  dmaBarrier();
  if (!gpu.transport.enableQueue(0, gpu.size, gpu.physical, gpu.physical + 1024, gpu.physical + 2048)) return false;
  gpu.transport.setStatus(virtio_pci::statusAcknowledge | virtio_pci::statusDriver | virtio_pci::statusFeaturesOk |
                          virtio_pci::statusDriverOk);
  return (gpu.transport.status() & 15) == 15;
}

// The GOP mode the firmware handed over (PE_Video: physical base, row bytes, 32 bpp BGRX).
bool framebuffer(PE_Video &video)
{
  return !PE_current_console(&video) && video.v_baseAddr && video.v_width && video.v_height && video.v_depth == 32 &&
         video.v_rowBytes >= video.v_width * 4 && video.v_width <= 8192 && video.v_height <= 8192;
}

bool setUp(const PE_Video &video)
{
  uint32_t width = static_cast<uint32_t>(video.v_width), height = static_cast<uint32_t>(video.v_height);
  native_virtio_gpu_stats[5] = width;
  native_virtio_gpu_stats[6] = height;
  prepare<DisplayInfo>(cmdGetDisplayInfo);
  if (!command(sizeof(Header), sizeof(DisplayInfo), respOkDisplayInfo)) { native_virtio_gpu_stats[1] = 3; return false; }
  const DisplayInfo *info = reinterpret_cast<const DisplayInfo *>(gpu.response());
  uint32_t scanout = 0;
  for (uint32_t i = 0; i < maxScanouts; ++i) {
    if (info->modes[i].enabled) { scanout = i; break; }
  }
  native_virtio_gpu_stats[7] = scanout;
  IOLog("NativeVirtioGpu: host display %u: %ux%u, GOP %ux%u\n", scanout, info->modes[scanout].rect.width,
        info->modes[scanout].rect.height, width, height);

  Create2d *create = prepare<Create2d>(cmdResourceCreate2d);
  *create = {create->header, resourceId, formatB8G8R8X8, width, height};
  if (!command(sizeof(Create2d), sizeof(Header), respOkNodata)) { native_virtio_gpu_stats[1] = 4; return false; }

  uint64_t bytes = static_cast<uint64_t>(width) * height * 4;
  backing = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task, kIODirectionInOut, bytes, 4096);
  if (!backing || backing->prepare() != kIOReturnSuccess) { native_virtio_gpu_stats[1] = 5; return false; }
  bzero(backing->getBytesNoCopy(), bytes);
  AttachBacking *attach = prepare<AttachBacking>(cmdResourceAttachBacking);
  attach->resource = resourceId;
  MemEntry *entries = reinterpret_cast<MemEntry *>(attach + 1);
  uint32_t count = 0;
  for (uint64_t offset = 0; offset < bytes;) {
    IOByteCount segment = 0;
    uint64_t address = backing->getPhysicalSegment(offset, &segment, kIOMemoryMapperNone);
    if (!address || !segment) { native_virtio_gpu_stats[1] = 6; return false; }
    if (segment > bytes - offset) segment = bytes - offset;
    if (count && entries[count - 1].address + entries[count - 1].length == address &&
        entries[count - 1].length + segment <= UINT32_MAX) {
      entries[count - 1].length += static_cast<uint32_t>(segment);
    } else {
      if (sizeof(AttachBacking) + (count + 1) * sizeof(MemEntry) > requestBytes) { native_virtio_gpu_stats[1] = 7; return false; }
      entries[count++] = {address, static_cast<uint32_t>(segment), 0};
    }
    offset += segment;
  }
  attach->entries = count;
  if (!command(static_cast<uint32_t>(sizeof(AttachBacking) + count * sizeof(MemEntry)), sizeof(Header), respOkNodata)) {
    native_virtio_gpu_stats[1] = 8;
    return false;
  }
  SetScanout *set = prepare<SetScanout>(cmdSetScanout);
  set->rect = {0, 0, width, height};
  set->scanout = scanout;
  set->resource = resourceId;
  if (!command(sizeof(SetScanout), sizeof(Header), respOkNodata)) { native_virtio_gpu_stats[1] = 9; return false; }

  IOMemoryDescriptor *source = IOMemoryDescriptor::withPhysicalAddress(video.v_baseAddr, video.v_rowBytes * video.v_height,
                                                                      kIODirectionIn);
  framebufferMap = source ? source->createMappingInTask(kernel_task, 0, kIOMapAnywhere | kIOMapWriteCombineCache) : nullptr;
  OSSafeReleaseNULL(source);
  if (!framebufferMap) { native_virtio_gpu_stats[1] = 11; return false; }
  IOLog("NativeVirtioGpu: resource %ux%u, %u backing range(s), scanout %u\n", width, height, count, scanout);
  return true;
}

bool present(const PE_Video &video)
{
  uint32_t width = static_cast<uint32_t>(video.v_width), height = static_cast<uint32_t>(video.v_height);
  const unsigned char *from = reinterpret_cast<const unsigned char *>(framebufferMap->getVirtualAddress());
  unsigned char *to = static_cast<unsigned char *>(backing->getBytesNoCopy());
  for (uint32_t y = 0; y < height; ++y) memcpy(to + static_cast<size_t>(y) * width * 4, from + y * video.v_rowBytes, width * 4);
  __asm__ volatile("dsb sy" ::: "memory");
  Transfer2d *transfer = prepare<Transfer2d>(cmdTransferToHost2d);
  transfer->rect = {0, 0, width, height};
  transfer->resource = resourceId;
  if (!command(sizeof(Transfer2d), sizeof(Header), respOkNodata)) return false;
  Flush *flush = prepare<Flush>(cmdResourceFlush);
  flush->rect = {0, 0, width, height};
  flush->resource = resourceId;
  if (!command(sizeof(Flush), sizeof(Header), respOkNodata)) return false;
  native_virtio_gpu_stats[2] = native_virtio_gpu_stats[2] + 1;
  return true;
}

IOService *waitFor(OSDictionary *matching, uint64_t seconds)
{
  if (!matching) return nullptr;
  IOService *service = IOService::waitForMatchingService(matching, seconds * 1000000000ULL);
  matching->release();
  return service;
}

void gpuThread(void *, wait_result_t)
{
  unsigned period = 50;
  PE_parse_boot_argn("virtio-gpu-ms", &period, sizeof(period));
  if (period < 5) period = 5;
  native_virtio_gpu_stats[0] = 1;
  IOService *bridge = waitFor(IOService::nameMatching("pcie"), 600);
  IOMemoryMap *ecam = bridge ? bridge->mapDeviceMemoryWithIndex(0, kIOMapAnywhere | kIOMapInhibitCache) : nullptr;
  OSSafeReleaseNULL(bridge);
  PE_Video video = {};
  if (!ecam) {
    native_virtio_gpu_stats[1] = 1;
    IOLog("NativeVirtioGpu: no ECAM bridge (/arm-io/pcie)\n");
    return;
  }
  if (!gpu.transport.open(static_cast<uintptr_t>(ecam->getVirtualAddress()), ecam->getLength(), virtio_pci::deviceGpu, 0)) {
    native_virtio_gpu_stats[1] = 2;
    IOLog("NativeVirtioGpu: no virtio-gpu function\n");
    return;
  }
  if (!framebuffer(video) || !configure() || !setUp(video)) {
    IOLog("NativeVirtioGpu: setup failed at %u (command %#x, response %#x)\n", native_virtio_gpu_stats[1],
          native_virtio_gpu_stats[3], native_virtio_gpu_stats[4]);
    return;
  }
  native_virtio_gpu_stats[0] = 2;
  IOLog("NativeVirtioGpu: presenting the GOP framebuffer every %u ms\n", period);
  unsigned failures = 0;
  for (;;) {
    if (!present(video) && ++failures == 1)
      IOLog("NativeVirtioGpu: present failed (command %#x, response %#x)\n", native_virtio_gpu_stats[3], native_virtio_gpu_stats[4]);
    IOSleep(period);
  }
}

struct Starter {
  Starter() {
    unsigned enabled = 1;
    PE_parse_boot_argn("virtio-gpu", &enabled, sizeof(enabled));
    if (!enabled) return;
    thread_t thread;
    if (kernel_thread_start(gpuThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
  }
};
Starter starter;

}  // namespace
