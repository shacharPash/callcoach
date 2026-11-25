import numpy as np
import pytest
from audio_helpers import RATE, delayed, speech_like, to_pcm

from callcoach.aec import FRAME, Cancelled, EchoCanceller, StreamClock, cancel_echo
from callcoach.data import ME, THEM, Chunk

STEP = 0.02
STEP_SAMPLES = round(STEP * RATE)


def frames_of(value: int, samples: int) -> bytes:
    return np.full(samples, value, dtype="<i2").tobytes()


def first(frame: bytes) -> int:
    return int(np.frombuffer(frame[:2], dtype="<i2")[0])


class FakeApm:
    """Records which far-end frame went in before each mic frame; the mic comes out as it went in."""

    def __init__(self, fail_after: int | None = None):
        self.calls: list[tuple[str, int]] = []
        self.fail_after = fail_after

    def render(self, frame: bytes) -> None:
        self.calls.append(("render", first(frame)))

    def capture(self, frame: bytes) -> bytes:
        if self.fail_after is not None and len([c for c in self.calls if c[0] == "capture"]) >= self.fail_after:
            raise RuntimeError("apm broke")
        self.calls.append(("capture", first(frame)))
        return frame


def canceller(apm, **kw):
    out: list[Chunk] = []
    c = EchoCanceller(apm, **kw)
    c.attach(out.append)
    return c, out


def numbered(channel: str, k: int, t0: float = 100.0) -> Chunk:
    """The k-th 20 ms chunk: its two 10 ms frames hold the frame numbers 2k+1 and 2k+2."""
    pcm = frames_of(2 * k + 1, FRAME) + frames_of(2 * k + 2, FRAME)
    return Chunk(channel, pcm, t0 + (k + 1) * STEP)


def out_of_line(calls: list[tuple[str, int]]) -> list[int]:
    """The mic frames that went in after a far-end frame other than their own."""
    last_render, wrong = 0, []
    for kind, n in calls:
        if kind == "render":
            last_render = n
        elif last_render != n:
            wrong.append(n)
    return wrong


FIRST_BURST_FRAMES = 2 * 11


def test_the_other_side_passes_straight_on():
    c, out = canceller(FakeApm())
    chunk = numbered(THEM, 0)
    c.put(chunk)
    assert out == [chunk]


def test_each_mic_frame_goes_in_after_the_far_end_of_the_same_moment():
    apm = FakeApm()
    c, out = canceller(apm)
    for k in range(50):
        c.put(numbered(THEM, k))
        c.put(numbered(ME, k))
    assert not out_of_line(apm.calls)
    assert [ch.channel for ch in out].count(ME) == 50


def late_in_bursts(chunks: list[Chunk], seed: int) -> list[Chunk]:
    """The same chunks, held back and then delivered together, the way a busy thread delivers them: every chunk of
    a burst is stamped when the last one comes, and the last one is on time."""
    rng = np.random.default_rng(seed)
    out, k = [], 0
    while k < len(chunks):
        burst = chunks[k:k + int(rng.integers(1, 12))]
        out += [Chunk(c.channel, c.pcm, burst[-1].t_end) for c in burst]
        k += len(burst)
    return out


def in_arrival_order(*streams: list[Chunk]) -> list[Chunk]:
    return sorted((c for s in streams for c in s), key=lambda c: c.t_end)


def test_a_far_end_that_arrives_late_and_in_bursts_lines_up_as_if_it_came_on_time():
    """Only the first burst, before the far end's start is known, goes in out of line."""
    apm = FakeApm()
    c, out = canceller(apm)
    them = late_in_bursts([numbered(THEM, k) for k in range(200)], seed=3)
    mine = [Chunk(ME, ch.pcm, ch.t_end + 0.002) for ch in (numbered(ME, k) for k in range(200))]
    for chunk in in_arrival_order(them, mine):
        c.put(chunk)
    c.flush()
    assert max(out_of_line(apm.calls), default=0) <= FIRST_BURST_FRAMES
    assert [ch.channel for ch in out].count(ME) == 200


def test_a_mic_that_arrives_late_and_in_bursts_lines_up_as_if_it_came_on_time():
    apm = FakeApm()
    c, out = canceller(apm)
    them = [numbered(THEM, k) for k in range(200)]
    mine = late_in_bursts([numbered(ME, k) for k in range(200)], seed=4)
    for chunk in in_arrival_order(them, mine):
        c.put(chunk)
    c.flush()
    assert max(out_of_line(apm.calls), default=0) <= FIRST_BURST_FRAMES
    assert [ch.channel for ch in out].count(ME) == 200


def test_a_clock_counts_the_samples_and_fills_only_real_gaps():
    clock = StreamClock(gap_tolerance_s=0.25)
    assert clock.add(320, 100.02) == 0 and clock.start == pytest.approx(100.0)
    assert clock.add(320, 100.2) == 0
    assert clock.count == 640 and clock.start == pytest.approx(100.0)
    assert clock.add(320, 101.06) == RATE
    assert clock.count == 640 + RATE + 320 and clock.time_of(clock.count) == pytest.approx(101.06)


