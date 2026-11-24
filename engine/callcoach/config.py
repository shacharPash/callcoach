from __future__ import annotations

import os
import shutil
from pathlib import Path

RATE = 16000
SAMPLE_BYTES = 2
BYTES_PER_SECOND = RATE * SAMPLE_BYTES

HOME = Path.home()


def _env_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name) or default).expanduser()


TOOLS_DIR = _env_path("CALLCOACH_TOOLS", HOME / ".callcoach")
DATA_DIR = _env_path("CALLCOACH_DATA", HOME / "CallCoachData")
RUN_DIR = TOOLS_DIR / "run"
DEFAULTS_DIR = Path(__file__).resolve().parents[2] / "defaults"

GLOSSARY_PROMPT = True
# Seconds between two nudges the agent offers on its own; the active level halves it, ask_now ignores it.
# Terms do not wait for it.
CARD_GAP_S = 40.0

MODEL_FILES = {
    "he": "ivrit-whisper-large-v3-turbo.bin",
    "en": "ggml-large-v3-turbo-q8_0.bin",
    "langid": "ggml-base.bin",
    "vad": "ggml-silero-v5.1.2.bin",
}


def model_path(key: str) -> Path:
    return TOOLS_DIR / "models" / MODEL_FILES[key]


FALLBACK_BIN_DIRS = (HOME / ".callrec" / "bin",)


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def tool(name: str) -> str:
    for folder in (TOOLS_DIR / "bin", *FALLBACK_BIN_DIRS):
        if _executable(folder / name):
            return str(folder / name)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(f"{name} not found; run ./setup.sh")
