#!/usr/bin/env bash
# Build the Data volume seed of the virtual machine board on a Linux host with sudo (run on .53).
#
#   make-data-seed.sh APFSPROGS_DIR APFS_KO PACKED_RAMDISK_PY OUTPUT_DIR [SIZE_MIB]
#
# APFSPROGS_DIR: a build of github.com/linux-apfs/apfsprogs (mkapfs/mkapfs); APFS_KO: linux-apfs-rw built for the running
# kernel; PACKED_RAMDISK_PY: DarwinBoot/Test/packed_ramdisk.py. OUTPUT_DIR (new) receives data.img (the dense APFS
# container) and scratch-data.img (DBRDISK1 packed, staged as the entry's initrd with ADT --data-ramdisk packed).
#
# The Data volume holds only an empty /private/var directory skeleton (no keys, no user records, no Apple data); iOS
# creates the rest. Its UUID is CONFIG_XNU_OWNER_DATA_VOLUME_UUID of droidvm_defconfig.
set -euo pipefail
APFSPROGS=$1
APFS_KO=$2
PACKER=$3
OUT=$4
SIZE_MIB=${5:-512}
VOLUME_UUID=${VOLUME_UUID:-8748FE31-7E7F-4BFA-A7E2-7A4F5E14D0F6}   # CONFIG_XNU_OWNER_DATA_VOLUME_UUID of the kernel
[ -e "$OUT" ] && { echo "$OUT exists: give a new directory" >&2; exit 1; }
mkdir -p "$OUT"
IMAGE=$OUT/data.img
truncate -s "${SIZE_MIB}M" "$IMAGE"
"$APFSPROGS/mkapfs/mkapfs" -s -L Data -u "$VOLUME_UUID" "$IMAGE"

LOADED=0
if ! grep -q '^apfs ' /proc/modules; then
  sudo insmod "$APFS_KO"
  LOADED=1
fi
LOOP=$(sudo losetup --find --show "$IMAGE")
MNT=$(mktemp -d)
OSM=$(mktemp -d)
cleanup() {
  mountpoint -q "$MNT" && sudo umount "$MNT"
  mountpoint -q "$OSM" && sudo umount "$OSM"
  rmdir -- "$MNT" "$OSM" 2>/dev/null || true
  [ -n "${LOOP:-}" ] && sudo losetup -d "$LOOP" 2>/dev/null || true
  [ -n "${OS_LOOP:-}" ] && sudo losetup -d "$OS_LOOP" 2>/dev/null || true
  [ "$LOADED" = 1 ] && sudo rmmod apfs 2>/dev/null || true
}
trap cleanup EXIT
sudo mount -t apfs -o readwrite "$LOOP" "$MNT"

# OS_IMAGE (the expanded OS disk, its APFS container at OS_OFFSET bytes, default the GPT partition at sector 34): start from
# the OS volume's own initial /private/var, as UniXNU scripts/prepare-owner-data-fixture.py does, with its owners and modes
# (daemons running as their own users need their db/, mobile/ ... directories). staged_system_apps is left out.
if [ -n "${OS_IMAGE:-}" ]; then
  OS_LOOP=$(sudo losetup --find --show --read-only --offset "${OS_OFFSET:-17408}" "$OS_IMAGE")
  sudo mount -t apfs -o ro "$OS_LOOP" "$OSM"
  sudo rsync -aH --exclude=/staged_system_apps "$OSM/private/var/" "$MNT/"
  sudo umount "$OSM"
  sudo losetup -d "$OS_LOOP"
  OS_LOOP=
fi

# /private/var skeleton: the common iOS Data directories (UniXNU scripts/prepare-offline-data.py) plus the usual
# top-level system ones. root:wheel 0755 unless listed in MOBILE (mobile 501:501) or WORLD (1777).
DIRECTORIES=(
  db db/dslocal db/dslocal/nodes db/dslocal/nodes/Default db/dslocal/nodes/Default/users
  db/dslocal/nodes/Default/groups db/dslocal/nodes/Default/config db/timezone
  containers containers/Data containers/Data/System containers/Data/Application containers/Shared
  containers/Shared/SystemGroup
  PersonaVolumes preboot root root/Library root/Library/Preferences tmp Library Library/Preferences
  keybags keybags/backup Keychains logs log run vm folders empty spool audit
  MobileAsset MobileDevice MobileSoftwareUpdate installd networkd protected wireless wireless/Library
  hardware hardware/FactoryData hardware/FactoryData/System hardware/FactoryData/System/Library
  hardware/FactoryData/System/Library/Caches hardware/FactoryData/System/Library/Caches/com.apple.factorydata
  mobile mobile/Library mobile/Library/Preferences mobile/Library/Caches mobile/Library/Logs mobile/Containers
  mobile/Documents mobile/Media mobile/tmp
)
for directory in "${DIRECTORIES[@]}"; do
  sudo mkdir -p "$MNT/$directory"
