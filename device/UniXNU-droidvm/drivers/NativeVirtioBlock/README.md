# NativeVirtioBlock (UniXNU-droidvm)

Override of UniXNU NativeVirtioBlock (98909eb). The upstream virtio-mmio transport is unchanged; a modern virtio-pci
transport (NativeVirtioPci.h) is used when the provider is the `pci-host-ecam-generic` node. Read-only devices only.

Current state: written, not yet run.
