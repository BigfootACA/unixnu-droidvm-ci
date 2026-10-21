#!/system/bin/sh
# Boot a disk image with DroidVM's crosvm and edk2-gunyah on the phone, as root (adb shell su -c).
#
#   run-crosvm.sh DISK_IMAGE [SECONDS]
#
# Environment: WORK (/data/local/tmp/unixnu), MEM_MIB (2048), CPUS (1), PROTECTION (without-firmware |
# pseudo-unprotected | none), SWIOTLB_MIB (64), EXTRA (more crosvm arguments).
# Logs: $WORK/serial-sbsa.log (PL011 at 0x6000, the XNU console), $WORK/serial-16550.log (UEFI console at 0x3f8),
# $WORK/crosvm.log. Without --hugepages the host fails guest page faults with ENOMEM once the kernel touches
# its window. The VM is stopped through the control socket: never kill -9 crosvm, it leaks Gunyah memory.
set -u
DISK=$1
SECONDS_TO_RUN=${2:-60}
DROIDVM=/data/data/cn.classfun.droidvm
WORK=${WORK:-/data/local/tmp/unixnu}
MEM_MIB=${MEM_MIB:-2048}
CPUS=${CPUS:-1}
PROTECTION=${PROTECTION:-without-firmware}
SWIOTLB_MIB=${SWIOTLB_MIB:-64}
mkdir -p "$WORK"
if pidof crosvm >/dev/null; then
  echo "crosvm is already running; only one VM at a time" >&2
  exit 1
fi
SOCK=$WORK/crosvm.sock
rm -f "$SOCK" "$WORK/serial-sbsa.log" "$WORK/serial-16550.log"
cp "$DROIDVM/usr/share/droidvm/edk2-gunyah.vars.fd" "$WORK/vars.fd"
case $PROTECTION in
  without-firmware) PROTECT="--protected-vm-without-firmware --swiotlb $SWIOTLB_MIB" ;;
  pseudo-unprotected) PROTECT="--protected-vm-pseudo-unprotected" ;;
  *) PROTECT="" ;;
esac
ulimit -l unlimited
export LD_LIBRARY_PATH=$DROIDVM/usr/lib:$DROIDVM/lib
# shellcheck disable=SC2086
"$DROIDVM/usr/bin/crosvm" --extended-status run --name unixnu --mem "$MEM_MIB" --cpus "$CPUS" \
  --hypervisor gunyah $PROTECT \
  --no-balloon --no-pmu --no-rng --no-smt --no-usb --disable-sandbox \
  --hugepages --prepare-lend-mthp-mode chunked \
  --socket "$SOCK" \
  --block "path=$DISK,lock=false,ro=true" \
  --serial "type=file,hardware=sbsa,num=1,path=$WORK/serial-sbsa.log,console,earlycon" \
  --serial "type=file,hardware=serial,num=1,path=$WORK/serial-16550.log" \
  ${EXTRA:-} \
  "$DROIDVM/usr/share/droidvm/edk2-gunyah.fd" \
  --pflash "path=$WORK/vars.fd,block_size=262144" > "$WORK/crosvm.log" 2>&1 &
PID=$!
elapsed=0
while [ "$elapsed" -lt "$SECONDS_TO_RUN" ] && kill -0 "$PID" 2>/dev/null; do
  sleep 1
  elapsed=$((elapsed + 1))
done
if kill -0 "$PID" 2>/dev/null; then
  "$DROIDVM/usr/bin/crosvm" stop "$SOCK" >/dev/null 2>&1 || kill -TERM "$PID"
  for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
fi
wait "$PID"
echo "== crosvm exit $? after ${elapsed}s; logs in $WORK"