done
for directory in containers/Bundle containers/Temp; do
  sudo mkdir -p "$MNT/$directory"
done
# PerfPowerService (PowerlogCore PLKQueue) watches PLD_ManagedPreferencePath, /Library/Managed Preferences/mobile, and aborts
# when it cannot open it; MDM profiles normally create it.
sudo mkdir -p "$MNT/Managed Preferences/mobile"
# mobileactivationd compares MobileGestalt BuildVersion/ProductVersion with its data ark and otherwise migrates the data of an
# older release; on an empty Data volume that migration fails and it os_crash()es ("Migration failed."), which blocks lsd and
# SpringBoard on its throttled XPC service. The ark of a device already on this build: no migration due.
DATA_ARK=${DATA_ARK:-0}

# Setup marked as done, as UniXNU scripts/seed-data-skip-setup.py does (docs/guide/skip-setup.md), so the first boot goes
# to SpringBoard instead of the Setup app. SKIP_SETUP=0 keeps Setup.
if [ "${SKIP_SETUP:-1}" = 1 ]; then
  PREFS=$(mktemp -d)
  python3 - "$PREFS" <<'PY'
import plistlib, sys
from pathlib import Path
out = Path(sys.argv[1])
BUILD = '21H461'
prefs = {
    'com.apple.purplebuddy': dict(SetupDone=True, SetupFinishedAllSteps=True, SetupVersion=11, daemonizeNeedsToRun=False),
    'com.apple.purplebuddy.notbackedup': {key: False for key in (
        'ShouldRunDiagnosticsMiniBuddy', 'ShouldRunHSA2MiniBuddy', 'ShouldRunKeychainSyncMiniBuddy', 'ShouldRunMesaMiniBuddy',
        'ShouldRunPaymentMiniBuddy', 'ShouldRunStandardMiniBuddy', 'SilentICDPUpgrade')},
    'com.apple.managedconfiguration.notbackedup': dict(LockdownActivationIndicatesCloudConfigurationAvailable=False),
    'com.apple.springboard': dict(BKNoWatchdogs=True, FBLaunchWatchdogExceptions={'com.apple.purplebuddy': 100.0},
                                  MCForceShowCloudConfigurationUI=False),
    'com.apple.migration': dict(LastSystemVersion=BUILD,
                                DMUserDataDisposition=dict(SyntheticDidUpgrade=False, buildVersion=BUILD)),
}
for name, values in prefs.items():
    (out / f'{name}.plist').write_bytes(plistlib.dumps(values, fmt=plistlib.FMT_BINARY))
PY
  for name in com.apple.purplebuddy com.apple.purplebuddy.notbackedup com.apple.managedconfiguration.notbackedup \
              com.apple.springboard com.apple.migration; do
    sudo install -m 0600 -o 501 -g 501 "$PREFS/$name.plist" "$MNT/mobile/Library/Preferences/$name.plist"
    rm -f -- "$PREFS/$name.plist"
  done
  rmdir -- "$PREFS"
fi
if [ "$DATA_ARK" = 1 ]; then
  ARK=$(mktemp -d)
  python3 - "$ARK/data_ark.plist" <<'PY'
import plistlib, sys
ark = {'-BuildVersion': '21H461', '-ProductVersion': '17.7.11',
       '-PreSydroDataMigrationCompleted': True, '-DataMigrationCompleted': True}
open(sys.argv[1], 'wb').write(plistlib.dumps(ark, fmt=plistlib.FMT_BINARY))
PY
  sudo mkdir -p "$MNT/mobile/Library/mad"
  sudo install -m 0600 -o 0 -g 0 "$ARK/data_ark.plist" "$MNT/mobile/Library/mad/data_ark.plist"
  rm -f -- "$ARK/data_ark.plist"
  rmdir -- "$ARK"
fi
sudo chown -R 501:501 "$MNT/mobile"
sudo chown 0:0 "$MNT/mobile/Library/mad/data_ark.plist" 2>/dev/null || true
sudo chmod 1777 "$MNT/tmp" "$MNT/mobile/tmp"
sudo find "$MNT" -maxdepth 2 | sort | head -80
sync
cleanup
trap - EXIT

python3 "$PACKER" "$IMAGE" "$OUT/scratch-data.img"
ls -la "$OUT"
echo "Data volume UUID $VOLUME_UUID"
