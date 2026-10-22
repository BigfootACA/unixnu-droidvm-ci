// SPDX-License-Identifier: BSD-2-Clause-Patent
// UniXNU NativeVirtioBlock with a modern virtio-pci transport beside virtio-mmio (UniXNU-droidvm).
// crosvm (DroidVM) has virtio-pci only; the provider is then the generic ECAM host bridge of bus 0.
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IOLib.h>
#include <IOKit/storage/IOBlockStorageDevice.h>
#include <libkern/c++/OSData.h>
#include <libkern/c++/OSNumber.h>
#include <libkern/crypto/sha2.h>
#include <kern/clock.h>
#include <kern/task.h>
#include "NativeVirtioPci.h"

#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK
#include "verified_root.h"
#if defined(NATIVE_VERIFIED_BLOCK_MULTI_POLICY) && NATIVE_VERIFIED_BLOCK_MULTI_POLICY
#include "VerifiedBlockPolicy.h"
#endif
#include <IOKit/IOBSD.h>
#include <libkern/c++/OSDictionary.h>
#include <libkern/c++/OSIterator.h>
#include <libkern/c++/OSNumber.h>
#include <libkern/c++/OSString.h>
#include <sys/types.h>
#include <sys/param.h>
#include <vm/vm_kern.h>
#include <vm/vm_map.h>
extern "C" {
extern const struct vr_crypto vr_xnu_crypto;
extern const uint8_t verified_root_manifest[], verified_root_key[];
extern const uint64_t verified_root_manifest_bytes;
}
#endif

extern "C" {
volatile uint32_t native_virtio_ready = 0;
#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK
int native_verified_block_root_authorized(dev_t device);
// Provided by NativeUfs when linked: -1 not a UFS window, else 1/0 for the signed volume UUID.
// UniXNU-droidvm: the kernel is linked statically, so an undefined weak reference does not link; this weak default
// (no UFS window) yields to NativeUfs's definition when that driver is linked.
__attribute__((weak)) int native_ufs_block_authenticates(IOService *, const char *, uint8_t *) { return -1; }
int native_verified_block_volume_device(const char *, dev_t *, char *, size_t);
volatile uint32_t native_verified_block_root_device = 0;
volatile uint32_t native_verified_block_root_media_matches = 0;
volatile uint32_t native_verified_block_root_provider_depth = 0;
volatile uint32_t native_verified_block_root_uuid_match = 0;
volatile uint32_t native_verified_block_wrong_uuid_refused = 0;
char native_verified_block_root_uuid[37] = {};
volatile uint32_t native_virtio_manifest_ready = 0;
volatile uint32_t native_virtio_verified_devices = 0;
volatile uint32_t native_virtio_ambiguous_policies = 0;
volatile uint64_t native_virtio_verified_blocks = 0;
volatile uint32_t native_virtio_integrity_failures = 0;
volatile uintptr_t native_virtio_verified_manifest_pointer = 0;
volatile uintptr_t native_virtio_verified_key_pointer = 0;
#endif
volatile uint32_t native_virtio_read_requests = 0;
volatile uint32_t native_virtio_read_failures = 0;
volatile uint64_t native_virtio_sectors = 0;
volatile uint64_t native_virtio_mmio_physical = 0;
unsigned char native_virtio_first_sector_sha256[32] = {};
__attribute__((noinline)) void native_virtio_read_checkpoint(void *device) {
  __asm__ volatile("" : : "r"(device) : "memory");
}
}

// VirtIO 1.2 MMIO or PCI (modern), split queue. All numbers here are protocol offsets.
namespace {
constexpr unsigned queueSize = 8;
constexpr unsigned transferSize = 65536;
struct Descriptor { uint64_t address; uint32_t length; uint16_t flags; uint16_t next; };
struct Request { uint32_t operation; uint32_t reserved; uint64_t sector; };
struct Used { uint32_t id; uint32_t length; };
static_assert(sizeof(Descriptor) == 16 && sizeof(Request) == 16 && sizeof(Used) == 8, "VirtIO wire layout");
inline void dmaBarrier() { __asm__ volatile("dmb osh" ::: "memory"); }
}

