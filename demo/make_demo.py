"""Builds the synthetic demo call from demo/script.json with macOS `say`.

Writes demo/out/demo.wav (16 kHz stereo, left = me, right = them), demo/out/them.wav (the other
side only, for the live loopback test) and demo/out/demo.truth.json (when each turn starts and ends).
Each spoken turn is trimmed to its first and last audible sample, so the truth times are speech times.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
RATE = 16000


def ffmpeg() -> str:
    for p in (Path.home() / ".callcoach/bin/ffmpeg", Path.home() / ".callrec/bin/ffmpeg"):
        if p.exists():
            return str(p)
    return "ffmpeg"


def trim(audio: np.ndarray, threshold: int = 300) -> np.ndarray:
    voiced = np.flatnonzero(np.abs(audio.astype(np.int32)) > threshold)
    return audio[voiced[0]:voiced[-1] + 1] if len(voiced) else audio


def speak(text: str, voice: str, rate: int, tmp: Path) -> np.ndarray:
    aiff, raw = tmp / "turn.aiff", tmp / "turn.raw"
    subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(aiff), text], check=True)
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(aiff),
                    "-ac", "1", "-ar", str(RATE), "-f", "s16le", str(raw)], check=True)
    return trim(np.frombuffer(raw.read_bytes(), dtype="<i2"))


def write_wav(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(samples.shape[1])
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.ascontiguousarray(samples).tobytes())


def build(script_path: Path, out_dir: Path) -> None:
    script = json.loads(script_path.read_text(encoding="utf-8"))
    voices = script["voices"]
    out_dir.mkdir(parents=True, exist_ok=True)
    pieces, truth, cursor = [], [], script.get("lead_in", 1.0)
    with tempfile.TemporaryDirectory() as tmp:
        for i, turn in enumerate(script["turns"]):
            v = voices[turn["voice"]]
            audio = speak(turn["text"], v["voice"], v["rate"], Path(tmp))
            start, end = cursor, cursor + len(audio) / RATE
            pieces.append((turn["who"], int(round(start * RATE)), audio))
            truth.append({"i": i, "who": turn["who"], "lang": v["lang"], "text": turn["text"],
                          "start": round(start, 3), "end": round(end, 3), "moment": turn.get("moment")})
            cursor = end + turn.get("gap", 0.8)
    total = int(round((cursor + script.get("tail", 2.0)) * RATE))
    stereo = np.zeros((total, 2), dtype=np.int16)
    for who, at, audio in pieces:
        stereo[at:at + len(audio), 0 if who == "me" else 1] = audio
    write_wav(out_dir / "demo.wav", stereo)
    write_wav(out_dir / "them.wav", stereo[:, 1:2])
    (out_dir / "demo.truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{out_dir / 'demo.wav'}: {total / RATE:.1f} s, {len(truth)} turns")


if __name__ == "__main__":
    build(ROOT / "script.json", ROOT / "out")
