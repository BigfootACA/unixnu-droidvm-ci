#!/usr/bin/env bash
# Boot a staged ESP (scripts/stage-virt-esp.py) in QEMU virt with KVM on an ARM64 Linux host.
#
#   run-qemu-kvm.sh ESP_DIR [LOG] [-- extra qemu arguments]
#
# Environment: CPUS (1), MEM_MIB (2048), TIMEOUT (seconds, 120), ACCEL (kvm), CPU (host), FIRMWARE (AAVMF code image).
# The serial console goes to LOG (default ESP_DIR/../serial.log) and to stdout.
set -euo pipefail
ESP=$(realpath "$1")
LOG=${2:-$(dirname "$ESP")/serial.log}
shift $(( $# >= 2 ? 2 : 1 ))
[ "${1:-}" = -- ] && shift
CPUS=${CPUS:-1}
MEM_MIB=${MEM_MIB:-2048}
TIMEOUT=${TIMEOUT:-120}
ACCEL=${ACCEL:-kvm}
CPU=${CPU:-host}
for candidate in "${FIRMWARE:-}" /usr/share/edk2/aarch64/QEMU_CODE.fd /usr/share/AAVMF/AAVMF_CODE.fd; do
  if [ -n "$candidate" ] && [ -f "$candidate" ]; then FIRMWARE=$candidate; break; fi
done
WORK=$(dirname "$ESP")
VARS=$WORK/vars.fd
truncate -s 64M "$VARS"
CODE=$WORK/code.fd
cp "$FIRMWARE" "$CODE"
truncate -s 64M "$CODE"
: > "$LOG"
if [ -d "$ESP" ]; then
  ESP_DRIVE="file=fat:$ESP"
else
  ESP_DRIVE="file=$ESP"                      # a disk image (scripts/make-esp-image.sh)
fi
set +e
timeout --foreground "$TIMEOUT" qemu-system-aarch64 \
  -M virt,gic-version=3,virtualization=off,secure=off -accel "$ACCEL" -cpu "$CPU" \
  -m "$MEM_MIB" -smp "$CPUS" -display none -monitor none -nic none -no-reboot \
  -chardev stdio,id=ser0,logfile="$LOG",signal=off -serial chardev:ser0 \
  -drive if=pflash,format=raw,readonly=on,file="$CODE" \
  -drive if=pflash,format=raw,file="$VARS" \
  -drive if=none,id=esp,format=raw,readonly=on,"$ESP_DRIVE" -device virtio-blk-pci,drive=esp \
  "$@" < /dev/null
status=$?
set -e
echo "== qemu exit $status (124 = timeout); log $LOG"