class NativeVirtioBlock : public IOBlockStorageDevice {
  OSDeclareDefaultStructors(NativeVirtioBlock)
  IOMemoryMap *mapping = nullptr;
  // The PCI transport (NativeVirtioPci.h) maps its own windows; registers stays 0 for it.
  bool pci = false;
  // A sibling (startSiblings) is published by the first function only once every function is set up.
  bool deferRegistration = false;
  virtio_pci::Transport transport;
  IOBufferMemoryDescriptor *queueMemory = nullptr;
  IOBufferMemoryDescriptor *transferMemory = nullptr;
  IOLock *gate = nullptr;
  uintptr_t registers = 0;
  uint64_t queuePhysical = 0;
  uint64_t transferPhysical = 0;
  uint64_t capacity = 0;
  uint16_t availableIndex = 0;
  uint16_t usedIndex = 0;
#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK
  struct vr_view verified = {};
  vm_offset_t verifiedStorage = 0;
  vm_size_t verifiedStorageBytes = 0;
  unsigned char *verifiedSnapshot = nullptr;
  bool configureVerification();
  IOReturn readRawSectors(uint64_t, uint32_t);
#endif
  const void *readBuffer() const {
#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK
    return verifiedSnapshot;
#else
    return transferMemory->getBytesNoCopy();
#endif
  }
  bool online = false;
  bool ownsDevice = false;
  // Plain ldr/str only (NativeVirtioPci.h): KVM and Gunyah cannot emulate writeback forms on MMIO.
  uint32_t read32(unsigned offset) const { return virtio_pci::mmioRead<uint32_t>(registers + offset); }
  void write32(unsigned offset, uint32_t value) { virtio_pci::mmioWrite<uint32_t>(registers + offset, value); }
  void writeAddress(unsigned offset, uint64_t value) {
    write32(offset, static_cast<uint32_t>(value));
    write32(offset + 4, static_cast<uint32_t>(value >> 32));
  }
  bool transportMapped() const { return pci ? transport.isOpen() : registers != 0; }
  uint8_t deviceStatus() const { return pci ? transport.status() : static_cast<uint8_t>(read32(0x70)); }
  void setDeviceStatus(uint8_t value) {
    if (pci) transport.setStatus(value);
    else write32(0x70, value);
  }
  void notifyQueue() {
    if (pci) transport.notify(0);
    else write32(0x50, 0);
  }
  void acknowledgeInterrupt() {
    if (pci) { transport.acknowledgeInterrupt(); return; }
    uint32_t interrupts = read32(0x60) & 3;
    if (interrupts) write32(0x64, interrupts);
  }
  bool readCapacity();
  bool startPci(IOService *provider);
  unsigned pciIndex() const;
  void startSiblings(IOService *provider);
  bool reset();
  bool configureQueue();
  IOReturn readSectors(uint64_t sector, uint32_t bytes);
public:
#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK
  bool authenticatesVolume(const char *, uint8_t * = nullptr) const;
#endif
  IOService *probe(IOService *, SInt32 *) override;
  bool start(IOService *) override;
  void stop(IOService *) override;
  void free() override;
  IOReturn doEjectMedia() override { return kIOReturnUnsupported; }
  IOReturn doFormatMedia(UInt64) override { return kIOReturnNotWritable; }
  UInt32 doGetFormatCapacities(UInt64 *, UInt32) const override { return 0; }
  char *getVendorString() override { return const_cast<char *>("VirtIO"); }
  char *getProductString() override { return const_cast<char *>("VirtIO read-only block"); }
  char *getRevisionString() override { return const_cast<char *>("1.0"); }
  char *getAdditionalDeviceInfoString() override {
    return const_cast<char *>(pci ? "modern PCI, coherent DMA" : "modern MMIO, coherent DMA");
  }
  IOReturn reportBlockSize(UInt64 *value) override { *value = 512; return kIOReturnSuccess; }
  IOReturn reportEjectability(bool *value) override { *value = false; return kIOReturnSuccess; }
  IOReturn reportMaxValidBlock(UInt64 *value) override { *value = capacity - 1; return kIOReturnSuccess; }
  IOReturn reportMediaState(bool *present, bool *changed) override {
    *present = online; if (changed) *changed = false; return kIOReturnSuccess;
  }
  IOReturn reportRemovability(bool *value) override { *value = false; return kIOReturnSuccess; }
  IOReturn reportWriteProtection(bool *value) override { *value = true; return kIOReturnSuccess; }
  IOReturn doSynchronizeCache() override { return online ? kIOReturnSuccess : kIOReturnNotReady; }
  IOReturn doSynchronize(UInt64, UInt64, IOOptionBits) override { return doSynchronizeCache(); }
  IOReturn getWriteCacheState(bool *value) override { *value = false; return kIOReturnSuccess; }
  IOReturn setWriteCacheState(bool) override { return kIOReturnUnsupported; }
  IOReturn doAsyncReadWrite(IOMemoryDescriptor *, UInt64, UInt64, IOStorageAttributes *, IOStorageCompletion *) override;
};
OSDefineMetaClassAndStructors(NativeVirtioBlock, IOBlockStorageDevice)

