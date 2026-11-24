#!/bin/bash
# Builds whisper-server from whisper.cpp with the same version and Metal flags as CallRec's
# whisper-cli, into the folder given as $1 (default ~/.callcoach/bin).
# Needs the Xcode Command Line Tools and uv (cmake comes from PyPI through uvx).
set -euo pipefail

DEST="${1:-$HOME/.callcoach/bin}"
MIN_MACOS="14.2"
CMAKE_VERSION="4.4.4"
WHISPER_VERSION="1.9.5"
WHISPER_URL="https://github.com/ggml-org/whisper.cpp/archive/refs/tags/v${WHISPER_VERSION}.tar.gz"
WHISPER_SHA256="ff1a9053feb509ff9d7729703355541ae9690073a6b1c40eb692c962e0dc1720"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$ROOT/build/whisper"
JOBS="$(sysctl -n hw.ncpu 2>/dev/null || echo 8)"

die() { printf 'Error: %s\n' "$1" >&2; exit 1; }
sha256_of() { shasum -a 256 "$1" | awk '{print $1}'; }
cmake() { uvx --quiet --from "cmake==$CMAKE_VERSION" cmake "$@"; }

command -v uvx >/dev/null || die "uv is needed (it provides cmake): https://docs.astral.sh/uv/"
xcode-select -p >/dev/null 2>&1 || die "Install the Xcode Command Line Tools: xcode-select --install"
mkdir -p "$WORK" "$DEST"

TARBALL="$WORK/whisper.cpp-$WHISPER_VERSION.tar.gz"
if [ ! -f "$TARBALL" ] || [ "$(sha256_of "$TARBALL")" != "$WHISPER_SHA256" ]; then
  curl -fL --retry 3 --progress-bar -o "$TARBALL.part" "$WHISPER_URL" || die "Could not download $WHISPER_URL"
  mv -f "$TARBALL.part" "$TARBALL"
fi
[ "$(sha256_of "$TARBALL")" = "$WHISPER_SHA256" ] || { rm -f "$TARBALL"; die "whisper.cpp source failed its checksum"; }

rm -rf "$WORK/src" && mkdir -p "$WORK/src"
tar -xf "$TARBALL" -C "$WORK/src" --strip-components 1
cmake -S "$WORK/src" -B "$WORK/out" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
  -DCMAKE_OSX_ARCHITECTURES=arm64 "-DCMAKE_OSX_DEPLOYMENT_TARGET=$MIN_MACOS" \
  -DGGML_NATIVE=OFF -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON -DGGML_OPENMP=OFF \
  -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=ON -DWHISPER_SDL2=OFF -DWHISPER_CURL=OFF \
  >"$WORK/configure.log" 2>&1 || die "cmake configure failed, see $WORK/configure.log"
cmake --build "$WORK/out" --config Release -j "$JOBS" --target whisper-server >"$WORK/build.log" 2>&1 \
  || die "build failed, see $WORK/build.log"
install -m 755 "$WORK/out/bin/whisper-server" "$DEST/whisper-server"
echo "whisper-server $WHISPER_VERSION -> $DEST/whisper-server"
