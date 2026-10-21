#!/usr/bin/env bash
# Download and decrypt the kernel of a kernel.yml run.
#
#   fetch-kernel.sh RUN_ID OUTPUT_DIR [DEVICE] [kernel|build]
#
# kernel (default): OUTPUT_DIR/kernel/kernel.macho and kernel.json (artifact kernel-DEVICE).
# build: OUTPUT_DIR/build/... archives, records and the unprelinked kernel (artifact build-DEVICE).
# Needs gh and the artifact key in $ARTIFACT_KEY_FILE (default /home/bigfoot/unixnu-droidvm/.ci-artifact-key).
# The download goes through curl with resume (-C -), since the artifact store can be slow.
set -euo pipefail
RUN=$1
OUT=$2
DEVICE=${3:-droidvm}
WHAT=${4:-kernel}
KEY=${ARTIFACT_KEY_FILE:-/home/bigfoot/unixnu-droidvm/.ci-artifact-key}
REPO=${REPO:-BigfootACA/unixnu-droidvm-ci}
# The LAN proxy is about 4x faster than the direct path to the artifact store; FETCH_PROXY= disables it.
PROXY=${FETCH_PROXY-http://192.168.0.253:8080}
WORK=$(mktemp -d)
cleanup() {
  # Only the files this script creates, one by one, then the (empty) directory.
  for name in artifact.zip "$WHAT.tgz.enc" "$WHAT.tgz"; do
    if [ -f "$WORK/$name" ]; then rm -f -- "$WORK/$name"; fi
  done
  rmdir -- "$WORK" 2>/dev/null || echo "fetch-kernel: left $WORK (unexpected files)" >&2
}
trap cleanup EXIT
ID=$(gh api "repos/$REPO/actions/runs/$RUN/artifacts" --jq ".artifacts[] | select(.name==\"$WHAT-$DEVICE\") | .id")
[ -n "$ID" ] || { echo "no artifact $WHAT-$DEVICE in run $RUN" >&2; exit 1; }
for attempt in 1 2 3 4 5 6; do
  URL=$(curl -sS -o /dev/null -w '%{redirect_url}' -H "Authorization: Bearer $(gh auth token)" \
        "https://api.github.com/repos/$REPO/actions/artifacts/$ID/zip")
  if curl -sS -C - ${PROXY:+-x "$PROXY"} -o "$WORK/artifact.zip" "$URL"; then break; fi
  echo "fetch-kernel: download interrupted, resuming ($attempt)" >&2
done
unzip -q -o "$WORK/artifact.zip" "$WHAT.tgz.enc" -d "$WORK"
openssl enc -d -aes-256-cbc -pbkdf2 -pass "file:$KEY" -in "$WORK/$WHAT.tgz.enc" -out "$WORK/$WHAT.tgz"
mkdir -p "$OUT"
tar -xzf "$WORK/$WHAT.tgz" -C "$OUT"
find "$OUT" -maxdepth 5 \( -name 'mach.*' -o -name 'kernel.macho' \) -exec ls -la {} \;