IOService *NativeVirtioBlock::probe(IOService *provider, SInt32 *score)
{
  OSData *compatible = OSDynamicCast(OSData, provider->getProperty("compatible"));
  if (!compatible) return nullptr;
  bool mmio = compatible->getLength() == 12 && !memcmp(compatible->getBytesNoCopy(), "virtio,mmio", 12);
  bool ecam = compatible->getLength() == 22 && !memcmp(compatible->getBytesNoCopy(), "pci-host-ecam-generic", 22);
  if (!mmio && !ecam) return nullptr;
  return IOBlockStorageDevice::probe(provider, score);
}

bool NativeVirtioBlock::reset()
{
  if (!transportMapped() || !ownsDevice) return true;
  online = false;
  setDeviceStatus(0);
  uint64_t deadline;
  clock_interval_to_deadline(1000, kMillisecondScale, &deadline);
  while (deviceStatus()) {
    if (mach_absolute_time() >= deadline) return false;
    IODelay(1);
  }
  return true;
}

bool NativeVirtioBlock::configureQueue()
{
  if (!reset()) return false;
  // A read-only backend and VIRTIO_F_VERSION_1 are mandatory for this driver.
  if (pci) {
    if (!transport.negotiate(1U << 5) || transport.queueMaximum(0) < queueSize) return false;
  } else {
    write32(0x70, 1);
    write32(0x70, 3);
    write32(0x14, 0);
    uint32_t lowFeatures = read32(0x10);
    write32(0x14, 1);
    uint32_t highFeatures = read32(0x10);
    if (!(lowFeatures & (1U << 5)) || !(highFeatures & 1)) return false;
    write32(0x24, 0);
    write32(0x20, 1U << 5);
    write32(0x24, 1);
    write32(0x20, 1);
    write32(0x70, 11);
    if (!(read32(0x70) & 8)) return false;
    write32(0x30, 0);
    if (read32(0x44) || read32(0x34) < queueSize) return false;
  }
  queueMemory = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task,
    static_cast<IOOptionBits>(kIODirectionInOut) | static_cast<IOOptionBits>(kIOMemoryPhysicallyContiguous), 4096, 4096);
  transferMemory = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task,
    static_cast<IOOptionBits>(kIODirectionInOut) | static_cast<IOOptionBits>(kIOMemoryPhysicallyContiguous), transferSize, 4096);
  if (!queueMemory || !transferMemory) return false;
  IOByteCount queueLength = 0, transferLength = 0;
  queuePhysical = queueMemory->getPhysicalSegment(0, &queueLength);
  transferPhysical = transferMemory->getPhysicalSegment(0, &transferLength);
  if (!queuePhysical || !transferPhysical || queueLength < 4096 || transferLength < transferSize) return false;
  bzero(queueMemory->getBytesNoCopy(), 4096);
  // Suppress used-buffer interrupts: this bounded initial transport polls.
  *reinterpret_cast<volatile uint16_t *>(static_cast<unsigned char *>(queueMemory->getBytesNoCopy()) + 128) = 1;
  availableIndex = usedIndex = 0;
  dmaBarrier();
  if (pci) {
    if (!transport.enableQueue(0, queueSize, queuePhysical, queuePhysical + 128, queuePhysical + 256)) return false;
  } else {
    write32(0x38, queueSize);
    writeAddress(0x80, queuePhysical);
    writeAddress(0x90, queuePhysical + 128);
    writeAddress(0xa0, queuePhysical + 256);
    write32(0x44, 1);
  }
  if (!readCapacity()) return false;
  setDeviceStatus(15);
  online = (deviceStatus() & 15) == 15;
  return online;
}

bool NativeVirtioBlock::readCapacity()
{
  // Read 64-bit capacity through stable, aligned 32-bit configuration accesses.
  for (unsigned attempt = 0; attempt < 8; ++attempt) {
    uint32_t generation = pci ? transport.generation() : read32(0xfc);
    uint64_t low = pci ? transport.config<uint32_t>(0) : read32(0x100);
    uint64_t high = pci ? transport.config<uint32_t>(4) : read32(0x104);
    if (generation == (pci ? transport.generation() : read32(0xfc))) {
      capacity = low | (high << 32);
      return capacity && capacity <= UINT64_MAX / 512;
    }
  }
  return false;
}

// Which virtio-blk function of the bus this instance drives: the matched instance takes the first and starts one
// sibling per further function (startSiblings), each with its index in this property.
static const char *const pciIndexKey = "NativeVirtioPciIndex";

unsigned NativeVirtioBlock::pciIndex() const
{
  OSNumber *index = OSDynamicCast(OSNumber, getProperty(pciIndexKey));
  return index ? index->unsigned32BitValue() : 0;
}