def test_a_clock_takes_its_start_from_the_earliest_chunks_of_the_last_window():
    clock = StreamClock(window_s=2.0)
    for k in range(50):
        clock.add(320, 100.0 + (k + 1) * STEP + (0.05 if k == 0 else 0.0))
    assert clock.start == pytest.approx(100.0)
    for k in range(50, 400):
        clock.add(320, 100.0 + 0.1 + (k + 1) * STEP)
    assert clock.start == pytest.approx(100.1)
    assert clock.index_at(clock.time_of(1234)) == 1234


def test_mic_chunks_come_out_whole_in_order_with_their_times():
    c, out = canceller(FakeApm())
    sizes = [320, 100, 380, 640, 50]
    t, pcm_in, chunks_in = 100.0, b"", []
    for k in range(25):
        c.put(Chunk(THEM, bytes(2 * STEP_SAMPLES), 100.0 + (k + 1) * STEP))
    for i, n in enumerate(sizes):
        t += n / RATE
        pcm = np.arange(i * 1000, i * 1000 + n, dtype="<i2").tobytes()
        pcm_in += pcm
        chunks_in.append(Chunk(ME, pcm, t))
        c.put(chunks_in[-1])
    c.flush()
    mine = [ch for ch in out if ch.channel == ME]
    assert [(len(ch.pcm), ch.t_end) for ch in mine] == [(len(ch.pcm), ch.t_end) for ch in chunks_in]
    assert b"".join(ch.pcm for ch in mine) == pcm_in


def test_the_mic_waits_for_the_far_end_of_its_moment():
    c, out = canceller(FakeApm())
    for k in range(10):
        c.put(numbered(THEM, k))
    c.put(numbered(ME, 10))
    assert not [ch for ch in out if ch.channel == ME]
    c.put(numbered(THEM, 10))
    assert [ch.t_end for ch in out if ch.channel == ME] == [numbered(ME, 10).t_end]


def test_the_mic_does_not_wait_longer_than_max_wait():
    c, out = canceller(FakeApm(), max_wait_s=0.3)
    for k in range(10):
        c.put(numbered(THEM, k))
    for k in range(50):
        c.put(numbered(ME, k))
    mine = [ch for ch in out if ch.channel == ME]
    assert 30 <= len(mine) <= 36
    c.flush()
    assert len([ch for ch in out if ch.channel == ME]) == 50


def test_without_any_far_end_the_mic_still_comes_out():
    apm = FakeApm()
    c, out = canceller(apm)
    for k in range(40):
        c.put(numbered(ME, k))
    c.flush()
    assert len(out) == 40 and not [k for k, _ in apm.calls if k == "render"]


def test_a_gap_in_the_far_end_is_silence():
    apm = FakeApm()
    c, out = canceller(apm)
    for k in range(5):
        c.put(numbered(THEM, k))
    c.put(numbered(THEM, 60))
    c.put(numbered(ME, 30))
    renders = [n for kind, n in apm.calls if kind == "render"]
    assert renders and renders[-1] == 0


def test_a_long_silence_on_the_far_end_keeps_only_the_buffer():
    c, out = canceller(FakeApm(), buffer_s=5.0)
    c.put(numbered(THEM, 0))
    c.put(numbered(THEM, 60 * 50))
    assert len(c._render) == 5 * RATE * 2


def test_after_a_gap_in_the_mic_the_mic_lines_up_with_the_far_end_again():
    apm = FakeApm()
    c, out = canceller(apm)
    for k in range(10):
        c.put(numbered(THEM, k))
        c.put(numbered(ME, k))
    for k in range(10, 60):
        c.put(numbered(THEM, k))
    for k in range(40, 60):
        c.put(numbered(ME, k))
    c.flush()
    assert not out_of_line(apm.calls)
    assert [ch.t_end for ch in out if ch.channel == ME] == [numbered(ME, k).t_end for k in [*range(10), *range(40, 60)]]


def test_far_end_older_than_keep_is_skipped():
    apm = FakeApm()
    c, out = canceller(apm, keep_s=0.5)
    for k in range(150):
        c.put(numbered(THEM, k))
    c.put(numbered(ME, 149))
    renders = [n for kind, n in apm.calls if kind == "render"]
    assert 48 <= len(renders) <= 52 and renders[-1] == 300


def test_a_failing_canceller_sends_the_mic_on_as_it_is():
    errors = []
    c, out = canceller(FakeApm(fail_after=3), on_error=errors.append)
    pcm_in = b""
    for k in range(10):
        c.put(numbered(THEM, k))
        chunk = numbered(ME, k)
        pcm_in += chunk.pcm
        c.put(chunk)
    assert b"".join(ch.pcm for ch in out if ch.channel == ME) == pcm_in
    assert len(errors) == 1 and "apm broke" in errors[0]


class Source:
    def __init__(self):
        self.sink = None
        self.stopped = False
        self.kind = "helper"

    def start(self, sink):
        self.sink = sink

    def stop(self):
        self.stopped = True

    def check(self, now):
        self.checked = now


