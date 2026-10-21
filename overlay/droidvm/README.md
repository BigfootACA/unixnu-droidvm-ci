# Overlay of the DroidVM mainline build

`.github/workflows/mainline.yml` builds Droid-VM/UniXNU-droidvm with Droid-VM/UniXNU and copies `patches/xnu/*.patch` of this
directory into the device's `patches/xnu` (with the UniXNU patches beside them). All are default-off diagnostics:

| Patch | Boot argument | Output |
| --- | --- | --- |
| virt-exec-log | `exec-log=1` | `exec: pid N name` / `exit: pid N name status X reason ...` on the console |
| virt-crash-log | `crash-log=1` | user backtrace of every fatal exception (`scripts/crash-symbolize.py`) |
| virt-stackshot-dump | `stackshot-dump=<s> [stackshot-dump-period=<s>]` | in-kernel stackshots as base64 on the serial port (`scripts/stackshot-from-serial.py`, `stackshot-summary.py`) |
| virt-pl011-uart | (ADT `uart0` PL011 node) | XNU serial console on a PL011 |
| virt-vc-text | `vc-text=1` | kernel text console on the framebuffer |

`stackshot-dump` stops the other cores with the debugger cross-call. ArmCpu leaves a secondary core SIGPdisabled until it took its
first IPI, and `cpu_signal()` skips such a core silently, so on an SMP guest also pass `armcpu-self-ipi=1` (QEMU/KVM GICv3);
otherwise a core keeps running during the snapshot and the DEVELOPMENT consistency checks of kern_stackshot.c panic.