// Bus 0 of a generic ECAM host bridge: the pciIndex()-th virtio-blk function (NativeVirtioPci.h).
bool NativeVirtioBlock::startPci(IOService *provider)
{
  mapping = provider->mapDeviceMemoryWithIndex(0, kIOMapAnywhere | kIOMapInhibitCache);
  if (!mapping) return false;
  pci = transport.open(static_cast<uintptr_t>(mapping->getVirtualAddress()), mapping->getLength(),
                       virtio_pci::deviceBlock, pciIndex()) && transport.hasDeviceConfig();
  if (pci) IOLog("NativeVirtioBlock: virtio-pci function %u, common config at %#llx\n", transport.pciSlot(), transport.commonPhysical());
  else IOLog("NativeVirtioBlock: no usable virtio-blk function %u on the ECAM bus\n", pciIndex());
  return pci;
}

// Every further function is set up before any of them is published: the root waits only for its own media, and the
// owner SystemOS mount at init looks its volume up without waiting, so no disk may become visible before the others.
void NativeVirtioBlock::startSiblings(IOService *provider)
{
  if (pciIndex() != 0 || !mapping) return;
  unsigned total = virtio_pci::count(static_cast<uintptr_t>(mapping->getVirtualAddress()), mapping->getLength(),
                                     virtio_pci::deviceBlock);
  NativeVirtioBlock *started[8] = {};
  for (unsigned index = 1; index < total && index < 8; ++index) {
    NativeVirtioBlock *sibling = new NativeVirtioBlock;
    OSNumber *number = OSNumber::withNumber(index, 32);
    bool ok = sibling && number && sibling->init(nullptr) && sibling->setProperty(pciIndexKey, number) &&
              sibling->attach(provider);
    if (ok) {
      sibling->deferRegistration = true;
      if (!sibling->start(provider)) {
        sibling->detach(provider);
        ok = false;
      }
    }
    if (ok) {
      started[index] = sibling;   // the provider retains it while attached; this reference is dropped after publication
    } else {
      IOLog("NativeVirtioBlock: virtio-blk function %u did not start\n", index);
      OSSafeReleaseNULL(sibling);
    }
    OSSafeReleaseNULL(number);
  }
  for (unsigned index = 1; index < 8; ++index) {
    if (!started[index]) continue;
    started[index]->registerService();
    started[index]->release();
  }
}

#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK
bool NativeVirtioBlock::authenticatesVolume(const char *uuid, uint8_t *manifestDigest) const
{
  if (!uuid || !online || !verifiedStorage || !verifiedSnapshot || !verified.hashes) return false;
  static const char hex[] = "0123456789ABCDEF";
  char expected[37];
  unsigned at = 0;
  for (unsigned i = 0; i < 16; ++i) {
    if (i == 4 || i == 6 || i == 8 || i == 10) expected[at++] = '-';
    expected[at++] = hex[verified.volume_uuid[i] >> 4];
    expected[at++] = hex[verified.volume_uuid[i] & 15];
  }
  expected[at] = 0;
  if (strcmp(uuid, expected)) return false;
  if (manifestDigest) {
    // Hash only the authenticated envelope, excluding allocation padding and key.
    const uint64_t bytes = VR_HEADER_BYTES + VR_SIGNATURE_BYTES + verified.block_count * 32;
    SHA384_CTX context;
    SHA384_Init(&context);
    SHA384_Update(&context, reinterpret_cast<const void *>(verifiedStorage), bytes);
    SHA384_Final(manifestDigest, &context);
  }
  return true;
}

