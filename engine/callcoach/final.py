"""The final transcript of a finished call: each side of call.m4a transcribed again in full once the call is over,
with the call's names and terms as the prompt. Whisper then hears every sentence with the ones around it, rather than
in the live pieces of about 8 s, so a word cut at the edge of a piece comes out whole, and lines of one speaker that
follow each other are joined into one.

The live lines stay in lines.live.jsonl. lines.jsonl gets the final ones beside the call's marks and notes, so the
call page, transcript.md, a summary or follow-up written later and the copy for the export folder all read them. Each
stretch of a side keeps the language its live lines had. It runs in a process of its own after the call, waits while
another call is recording, and changes nothing when it fails. A call whose mic still had the other side in it (no
echo cancellation) keeps its live lines, which were checked for echo as they came."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from callcoach.config import RATE, RUN_DIR, SAMPLE_BYTES, model_path, tool
from callcoach.data import CHANNELS, ME, THEM, Line
from callcoach.textfilter import clean, repeats
from callcoach.transcriber import glossary_prompt

LIVE_NAME = "lines.live.jsonl"
PIECE_S = 300.0  # longest stretch sent at once, so a call that starts meanwhile waits for one piece at most
JOIN_GAP_S = 2.0  # lines of one speaker this close, with nobody between, are one line
JOIN_MAX_S = 40.0
ECHO_WINDOW_S = 2.0
WAIT_S = 20.0


@dataclass(frozen=True)
class FinalResult:
    ok: bool
    lines: int = 0
    reason: str = ""


def echo_free(folder: Path) -> bool:
    """Whether the mic of this call was free of the other side: CallCoach's echo canceller ran to the end of it
    (events.log notes when it starts and when it stops)."""
    try:
        text = (folder / "events.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    states = []
    for raw in text.splitlines():
        if '"aec"' in raw:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "note" and event.get("kind") == "aec":
                states.append(event.get("state"))
    return "on" in states and "stopped" not in states


def read_entries(path: Path) -> list[dict]:
    entries = []
    for raw in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return entries


def runs(lines: list[dict], started: float, duration: float, default: str) -> list[tuple[float, float, str]]:
    """One side's stretches of one language, in seconds from the start of the recording: each change of language
    between its live lines is put halfway between them. A side with no lines is one stretch in `default`."""
    spans = sorted((e["t_start"] - started, e["speech_end"] - started, e["lang"]) for e in lines)
    if not spans:
        return [(0.0, duration, default)]
    out = [[0.0, duration, spans[0][2]]]
    for (_, end, lang), (start, _, nxt) in zip(spans, spans[1:]):
        if nxt != lang:
            cut = min(max((end + start) / 2, out[-1][0]), duration)
            out[-1][1] = cut
            out.append([cut, duration, nxt])
    return [(a, b, lang) for a, b, lang in out if b > a]


def pieces(stretches: list[tuple[float, float, str]], spans: list[tuple[float, float]],
           longest: float = PIECE_S) -> list[tuple[float, float, str]]:
    """The stretches cut to `longest` at most, each cut at the widest silence between live lines in the last part of
    the piece, so no word is cut in two."""
    out = []
    spans = sorted(spans)
    for a, b, lang in stretches:
        start = a
        while b - start > longest:
            lo, hi = start + longest / 2, start + longest
            gaps = [(s2 - e1, (e1 + s2) / 2) for (_, e1), (s2, _) in zip(spans, spans[1:]) if lo < (e1 + s2) / 2 < hi]
            cut = max(gaps)[1] if gaps else hi
            out.append((start, cut, lang))
            start = cut
        out.append((start, b, lang))
    return out


def channel_pcm(audio: Path, index: int) -> bytes:
    proc = subprocess.run([tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(audio),
                           "-af", f"pan=mono|c0=c{index}", "-ar", str(RATE), "-f", "s16le", "pipe:1"],
                          capture_output=True, stdin=subprocess.DEVNULL, timeout=600, start_new_session=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not read {audio.name}: {proc.stderr.decode(errors='replace')[-300:]}")
    return proc.stdout


def join(segments: list[tuple[float, float, str, str, str]]) -> list[tuple[float, float, str, str, str]]:
    """(start, end, channel, lang, text) in time order, with lines of one speaker in one language that follow each
    other closely made one."""
    out: list[list] = []
    for start, end, channel, lang, text in sorted(segments):
        last = out[-1] if out else None
        if (last and last[2] == channel and last[3] == lang and start - last[1] <= JOIN_GAP_S
                and end - last[0] <= JOIN_MAX_S):
            last[1] = max(last[1], end)
            last[4] = f"{last[4]} {text}"
        else:
            out.append([start, end, channel, lang, text])
    return [tuple(x) for x in out]


def other_call_recording(data: Path, folder: Path) -> bool:
    from callcoach.recorder import recording_in_progress

    calls = data / "calls"
    recent = sorted((p for p in calls.iterdir() if p.is_dir() and p != folder), reverse=True)[:3] if calls.exists() else []
    return any(recording_in_progress(p) for p in recent)


class Servers:
    """One whisper server at a time, for the language of the piece at hand; stopped while another call records."""

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.server = None
        self.lang = None

    def client(self, lang: str, timeout: float):
        from callcoach.whisper import WhisperClient, WhisperServer

        if self.lang != lang:
            self.stop()
            self.server = WhisperServer(f"final-{lang}", model_path(lang), binary=tool("whisper-server"), lang=lang,
                                        vad_model=model_path("vad"), log_dir=self.log_dir, run_dir=RUN_DIR)
            self.server.start()
            self.server.wait_ready()
            self.lang = lang
        return WhisperClient(self.server.url, timeout=timeout)

    def stop(self) -> None:
        if self.server:
            self.server.stop()
        self.server, self.lang = None, None


def final_pass(folder: Path, data: Path, *, servers=None, read_channel: Callable[[Path, int], bytes] = channel_pcm,
               busy: Callable[[], bool] | None = None, sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.time) -> FinalResult:
    """Replaces the call's live lines with the final ones; returns how it went and leaves the call as it was when
    anything fails."""
    from callcoach.store import started_at, update_meta

    audio, live, current = folder / "call.m4a", folder / LIVE_NAME, folder / "lines.jsonl"
    if not audio.exists() or not (live.exists() or current.exists()):
        return FinalResult(False, reason="the call has no recording or no lines")
    if not echo_free(folder):
        return FinalResult(False, reason="the mic was not free of the other side's voice")
    entries = read_entries(live if live.exists() else current)
    lines = [e for e in entries if e.get("kind") == "line"]
    started = started_at(folder)
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    terms = list(meta.get("glossary") or [])
    try:
        terms += [t["term"] for t in json.loads((folder / "terms.json").read_text(encoding="utf-8")) if t.get("term")]
    except (OSError, ValueError, TypeError, KeyError):
        pass
    glossary = tuple(dict.fromkeys(terms))
    prompt = glossary_prompt(glossary)
    langs = [e["lang"] for e in lines]
    default = max(sorted(set(langs)), key=langs.count) if langs else "he"
    busy = busy or (lambda: other_call_recording(data, folder))
    servers = servers or Servers(folder / "logs")
    t0 = clock()
    segments = []
    try:
        work = []
        for index, channel in enumerate(CHANNELS):
            pcm = read_channel(audio, index)
            duration = len(pcm) / SAMPLE_BYTES / RATE
            mine = [e for e in lines if e.get("channel") == channel]
            spans = [(e["t_start"] - started, e["speech_end"] - started) for e in mine]
            work += [(lang, a, b, channel, pcm) for a, b, lang in pieces(runs(mine, started, duration, default), spans)]
        for lang, a, b, channel, pcm in sorted(work, key=lambda w: (w[0] != default, w[0], w[3], w[1])):
            while busy():
                servers.stop()
                sleep(WAIT_S)
            chunk = pcm[int(a * RATE) * SAMPLE_BYTES:int(b * RATE) * SAMPLE_BYTES]
            client = servers.client(lang, timeout=max(120.0, b - a))
            for start, end, text in client.segments(chunk, lang, prompt=prompt):
                text = clean(text, glossary)
                if text:
                    segments.append((a + start, a + end, channel, lang, text))
    except Exception as e:
        return FinalResult(False, reason=f"{type(e).__name__}: {e}"[:300])
    finally:
        servers.stop()
    theirs = [(s, e, t) for s, e, ch, _, t in segments if ch == THEM]
    segments = [seg for seg in segments if seg[2] != ME or not repeats(
        seg[4], [t for s, e, t in theirs if s < seg[1] + ECHO_WINDOW_S and e > seg[0] - ECHO_WINDOW_S])]
    final = [Line(n, n, ch, lang, text, started + a, started + b, started + b)
             for n, (a, b, ch, lang, text) in enumerate(join(segments), 1)]
    if lines and not final:
        return FinalResult(False, reason="the final pass found no speech")
    if not live.exists():
        _replace(live, current.read_text(encoding="utf-8"))
    rows = [e for e in entries if e.get("kind") != "line"] + [{"kind": "line", **line.to_dict()} for line in final]
    _replace(current, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    update_meta(folder, final_transcript={"lines": len(final), "seconds": round(clock() - t0, 1)})
    return FinalResult(True, lines=len(final))


def _replace(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def finish_call(folder: Path, data: Path) -> FinalResult:
    """The final pass, then transcript.md and the export folder's copy again when it changed the lines."""
    from callcoach.export import export_and_mark, export_root
    from callcoach.store import render, update_meta

    result = final_pass(folder, data)
    if result.ok:
        render(folder)
        export_and_mark(folder, export_root())
    else:
        try:
            update_meta(folder, final_transcript={"skipped": result.reason})
        except OSError:
            pass
    return result


def spawn(folder: Path, data: Path) -> None:
    """Starts the final pass of a call that just ended in a process of its own, so the next call can start at once;
    its output goes to logs/final.log in the call's folder."""
    log_dir = folder / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "final.log", "ab") as log:
        subprocess.Popen([sys.executable, "-m", "callcoach", "final", "--call", str(folder), "--data", str(data)],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
