#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
OUT="${CALLCOACH_BUILD:-$ROOT/build}"
APP="$OUT/CallCoach.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -swift-version 5 -parse-as-library -target arm64-apple-macos14.2 ./*.swift -o "$APP/Contents/MacOS/CallCoach"
cp Info.plist "$APP/Contents/Info.plist"
printf '%s\n' "$ROOT" > "$APP/Contents/Resources/root.txt"
ICONSET="$OUT/AppIcon.iconset"
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
swift "$ROOT/tools/app-icon.swift" "$OUT/icon.png" >/dev/null
for s in 16 32 128 256 512; do
  sips -z $s $s "$OUT/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$OUT/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$ICONSET" "$OUT/icon.png"
# Ad hoc signing gives each build a new code hash, and macOS forgets the microphone and system audio
# permissions with it. Requiring only the identifier keeps them across rebuilds.
codesign --force --sign - --identifier app.callcoach --requirements '=designated => identifier "app.callcoach"' "$APP"
"$APP/Contents/MacOS/CallCoach" --self-check
echo "built $APP"