static int verifiedVolumeQuery(dev_t device, const char *expectedUUID, uint8_t *manifestDigest)
{
  native_verified_block_root_device = static_cast<uint32_t>(device);
  native_verified_block_root_media_matches = 0;
  native_verified_block_root_provider_depth = 0;
  native_verified_block_root_uuid_match = 0;
  native_verified_block_wrong_uuid_refused = 0;
  bzero(native_verified_block_root_uuid, sizeof(native_verified_block_root_uuid));
  OSDictionary *matching = IOService::serviceMatching("IOMedia");
  OSDictionary *properties = OSDictionary::withCapacity(2);
  OSNumber *mjr = OSNumber::withNumber(static_cast<unsigned>(major(static_cast<uint32_t>(device))), 32);
  OSNumber *mnr = OSNumber::withNumber(static_cast<unsigned>(minor(static_cast<uint32_t>(device))), 32);
  if (!matching || !properties || !mjr || !mnr) {
    OSSafeReleaseNULL(mnr); OSSafeReleaseNULL(mjr); OSSafeReleaseNULL(properties); OSSafeReleaseNULL(matching);
    return 0;
  }
  bool ready = properties->setObject(kIOBSDMajorKey, mjr) && properties->setObject(kIOBSDMinorKey, mnr) &&
               matching->setObject("IOPropertyMatch", properties);
  mnr->release(); mjr->release(); properties->release();
  OSIterator *iterator = ready ? IOService::getMatchingServices(matching) : nullptr;
  matching->release();
  if (!iterator) return 0;
  bool authenticated = false;
  unsigned matches = 0, depth = 0;
  while (OSObject *object = iterator->getNextObject()) {
    IOService *media = OSDynamicCast(IOService, object);
    if (!media) continue;
    if (++matches != 1) { authenticated = false; break; }
    OSString *uuid = OSDynamicCast(OSString, media->getProperty("UUID"));
    if (!uuid || uuid->getLength() != 36 ||
        (expectedUUID && strcmp(expectedUUID, uuid->getCStringNoCopy()))) continue;
    strlcpy(native_verified_block_root_uuid, uuid->getCStringNoCopy(), sizeof(native_verified_block_root_uuid));
    for (IOService *provider = media; provider && depth < 64;
         provider = provider->getProvider()) {
      ++depth;
      NativeVirtioBlock *block = OSDynamicCast(NativeVirtioBlock, provider);
      if (!block) {
        {   // always defined (weak default above)
          int ufs = native_ufs_block_authenticates(provider, uuid->getCStringNoCopy(), manifestDigest);
          if (ufs >= 0) {
            authenticated = ufs == 1;
            native_verified_block_root_uuid_match = authenticated;
            native_verified_block_wrong_uuid_refused = 1;
            break;
          }
        }
        continue;
      }
      authenticated = block->authenticatesVolume(uuid->getCStringNoCopy(), manifestDigest);
      native_verified_block_root_uuid_match = authenticated;
      char wrong[37];
      strlcpy(wrong, uuid->getCStringNoCopy(), sizeof(wrong));
      wrong[0] ^= 1; // Local negative identity only; actual registry/media is unchanged.
      native_verified_block_wrong_uuid_refused = !block->authenticatesVolume(wrong);
      break;
    }
  }
  iterator->release();
  native_verified_block_root_media_matches = matches;
  native_verified_block_root_provider_depth = depth;
  if (!authenticated || matches != 1) {
    if (manifestDigest) bzero(manifestDigest, SHA384_DIGEST_LENGTH);
    return 0;
  }
  return 1;
}

extern "C" int native_verified_block_root_authorized(dev_t device)
{
  return verifiedVolumeQuery(device, nullptr, nullptr);
}

extern "C" int native_verified_block_manifest_sha384(dev_t device, const char *uuid, uint8_t digest[48])
{
  if (!digest) return 0;
  bzero(digest, SHA384_DIGEST_LENGTH);
  if (!uuid || strlen(uuid) != 36) return 0;
  return verifiedVolumeQuery(device, uuid, digest);
}

extern "C" int native_verified_block_volume_device(const char *uuid, dev_t *device, char *path, size_t size)
{
  if (!uuid || !device || !path || size < 6 || strlen(uuid) != 36) return 0;
  *device = NODEV;
  path[0] = 0;
  OSDictionary *matching = IOService::serviceMatching("IOMedia");
  OSDictionary *properties = OSDictionary::withCapacity(1);
  OSString *wanted = OSString::withCString(uuid);
  if (!matching || !properties || !wanted) {
    OSSafeReleaseNULL(wanted); OSSafeReleaseNULL(properties); OSSafeReleaseNULL(matching);
    return 0;
  }
  bool ready = properties->setObject("UUID", wanted) && matching->setObject("IOPropertyMatch", properties);
  wanted->release(); properties->release();
  OSIterator *iterator = ready ? IOService::getMatchingServices(matching) : nullptr;
  matching->release();
  if (!iterator) return 0;
  unsigned matches = 0;
  bool authenticated = false;
  dev_t candidate = NODEV;
  char candidatePath[64] = {};
  while (OSObject *object = iterator->getNextObject()) {
    IOService *media = OSDynamicCast(IOService, object);
    if (!media) continue;
    if (++matches != 1) { authenticated = false; break; }
    OSString *identity = OSDynamicCast(OSString, media->getProperty("UUID"));
    OSString *name = OSDynamicCast(OSString, media->getProperty(kIOBSDNameKey));
    OSNumber *mjr = OSDynamicCast(OSNumber, media->getProperty(kIOBSDMajorKey));
    OSNumber *mnr = OSDynamicCast(OSNumber, media->getProperty(kIOBSDMinorKey));
    if (!identity || strcmp(identity->getCStringNoCopy(), uuid) || !name || !mjr || !mnr ||
        !name->getLength() || name->getLength() >= sizeof(candidatePath) - 6 || name->getLength() >= size - 6 ||
        mjr->unsigned64BitValue() > 255 || mnr->unsigned64BitValue() > 0xffffff) continue;
    candidate = static_cast<dev_t>(makedev(mjr->unsigned32BitValue(), mnr->unsigned32BitValue()));
    if (!native_verified_block_root_authorized(candidate)) continue;
    snprintf(candidatePath, sizeof(candidatePath), "/dev/%s", name->getCStringNoCopy());
    authenticated = true;
  }
  iterator->release();
  if (!authenticated || matches != 1) return 0;
  *device = candidate;
  strlcpy(path, candidatePath, size);
  return 1;
}

