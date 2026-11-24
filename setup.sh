#!/bin/bash
# CallCoach setup: tools, models, the Python environment, the mic helper and the app. Safe to run again.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
TOOLS="${CALLCOACH_TOOLS:-$HOME/.callcoach}"
CALLREC="$HOME/.callrec"
HF="https://huggingface.co"
UV_VERSION="0.13.7"
UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-aarch64-apple-darwin.tar.gz"
UV_SHA256="81b811b57e4624df7ed3aac148e0359100d9962f3d9dce00aec533dc2e086731"
CALLREC_TOOLS="callrec-tools-1-macos-arm64"
CALLREC_TOOLS_URL="https://github.com/shacharPash/callrec/releases/download/v1.1/$CALLREC_TOOLS.tar.gz"
CALLREC_TOOLS_SHA256="7cc8cdc02a0964cc251f0eeccabcd40740dae6d7f9f7b532e5f8a20d4ae13ffd"
MODELS=(
  "ivrit-whisper-large-v3-turbo.bin|$HF/ivrit-ai/whisper-large-v3-turbo-ggml/resolve/2130c78e4a9cb4914cc4df91a1c3031407789705/ggml-model.bin|c8090411113357097bfafc2b8e228ec1639fa7f5fe4ecb5d054ac0ccef8641b1"
  "ggml-large-v3-turbo-q8_0.bin|$HF/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-large-v3-turbo-q8_0.bin|317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1"
  "ggml-silero-v5.1.2.bin|$HF/ggml-org/whisper-vad/resolve/9ffd54a1e1ee413ddf265af9913beaf518d1639b/ggml-silero-v5.1.2.bin|29940d98d42b91fbd05ce489f3ecf7c72f0a42f027e4875919a28fb4c04ea2cf"
  "ggml-base.bin|$HF/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-base.bin|60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"
)

die() { printf 'Error: %s\n' "$1" >&2; exit 1; }
step() { printf '\n==> %s\n' "$1"; }
sha256_of() { shasum -a 256 "$1" | awk '{print $1}'; }
download() {  # download URL FILE SHA256
  curl -fL --retry 3 -C - --progress-bar -o "$2.part" "$1" || die "Could not download $1"
  [ "$(sha256_of "$2.part")" = "$3" ] || { rm -f "$2.part"; die "$(basename "$2") failed its checksum."; }
  mv -f "$2.part" "$2"
}

[ "$(uname -m)" = "arm64" ] || die "CallCoach needs an Apple Silicon Mac."
xcrun --find swiftc >/dev/null 2>&1 || die "The Command Line Tools are needed: run xcode-select --install"
mkdir -p "$TOOLS/bin" "$TOOLS/models"
export PATH="$TOOLS/bin:$PATH"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

step "uv"
if ! command -v uv >/dev/null || ! command -v uvx >/dev/null; then
  download "$UV_URL" "$WORK/uv.tar.gz" "$UV_SHA256"
  tar -xzf "$WORK/uv.tar.gz" -C "$WORK"
  for t in uv uvx; do
    install -m 755 "$WORK/uv-aarch64-apple-darwin/$t" "$TOOLS/bin/$t"
  done
fi

step "ffmpeg and ffprobe"
if [ -x "$CALLREC/bin/ffmpeg" ] && [ -x "$CALLREC/bin/ffprobe" ]; then
  for t in ffmpeg ffprobe; do ln -sf "$CALLREC/bin/$t" "$TOOLS/bin/$t"; done
elif [ ! -x "$TOOLS/bin/ffmpeg" ] || [ ! -x "$TOOLS/bin/ffprobe" ]; then
  download "$CALLREC_TOOLS_URL" "$WORK/tools.tar.gz" "$CALLREC_TOOLS_SHA256"
  tar -xzf "$WORK/tools.tar.gz" -C "$WORK"
  for t in ffmpeg ffprobe; do
    [ -f "$WORK/$CALLREC_TOOLS/bin/$t" ] || die "The tools archive has no $t."
    install -m 755 "$WORK/$CALLREC_TOOLS/bin/$t" "$TOOLS/bin/$t"
  done
fi

step "Models"
for entry in "${MODELS[@]}"; do
  IFS='|' read -r name url sum <<< "$entry"
  target="$TOOLS/models/$name"
  [ -e "$target" ] && continue
  if [ -f "$CALLREC/models/$name" ]; then
    ln -sf "$CALLREC/models/$name" "$target"
  else
    printf '%s\n' "$name"
    download "$url" "$target" "$sum"
  fi
done

step "whisper-server"
[ -x "$TOOLS/bin/whisper-server" ] || "$ROOT/tools/build-whisper-server.sh" "$TOOLS/bin"

step "Echo-cancelled microphone helper"
"$ROOT/tools/build-mic-helper.sh" "$TOOLS/bin" >/dev/null

step "Python environment"
(cd "$ROOT" && uv sync --frozen)

step "CallCoach.app"
CALLCOACH_BUILD="$TOOLS/build" "$ROOT/app/build.sh"
mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/CallCoach.app"
ditto "$TOOLS/build/CallCoach.app" "$HOME/Applications/CallCoach.app"

printf '\nCallCoach is set up.\n  Tools: %s\n  App: %s\n' "$TOOLS" "$HOME/Applications/CallCoach.app"
printf 'Open the app, then add an account from its menu.\n'