def test_a_cancelled_source_is_still_the_source_and_flushes_when_it_stops():
    c, out = canceller(FakeApm())
    source = Source()
    wrapped = Cancelled(source, c)
    engine_sink = []
    wrapped.start(engine_sink.append)
    source.sink(numbered(ME, 0))
    assert not engine_sink
    wrapped.check(5.0)
    assert source.checked == 5.0 and wrapped.kind == "helper" and wrapped.name == "Source"
    wrapped.stop()
    assert source.stopped and [ch.channel for ch in engine_sink] == [ME]


def notes():
    seen = []
    return seen, lambda kind, **fields: seen.append({"kind": kind, **fields})


def test_cancel_echo_can_be_turned_off():
    seen, note = notes()
    sources = [Source(), Source()]
    assert cancel_echo(sources, note, environ={"CALLCOACH_AEC": "0"}, make_apm=FakeApm) is sources
    assert seen == [{"kind": "aec", "state": "off"}]


def test_cancel_echo_without_the_canceller_leaves_the_sources_as_they_are():
    def missing():
        raise ImportError("no livekit")

    seen, note = notes()
    sources = [Source(), Source()]
    assert cancel_echo(sources, note, environ={}, make_apm=missing) is sources
    assert seen[0]["state"] == "unavailable" and "no livekit" in seen[0]["error"]


def test_cancel_echo_puts_one_canceller_between_the_sources_and_the_engine():
    seen, note = notes()
    mic, system = Source(), Source()
    wrapped = cancel_echo([mic, system], note, environ={}, make_apm=FakeApm)
    assert [w.source for w in wrapped] == [mic, system] and wrapped[0].canceller is wrapped[1].canceller
    assert seen == [{"kind": "aec", "state": "on"}]


def db(x: np.ndarray) -> float:
    return 10 * np.log10(np.mean(x.astype(np.float64) ** 2) + 1e-12)


def run_live(mic: np.ndarray, far: np.ndarray, far_in_bursts: bool = False) -> np.ndarray:
    pytest.importorskip("livekit")
    from callcoach.aec import LivekitApm

    c, out = canceller(LivekitApm())
    mic_pcm, far_pcm = to_pcm(mic), to_pcm(far)
    step = 2 * STEP_SAMPLES
    n = len(mic_pcm) // step
    them = [Chunk(THEM, far_pcm[k * step:(k + 1) * step], 100.0 + (k + 1) * STEP) for k in range(n)]
    mine = [Chunk(ME, mic_pcm[k * step:(k + 1) * step], 100.0 + (k + 1) * STEP) for k in range(n)]
    if far_in_bursts:
        them = late_in_bursts(them, seed=9)
    for chunk in in_arrival_order(them, mine):
        c.put(chunk)
    c.flush()
    return np.frombuffer(b"".join(ch.pcm for ch in out if ch.channel == ME), dtype="<i2")


@pytest.mark.parametrize("far_in_bursts", [False, True], ids=["on time", "late and in bursts"])
def test_the_echo_of_the_other_side_is_removed_and_my_voice_stays(far_in_bursts):
    far = np.concatenate([speech_like(8, 1), np.zeros(3 * RATE)])
    mine = np.concatenate([np.zeros(8 * RATE), speech_like(3, 2, level=0.1)])
    echo = delayed(far, 40, 0.5)
    out = run_live(echo + mine, far, far_in_bursts)
    raw = to_pcm(echo + mine)
    raw = np.frombuffer(raw, dtype="<i2")
    settled = slice(4 * RATE, 8 * RATE)
    assert db(raw[settled]) - db(out[settled]) >= 20
    alone = slice(8 * RATE + RATE // 2, 11 * RATE)
    assert abs(db(out[alone]) - db(raw[alone])) <= 3


def test_with_headphones_my_voice_over_the_other_side_is_untouched():
    """No echo reaches the mic: once the other side has talked alone for about ten seconds, the canceller knows,
    and my voice said over theirs keeps its level."""
    far = np.concatenate([speech_like(10, 3), np.zeros(2 * RATE), speech_like(4, 5)])
    mine = np.concatenate([np.zeros(10 * RATE), speech_like(6, 4, level=0.1)])
    out = run_live(mine, far)
    raw = np.frombuffer(to_pcm(mine), dtype="<i2")
    alone, over = slice(10 * RATE + RATE // 2, 12 * RATE), slice(12 * RATE + RATE // 2, 16 * RATE)
    assert abs(db(out[alone]) - db(raw[alone])) <= 3
    assert abs(db(out[over]) - db(raw[over])) <= 3


def test_my_voice_over_the_other_side_on_speakers_stays_and_is_not_gated():
    """Both talk at once, with echo: the echo goes, and my voice loses only a few dB, far above the level gate."""
    far = speech_like(16, 5)
    mine = np.concatenate([np.zeros(10 * RATE), speech_like(6, 6, level=0.1)])
    echo = delayed(far, 60, 0.5)
    out = run_live(echo + mine, far)
    raw_mine = np.frombuffer(to_pcm(mine), dtype="<i2")
    over = slice(10 * RATE + RATE // 2, 16 * RATE)
    assert db(out[over]) - db(raw_mine[over]) >= -8