bool NativeVirtioBlock::configureVerification()
{
  if (capacity > UINT64_MAX / 512) return false;
  const uint8_t *manifest = verified_root_manifest;
  uint64_t manifestBytes = verified_root_manifest_bytes;
#if defined(NATIVE_VERIFIED_BLOCK_MULTI_POLICY) && NATIVE_VERIFIED_BLOCK_MULTI_POLICY
  manifest = nullptr;
  manifestBytes = 0;
  if (!verified_root_policy_count || verified_root_policy_count > 16) return false;
  for (uint64_t index = 0; index < verified_root_policy_count; ++index) {
    const auto &policy = verified_root_policies[index];
    if (!policy.manifest || policy.manifestBytes < VR_HEADER_BYTES + VR_SIGNATURE_BYTES) return false;
    uint64_t bytes = 0;
    for (unsigned i = 0; i < 8; ++i) bytes |= static_cast<uint64_t>(policy.manifest[24 + i]) << (8 * i);
    if (bytes != capacity * 512) continue;
    if (manifest) {
      __atomic_fetch_add(&native_virtio_ambiguous_policies, 1, __ATOMIC_RELAXED);
      return false;
    }
    manifest = policy.manifest;
    manifestBytes = policy.manifestBytes;
  }
  if (!manifest) return false;
#endif
  vm_size_t tableBytes = round_page(static_cast<vm_size_t>(manifestBytes));
  if (tableBytes < manifestBytes || tableBytes > UINT64_MAX - 4096) return false;
  verifiedStorageBytes = tableBytes + 4096;
  IOLog("NativeVirtioBlock: function %u: policy of %llu bytes, copying\n", transport.pciSlot(), manifestBytes);
  if (kmem_alloc(kernel_map, &verifiedStorage, verifiedStorageBytes,
      static_cast<kma_flags_t>(KMA_KOBJECT | KMA_DATA), VM_KERN_MEMORY_SECURITY) != KERN_SUCCESS) return false;
  memcpy(reinterpret_cast<void *>(verifiedStorage), manifest, manifestBytes);
  IOLog("NativeVirtioBlock: function %u: policy copied, opening\n", transport.pciSlot());
  auto *key = reinterpret_cast<uint8_t *>(verifiedStorage + tableBytes);
  memcpy(key, verified_root_key, VR_KEY_BYTES);
  if (vm_map_protect(kernel_map, verifiedStorage, verifiedStorage + verifiedStorageBytes,
      VM_PROT_READ, TRUE) != KERN_SUCCESS) return false;
  if (vr_open(reinterpret_cast<const uint8_t *>(verifiedStorage), manifestBytes,
      key, 1, &vr_xnu_crypto, &verified) != VR_OK || verified.image_bytes != capacity * 512) {
    IOLog("NativeVirtioBlock: function %u: policy refused\n", transport.pciSlot());
    return false;
  }
  IOLog("NativeVirtioBlock: function %u: policy opened\n", transport.pciSlot());
  verifiedSnapshot = static_cast<unsigned char *>(IOMalloc(transferSize));
  if (!verifiedSnapshot) return false;
  native_virtio_verified_manifest_pointer = verifiedStorage;
  native_virtio_verified_key_pointer = reinterpret_cast<uintptr_t>(key);
  native_virtio_manifest_ready = 1;
  return true;
}

