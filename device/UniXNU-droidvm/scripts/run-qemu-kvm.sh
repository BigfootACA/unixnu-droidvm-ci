#!/usr/bin/env bash
# Boot a staged ESP (scripts/stage-virt-esp.py) in QEMU virt with KVM on an ARM64 Linux host.
#
#   run-qemu-kvm.sh ESP_DIR [LOG] [-- extra qemu arguments]
#
# highmem=off keeps the PCIe ECAM (0x3f000000) and the BARs below 4 GiB, where the ADT describes them.
# Environment: CPUS (1), MEM_MIB (2048), TIMEOUT (seconds, 120), ACCEL (kvm), CPU (host), FIRMWARE (AAVMF code image),
# SCREENSHOT (PPM path) with SCREENSHOT_AT (seconds after start, comma separated: one file per time, suffixed);
# each screenshot also saves the vCPU registers (monitor info registers -a) beside it as .regs;
# PANIC_STOP (1): stop QEMU shortly after an XNU panic instead of waiting for TIMEOUT.
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
MONITOR=(-monitor none)
if [ -n "${SCREENSHOT:-}" ]; then
  SOCKET=$WORK/monitor.sock
  rm -f "$SOCKET"
  MONITOR=(-monitor "unix:$SOCKET,server=on,wait=off")
  python3 - "$SOCKET" "$SCREENSHOT" "${SCREENSHOT_AT:-60}" <<'PY' &
import socket, sys, time
sock, path, times = sys.argv[1], sys.argv[2], [float(t) for t in sys.argv[3].split(',')]
start = time.time()
for index, at in enumerate(times):
    time.sleep(max(0, start + at - time.time()))
    target = path if len(times) == 1 else path.replace('.ppm', f'-{int(at)}s.ppm')
    try:
        s = socket.socket(socket.AF_UNIX); s.connect(sock)
        s.settimeout(3)
        s.sendall(f'screendump {target}\n'.encode()); time.sleep(1)
        s.sendall(b'info registers -a\n'); time.sleep(1)
        registers = b''
        try:
            while chunk := s.recv(65536):
                registers += chunk
        except OSError:
            pass
        s.close()
        with open(target.replace('.ppm', '.regs'), 'wb') as out:
            out.write(registers)
    except OSError as error:
        print('screenshot failed:', error, file=sys.stderr)
PY
fi
# A panicked XNU spins forever: stop QEMU a few seconds after the panic banner (PANIC_STOP=0 keeps it running).
if [ "${PANIC_STOP:-1}" = 1 ]; then
  (
    while sleep 2; do
      pid=$(pgrep -n -f "logfile=$LOG" || true)
      if grep -aq 'Please go to https://panic.apple.com' "$LOG" 2>/dev/null; then
        sleep 5
        [ -n "$pid" ] && kill "$pid" 2>/dev/null
        break
      fi
      [ -z "$pid" ] && [ -s "$LOG" ] && break
    done
  ) &
fi
set +e
timeout --foreground "$TIMEOUT" qemu-system-aarch64 \
  -M virt,gic-version=3,virtualization=off,secure=off,highmem=off -accel "$ACCEL" -cpu "$CPU" \
  -m "$MEM_MIB" -smp "$CPUS" -display none "${MONITOR[@]}" -nic none -no-reboot \
  -chardev stdio,id=ser0,logfile="$LOG",signal=off -serial chardev:ser0 \
  -drive if=pflash,format=raw,readonly=on,file="$CODE" \
  -drive if=pflash,format=raw,file="$VARS" \
  -drive if=none,id=esp,format=raw,readonly=on,"$ESP_DRIVE" -device virtio-blk-pci,drive=esp \
  "$@" < /dev/null
status=$?
set -e
echo "== qemu exit $status (124 = timeout); log $LOG"
