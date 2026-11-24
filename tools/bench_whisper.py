"""Times whisper-server on each turn of the demo call: language detection and transcription.

usage: PYTHONPATH=engine uv run python tools/bench_whisper.py [--no-vad]
"""
from __future__ import annotations

import argparse
import json
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

from callcoach.transcriber import ServerTranscriber, default_servers

ROOT = Path(__file__).resolve().parents[1]


def clip(audio: np.ndarray, turn: dict) -> bytes:
    ch = 0 if turn["who"] == "me" else 1
    return audio[int(turn["start"] * 16000):int(turn["end"] * 16000), ch].copy().tobytes()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--no-vad", action="store_true")
    args = p.parse_args()
    truth = json.loads((ROOT / "demo/out/demo.truth.json").read_text(encoding="utf-8"))
    with wave.open(str(ROOT / "demo/out/demo.wav")) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2)
    with tempfile.TemporaryDirectory() as logs:
        t = ServerTranscriber(default_servers(Path(logs), vad=not args.no_vad))
        started = time.monotonic()
        t.start()
        print(f"servers ready in {time.monotonic() - started:.1f} s, whisper VAD {'off' if args.no_vad else 'on'}\n")
        try:
            print("| # | lang | dur s | detect ms | p(he) | p(en) | transcribe ms | text |")
            print("|---|---|---|---|---|---|---|---|")
            for u in truth:
                pcm = clip(audio, u)
                a = time.monotonic()
                probs = t.detect(pcm) or {}
                b = time.monotonic()
                text = t.transcribe(pcm, u["lang"])
                c = time.monotonic()
                print(f"| {u['i']} | {u['lang']} | {u['end'] - u['start']:.1f} | {(b - a) * 1000:.0f} "
                      f"| {probs.get('he', 0):.2f} | {probs.get('en', 0):.2f} | {(c - b) * 1000:.0f} | {text} |")
            he = next(u for u in truth if u["lang"] == "he" and u["end"] - u["start"] > 3)
            en = next(u for u in truth if u["lang"] == "en" and u["end"] - u["start"] > 3)
            jobs = [(clip(audio, he), "he"), (clip(audio, en), "en")]
            a = time.monotonic()
            for pcm, lang in jobs:
                t.transcribe(pcm, lang)
            sequential = time.monotonic() - a
            threads = [threading.Thread(target=t.transcribe, args=job) for job in jobs]
            a = time.monotonic()
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            parallel = time.monotonic() - a
            print(f"\nHebrew + English clip: sequential {sequential * 1000:.0f} ms, parallel {parallel * 1000:.0f} ms")
        finally:
            t.stop()


if __name__ == "__main__":
    main()