IOReturn NativeVirtioBlock::readSectors(uint64_t sector, uint32_t bytes)
{
  if (!verifiedSnapshot || !online || !bytes || bytes > transferSize - VR_BLOCK_BYTES ||
      (bytes & 511) || sector >= capacity || bytes / 512 > capacity - sector) return kIOReturnBadArgument;
  uint64_t begin = sector * 512, end = begin + bytes;
  uint64_t alignedBegin = begin & ~static_cast<uint64_t>(VR_BLOCK_BYTES - 1);
  uint64_t padding = (VR_BLOCK_BYTES - (end & (VR_BLOCK_BYTES - 1))) & (VR_BLOCK_BYTES - 1);
  if (padding > capacity * 512 - end) padding = capacity * 512 - end;
  uint32_t span = static_cast<uint32_t>(end + padding - alignedBegin);
  if (span > transferSize) return kIOReturnBadArgument;
  IOReturn status = readRawSectors(alignedBegin / 512, span);
  if (status) return status;
  // Snapshot completed DMA before hashing; only this CPU-owned copy is exposed.
  memcpy(verifiedSnapshot, transferMemory->getBytesNoCopy(), span);
#if defined(NATIVE_VERIFIED_BLOCK_INJECT_FAULT) && NATIVE_VERIFIED_BLOCK_INJECT_FAULT
  verifiedSnapshot[0] ^= 1; // Isolated negative input, original disk/signature untouched.
#endif
  for (uint32_t offset = 0; offset < span; offset += VR_BLOCK_BYTES) {
    uint32_t length = span - offset < VR_BLOCK_BYTES ? span - offset : VR_BLOCK_BYTES;
    if (vr_verify_block(&verified, alignedBegin / VR_BLOCK_BYTES + offset / VR_BLOCK_BYTES,
        verifiedSnapshot + offset, length, &vr_xnu_crypto) != VR_OK) {
      __atomic_fetch_add(&native_virtio_integrity_failures, 1, __ATOMIC_RELAXED);
      if (!reset()) panic("NativeVirtioBlock: unverified DMA could not be quiesced");
      return kIOReturnNotPrivileged;
    }
    __atomic_fetch_add(&native_virtio_verified_blocks, 1, __ATOMIC_RELAXED);
  }
  memmove(verifiedSnapshot, verifiedSnapshot + (begin - alignedBegin), bytes);
  return kIOReturnSuccess;
}
IOReturn NativeVirtioBlock::readRawSectors(uint64_t sector, uint32_t bytes)
#else
IOReturn NativeVirtioBlock::readSectors(uint64_t sector, uint32_t bytes)
#endif
{
  if (!online || !bytes || bytes > transferSize || (bytes & 511) ||
      sector >= capacity || bytes / 512 > capacity - sector) return kIOReturnBadArgument;
  auto *queue = static_cast<unsigned char *>(queueMemory->getBytesNoCopy());
  auto *descriptors = reinterpret_cast<Descriptor *>(queue);
  auto *request = reinterpret_cast<Request *>(queue + 512);
  volatile uint8_t *status = queue + 528;
  *request = {0, 0, sector};
  *status = 0xff;
  descriptors[0] = {queuePhysical + 512, sizeof(Request), 1, 1};
  descriptors[1] = {transferPhysical, bytes, 3, 2};
  descriptors[2] = {queuePhysical + 528, 1, 2, 0};
  auto *available = reinterpret_cast<volatile uint16_t *>(queue + 128);
  available[2 + availableIndex % queueSize] = 0;
  dmaBarrier();
  ++availableIndex;
  available[1] = availableIndex;
  dmaBarrier();
  notifyQueue();
  auto *used = reinterpret_cast<volatile uint16_t *>(queue + 256);
  uint64_t deadline;
  clock_interval_to_deadline(1000, kMillisecondScale, &deadline);
  while (used[1] == usedIndex) {
    if (mach_absolute_time() >= deadline) {
      reset();
      __atomic_fetch_add(&native_virtio_read_failures, 1, __ATOMIC_RELAXED);
      return kIOReturnTimeout;
    }
    dmaBarrier();
    IODelay(1);
  }
  dmaBarrier();
  auto *element = reinterpret_cast<volatile Used *>(queue + 260) + usedIndex % queueSize;
  bool valid = used[1] == static_cast<uint16_t>(usedIndex + 1) && element->id == 0 &&
               element->length >= bytes + 1 && *status == 0;
  ++usedIndex;
  acknowledgeInterrupt();
  if (!valid) {
    reset();
    __atomic_fetch_add(&native_virtio_read_failures, 1, __ATOMIC_RELAXED);
    return kIOReturnIOError;
  }
  __atomic_fetch_add(&native_virtio_read_requests, 1, __ATOMIC_RELAXED);
  return kIOReturnSuccess;
}

