// SPDX-License-Identifier: BSD-2-Clause-Patent
// UniXNU ArmVirtPlatform (532de8c) for UniXNU-droidvm: also publishes the pci-host-ecam-generic bridge.
#include <IOKit/IOPlatformExpert.h>
#include <IOKit/IODeviceTreeSupport.h>
#include <IOKit/IOLib.h>
#include <libkern/c++/OSData.h>
#include <libkern/c++/OSIterator.h>

// Observation counters describe real attached nubs, not initialized drivers.
extern "C" {
volatile uint32_t armvirt_platform_configured = 0;
volatile uint32_t armvirt_platform_child_count = 0;
volatile uint32_t armvirt_platform_cpu_count = 0;
volatile uint32_t armvirt_platform_bus_count = 0;
volatile uint32_t armvirt_platform_defaults_count = 0;
}

class ArmVirtPlatform : public IODTPlatformExpert {
  OSDeclareDefaultStructors (ArmVirtPlatform)
public:
  bool configure (IOService *provider) override;
  const char *deleteList (void) override;
  const char *excludeList (void) override;
private:
  bool createVirtioNubs (IORegistryEntry *bus);
};

OSDefineMetaClassAndStructors (ArmVirtPlatform, IODTPlatformExpert)

const char *ArmVirtPlatform::deleteList (void)
{
  return nullptr;
}

const char *ArmVirtPlatform::excludeList (void)
{
  return nullptr;
}

bool ArmVirtPlatform::createVirtioNubs (IORegistryEntry *bus)
{
  OSIterator *iterator = IODTFindMatchingEntries (bus, kIODTExclusive, nullptr);
  if (iterator == nullptr) { return true; }
  bool result = true;
  IORegistryEntry *entry;
  while ((entry = OSDynamicCast (IORegistryEntry, iterator->getNextObject ())) != nullptr) {
    OSData *compatible = OSDynamicCast (OSData, entry->getProperty ("compatible"));
    if (compatible == nullptr) { continue; }
    // UniXNU-droidvm: virtio-mmio transports, and the generic ECAM host bridge that carries virtio-pci (crosvm).
    bool mmio = (compatible->getLength () == 12) &&
                (memcmp (compatible->getBytesNoCopy (), "virtio,mmio", 12) == 0);
    bool ecam = (compatible->getLength () == 22) &&
                (memcmp (compatible->getBytesNoCopy (), "pci-host-ecam-generic", 22) == 0);
    if (!mmio && !ecam) { continue; }
    IOService *nub = createNub (entry);
    if (nub == nullptr) { result = false; break; }
    if (!nub->attach (this)) { nub->release (); result = false; break; }
    nub->registerService ();
    nub->release ();
  }
  iterator->release ();
  return result;
}

bool ArmVirtPlatform::configure (IOService *provider)
{
  // This platform has no Apple NVRAM controller. Enumerate its real DT nodes
  // without calling the legacy IODT NVRAM initialization path.
  if (!IOPlatformExpert::configure (provider)) {
    return false;
  }
  IORegistryEntry *cpus = provider->childFromPath ("cpus", gIODTPlane);
  IORegistryEntry *bus = provider->childFromPath ("arm-io", gIODTPlane);
  if ((cpus == nullptr) || (bus == nullptr)) {
    if (cpus != nullptr) { cpus->release (); }
    if (bus != nullptr) { bus->release (); }
    return false;
  }
  OSIterator *iterator = IODTFindMatchingEntries (cpus, kIODTExclusive, nullptr);
  cpus->release ();
  if (iterator == nullptr) {
    bus->release ();
    return false;
  }
  bool result = createNubs (this, iterator); // Consumes the iterator.
  if (!result) {
    bus->release ();
    return false;
  }
  IOService *busNub = createNub (bus);
  bool devicesCreated = (busNub != nullptr) && createVirtioNubs (busNub);
  bus->release ();
  if (!devicesCreated) {
    if (busNub != nullptr) { busNub->release (); }
    return false;
  }
  if (!busNub->attach (this)) {
    busNub->release ();
    return false;
  }
  busNub->registerService ();
  busNub->release ();
  IORegistryEntry *defaults = provider->childFromPath ("defaults", gIODTPlane);
  if (defaults == nullptr) {
    return false;
  }
  IOService *defaultsNub = createNub (defaults);
  defaults->release ();
  if (defaultsNub == nullptr) {
    return false;
  }
  if (!defaultsNub->attach (this)) {
    defaultsNub->release ();
    return false;
  }
  defaultsNub->registerService ();
  defaultsNub->release ();
  iterator = getChildIterator (gIOServicePlane);
  if (iterator == nullptr) {
    return false;
  }
  uint32_t count = 0;
  uint32_t cpuCount = 0;
  uint32_t busCount = 0;
  uint32_t defaultsCount = 0;
  IORegistryEntry *child;
  while ((child = OSDynamicCast (IORegistryEntry, iterator->getNextObject ())) != nullptr) {
    count++;
    OSData *type = OSDynamicCast (OSData, child->getProperty ("device_type"));
    if ((type != nullptr) && (type->getLength () == 4) &&
        (memcmp (type->getBytesNoCopy (), "cpu", 4) == 0)) {
      cpuCount++;
    }
    if (strcmp (child->getName (), "arm-io") == 0) {
      busCount++;
    }
    if (strcmp (child->getName (), "defaults") == 0) {
      defaultsCount++;
    }
  }
  iterator->release ();
  armvirt_platform_child_count = count;
  armvirt_platform_cpu_count = cpuCount;
  armvirt_platform_bus_count = busCount;
  armvirt_platform_defaults_count = defaultsCount;
  armvirt_platform_configured = 1;
  registerService ();
  return true;
}
