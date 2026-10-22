#!/usr/bin/env bash
# Build the Data volume seed of a macOS root for the virtual machine board on a Linux host with sudo (run on .53).
#
#   make-macos-data-seed.sh APFSPROGS_DIR APFS_KO PACKED_RAMDISK_PY OS_IMAGE OUTPUT_DIR [SIZE_MIB]
#
# Same container and packing as make-data-seed.sh (the owner Data mount puts it at /private/var), but case-insensitive like
# a macOS Data volume and filled from the macOS installer's template, /System/Library/Templates/Data of the OS volume
# (OS_IMAGE, the expanded OS disk, APFS container at OS_OFFSET bytes, default the 23G93 GPT partition at sector 409634):
# private/var at the root, and under .data the writable firmlink targets that make-macos-systemos.sh links to
# (Library, Users, Applications, Volumes, cores, opt, usr/local, System/Library/Caches, private/etc).
set -euo pipefail
APFSPROGS=$1
APFS_KO=$2
PACKER=$3
OS_IMAGE=$4
OUT=$5
SIZE_MIB=${6:-1024}
VOLUME_UUID=${VOLUME_UUID:-8748FE31-7E7F-4BFA-A7E2-7A4F5E14D0F6}   # CONFIG_XNU_OWNER_DATA_VOLUME_UUID of the kernel
[ -e "$OUT" ] && { echo "$OUT exists: give a new directory" >&2; exit 1; }
mkdir -p "$OUT"
IMAGE=$OUT/data.img
truncate -s "${SIZE_MIB}M" "$IMAGE"
"$APFSPROGS/mkapfs/mkapfs" -L Data -u "$VOLUME_UUID" "$IMAGE"

LOADED=0
if ! grep -q '^apfs ' /proc/modules; then
  sudo insmod "$APFS_KO"
  LOADED=1
fi
LOOP=$(sudo losetup --find --show "$IMAGE")
OS_LOOP=$(sudo losetup --find --show --read-only --offset "${OS_OFFSET:-209732608}" "$OS_IMAGE")
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
sudo mount -t apfs -o ro "$OS_LOOP" "$OSM"
TEMPLATE=$OSM/System/Library/Templates/Data
sudo rsync -aH "$TEMPLATE/private/var/" "$MNT/"
for directory in tmp log run vm folders db/dslocal/nodes/Default; do
  sudo mkdir -p "$MNT/$directory"
done
sudo chmod 1777 "$MNT/tmp"
# The bulky read-only parts of /Library stay on the SystemOS disk (make-macos-systemos.sh, under /System/Volumes/Data):
# DarwinBoot's packed RAM disk holds at most 256 MiB of nonzero pages.
READONLY=(Library/Apple "Library/Application Support/Apple" Library/Documentation Library/WebServer)
for path in Library Users Applications Volumes cores opt usr/local System/Library/Caches private/etc; do
  sudo mkdir -p "$MNT/.data/$(dirname "$path")"
  if [ -e "$TEMPLATE/$path" ]; then
    sudo rsync -aH "${READONLY[@]/#/--exclude=/}" "$TEMPLATE/$path" "$MNT/.data/$(dirname "$path")/"
  else
    sudo mkdir -p "$MNT/.data/$path"
  fi
done
for path in "${READONLY[@]}"; do
  sudo ln -s "/System/Volumes/Data/$path" "$MNT/.data/$path"
done
sudo chmod 1777 "$MNT/.data/Volumes" 2>/dev/null || true
sudo find "$MNT" -maxdepth 2 | sort | head -80
sync
cleanup
trap - EXIT

python3 "$PACKER" "$IMAGE" "$OUT/scratch-data.img"
ls -la "$OUT"
echo "Data volume UUID $VOLUME_UUID"
