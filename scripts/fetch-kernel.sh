#!/usr/bin/env bash
# Download and decrypt the kernel binaries of a kernel.yml run.
#
#   fetch-kernel.sh RUN_ID OUTPUT_DIR [DEVICE]
#
# Needs gh and the artifact key in $ARTIFACT_KEY_FILE (default /home/bigfoot/unixnu-droidvm/.ci-artifact-key).
# OUTPUT_DIR receives build/... (unprelinked kernel, archives, records) and kernel/kernel.macho when prelinked.
set -euo pipefail
RUN=$1
OUT=$2
DEVICE=${3:-droidvm}
KEY=${ARTIFACT_KEY_FILE:-/home/bigfoot/unixnu-droidvm/.ci-artifact-key}
REPO=${REPO:-BigfootACA/unixnu-droidvm-ci}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
gh run download "$RUN" --repo "$REPO" -n "binaries-$DEVICE" -D "$WORK"
openssl enc -d -aes-256-cbc -pbkdf2 -pass "file:$KEY" -in "$WORK/binaries.tgz.enc" -out "$WORK/binaries.tgz"
mkdir -p "$OUT"
tar -xzf "$WORK/binaries.tgz" -C "$WORK"
[ -f "$WORK/build.tar" ] && tar -xf "$WORK/build.tar" -C "$OUT"
[ -f "$WORK/kernel.tar" ] && tar -xf "$WORK/kernel.tar" -C "$OUT"
find "$OUT" -maxdepth 5 \( -name 'mach.*' -o -name 'kernel.macho' \) -exec ls -la {} \;
