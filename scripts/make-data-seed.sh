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
VOLUME_UUID=8748FE31-7E7F-4BFA-A7E2-7A4F5E14D0F6
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
cleanup() {
  mountpoint -q "$MNT" && sudo umount "$MNT"
  rmdir -- "$MNT" 2>/dev/null || true
  [ -n "${LOOP:-}" ] && sudo losetup -d "$LOOP" 2>/dev/null || true
  [ "$LOADED" = 1 ] && sudo rmmod apfs 2>/dev/null || true
}
trap cleanup EXIT
sudo mount -t apfs -o readwrite "$LOOP" "$MNT"

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
sudo chown -R 501:501 "$MNT/mobile"
sudo chmod 1777 "$MNT/tmp" "$MNT/mobile/tmp"
sudo find "$MNT" -maxdepth 2 | sort | head -80
sync
cleanup
trap - EXIT

python3 "$PACKER" "$IMAGE" "$OUT/scratch-data.img"
ls -la "$OUT"
echo "Data volume UUID $VOLUME_UUID"
