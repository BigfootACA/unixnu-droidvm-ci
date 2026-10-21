#!/usr/bin/env bash
# Pack a staged ESP directory (scripts/stage-virt-esp.py) into a GPT disk image with one FAT32 ESP.
#
#   make-esp-image.sh ESP_DIR IMAGE [SIZE_MIB]
#
# Needs sgdisk (gptfdisk) and mtools.
set -euo pipefail
ESP=$1
IMAGE=$2
SIZE_MIB=${3:-128}
FIRST=2048                                   # 1 MiB alignment, in 512-byte sectors
TOTAL=$((SIZE_MIB * 2048))
LAST=$((TOTAL - 34 - 1))                     # the backup GPT takes the last 33 sectors
LAST=$(((LAST + 1) / 2048 * 2048 - 1))       # keep the partition a whole number of MiB
SECTORS=$((LAST - FIRST + 1))
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
truncate -s $((SECTORS * 512)) "$WORK/fat.img"
mformat -i "$WORK/fat.img" -F -v UNIXNUESP -T "$SECTORS" -h 64 -s 32 ::
mcopy -s -i "$WORK/fat.img" "$ESP"/* ::/
rm -f "$IMAGE"
truncate -s $((TOTAL * 512)) "$IMAGE"
sgdisk -n "1:$FIRST:$LAST" -t 1:ef00 -c 1:UNIXNUESP "$IMAGE" > /dev/null
dd if="$WORK/fat.img" of="$IMAGE" bs=512 seek="$FIRST" conv=notrunc status=none
sgdisk -v "$IMAGE" | tail -1
echo "$IMAGE: ${SIZE_MIB} MiB, ESP sectors $FIRST-$LAST"