bool NativeVirtioBlock::start(IOService *provider)
{
  if (!IOBlockStorageDevice::start(provider) || !provider->getProperty("dma-coherent") ||
      provider->getResources() != kIOReturnSuccess) return false;
  OSData *compatible = OSDynamicCast(OSData, provider->getProperty("compatible"));
  if (compatible && compatible->getLength() == 22 &&
      !memcmp(compatible->getBytesNoCopy(), "pci-host-ecam-generic", 22)) {
    if (!startPci(provider)) return false;
    // Before this function's own setup: a disk without a policy (the ESP, in verified mode) must not keep the others down.
    startSiblings(provider);
  } else {
    mapping = provider->mapDeviceMemoryWithIndex(0, kIOMapAnywhere | kIOMapInhibitCache);
    if (!mapping || mapping->getLength() < 0x108) return false;
    registers = static_cast<uintptr_t>(mapping->getVirtualAddress());
    if (read32(0x00) != 0x74726976 || read32(0x04) != 2 || read32(0x08) != 2) return false;
  }
  ownsDevice = true;
  gate = IOLockAlloc();
  if (gate && pci) IOLog("NativeVirtioBlock: function %u: configuring the queue\n", transport.pciSlot());
  if (!gate || !configureQueue()) {
    IOLog("NativeVirtioBlock: queue setup failed (status %#x; a read-only device and VIRTIO_F_VERSION_1 are required)\n",
          transportMapped() ? deviceStatus() : 0);
    return false;
  }
#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK
  if (!configureVerification()) return false;
#endif
  if (IOReturn status = readSectors(0, 512)) {
    IOLog("NativeVirtioBlock: reading sector 0 failed: %#x\n", status);
    return false;
  }
  SHA256_CTX hash;
  SHA256_Init(&hash);
  SHA256_Update(&hash, readBuffer(), 512);
  SHA256_Final(native_virtio_first_sector_sha256, &hash);
  native_virtio_sectors = capacity;
  native_virtio_mmio_physical = mapping->getPhysicalAddress();
  native_virtio_ready = 1;
#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK
  __atomic_fetch_add(&native_virtio_verified_devices, 1, __ATOMIC_RELAXED);
#endif
  native_virtio_read_checkpoint(this);
  IOLog("NativeVirtioBlock: %s, %llu sectors, sector 0 read (%02x%02x%02x%02x...)\n", pci ? "virtio-pci" : "virtio-mmio",
        capacity, native_virtio_first_sector_sha256[0], native_virtio_first_sector_sha256[1],
        native_virtio_first_sector_sha256[2], native_virtio_first_sector_sha256[3]);
  if (!deferRegistration) registerService();
  return true;
}

IOReturn NativeVirtioBlock::doAsyncReadWrite(IOMemoryDescriptor *buffer, UInt64 block, UInt64 count,
                                           IOStorageAttributes *, IOStorageCompletion *completion)
{
  if (!buffer || !completion || !completion->action || !count || block >= capacity ||
      count > capacity - block || count > UINT64_MAX / 512 || buffer->getLength() < count * 512) return kIOReturnBadArgument;
  if (buffer->getDirection() != kIODirectionIn) return kIOReturnNotWritable;
  uint64_t actual = 0;
  IOReturn result = kIOReturnNotReady;
  IOLockLock(gate);
  while (online && actual < count * 512) {
#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK
    constexpr unsigned chunkSize = transferSize - VR_BLOCK_BYTES;
#else
    constexpr unsigned chunkSize = transferSize;
#endif
    uint32_t bytes = static_cast<uint32_t>(count * 512 - actual > chunkSize ? chunkSize : count * 512 - actual);
    result = readSectors(block + actual / 512, bytes);
    if (result) break;
    if (buffer->writeBytes(actual, readBuffer(), bytes) != bytes) {
      result = kIOReturnIOError;
      break;
    }
    actual += bytes;
  }
  IOLockUnlock(gate);
  completion->action(completion->target, completion->parameter, result, actual);
  return kIOReturnSuccess;
}

void NativeVirtioBlock::stop(IOService *provider)
{
  if (!reset()) panic("NativeVirtioBlock: device did not acknowledge DMA reset");
  IOBlockStorageDevice::stop(provider);
}

void NativeVirtioBlock::free()
{
  if (transportMapped() && !reset()) panic("NativeVirtioBlock: refusing to free active DMA buffers");
#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK
  if (verifiedSnapshot) { IOFree(verifiedSnapshot, transferSize); verifiedSnapshot = nullptr; }
  if (verifiedStorage) { kmem_free(kernel_map, verifiedStorage, verifiedStorageBytes); verifiedStorage = 0; }
#endif
  if (queueMemory) { queueMemory->release(); queueMemory = nullptr; }
  if (transferMemory) { transferMemory->release(); transferMemory = nullptr; }
  if (mapping) { mapping->release(); mapping = nullptr; }
  transport.close();
  if (gate) { IOLockFree(gate); gate = nullptr; }
  IOBlockStorageDevice::free();
}
