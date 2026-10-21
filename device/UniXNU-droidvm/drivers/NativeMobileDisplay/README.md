# NativeMobileDisplay (UniXNU-droidvm)

Override of UniXNU NativeMobileDisplay for the virtual machine board: only the Legacy framebuffer path is kept.

- `NativeLegacyDisplay.cpp`, `NativeLegacyRequest.h`: upstream, with the same class and overridden vtable slots, so the
  original IOMobileFramebufferLegacy bindings apply unchanged. One change: the GOP framebuffer (QEMU ramfb, crosvm
  simplefb) is ordinary guest RAM read by the host, so it is mapped write-back (`virt-fb-cache=0` restores the upstream
  uncached mapping).
- `VirtDisplayProbe.cpp`: the board half, trimmed from UniXNU-sheng `package/LegacyDisplayProbe.cpp`. It registers the
  class, makes the GOP nub over the boot framebuffer, attaches it as `/arm-io/gop-display` and starts the display. There
  is no GPU hand-off: every frame takes the CPU copy. The kernel console is paused before the first frame is stored.

Built by `scripts/build-virt-display.py` into `build/native-legacy-display/libNativeLegacyDisplay.late.a`, from the original
bindings recorded in `scripts/data` by `scripts/record-virt-display.py`. Without them the archive only holds empty hooks.

Boot arguments: `offline-mobile-graphics-load=1 offline-legacy-register=1 offline-legacy-phase=4 offline-legacy-gop=1
offline-legacy-pm=1 offline-legacy-validate=1 offline-legacy-transfer=1 offline-legacy-cpu-present=1
offline-legacy-unit-scale=<1..3>`.

Current state: written, not yet run.
