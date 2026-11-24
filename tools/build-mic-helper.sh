#!/bin/bash
# Builds callcoach-mic, the echo-cancelled microphone helper. Needs only the Command Line Tools.
set -euo pipefail
OUT="${1:?usage: build-mic-helper.sh OUT_DIR}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$OUT"
swiftc -O -swift-version 5 -target arm64-apple-macos14.2 \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$HERE/mic-helper/Info.plist" \
  "$HERE/mic-helper/main.swift" -o "$OUT/callcoach-mic"
codesign --force --sign - --identifier app.callcoach.mic "$OUT/callcoach-mic"
echo "$OUT/callcoach-mic"
