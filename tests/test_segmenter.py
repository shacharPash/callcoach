import itertools

import pytest
from audio_helpers import loud, quiet, tone

from callcoach.segmenter import FRAME_BYTES, Segmenter


def run(pcm, chunk=1000, t0=0.0, **kw):
    seg = Segmenter("them", loud, itertools.count(1), **kw)
    out, pos = [], 0
    while pos < len(pcm):
        part = pcm[pos:pos + chunk]
        pos += len(part)
        out += seg.feed(part, t0 + pos / 32000)
    last = seg.flush(t0 + len(pcm) / 32000)
    return out + ([last] if last else [])


def test_one_utterance_closes_600_ms_after_speech():
    segs = run(quiet(0.5) + tone(1.0) + quiet(1.0))
    assert len(segs) == 1
    s = segs[0]
    assert abs(s.speech_end - 1.5) < 0.04
    assert abs(s.closed_at - 2.1) < 0.04
    assert 1.0 <= s.duration <= 1.5


def test_short_noise_is_dropped():
    assert run(quiet(0.5) + tone(0.1) + quiet(1.0)) == []


def test_short_pause_keeps_one_segment():
    assert len(run(tone(1) + quiet(0.3) + tone(1) + quiet(1))) == 1


def test_long_pause_splits():
    assert len(run(tone(1) + quiet(0.8) + tone(1) + quiet(1))) == 2


def test_long_speech_is_cut_at_8_seconds():
    segs = run(tone(15) + quiet(1))
    assert len(segs) == 2
    assert abs(segs[0].duration - 8.0) < 0.05
    assert segs[0].closed_at <= 8.05


def test_ids_are_shared_between_channels():
    ids = itertools.count(1)
    a, b = Segmenter("me", loud, ids), Segmenter("them", loud, ids)
    got = a.feed(tone(1) + quiet(1), 2.0) + b.feed(tone(1) + quiet(1), 2.0)
    assert [s.id for s in got] == [1, 2]


def test_chunk_size_does_not_change_the_result():
    pcm = quiet(0.5) + tone(1.0) + quiet(1.0)
    a, b = run(pcm, chunk=FRAME_BYTES), run(pcm, chunk=777)
    key = lambda segs: [(round(s.speech_end, 2), len(s.pcm)) for s in segs]
    assert key(a) == key(b)


FRAME = FRAME_BYTES


def test_timing_is_exact_on_frame_boundaries():
    segs = run(quiet(0.6) + tone(0.9) + quiet(1.2), chunk=FRAME)
    assert len(segs) == 1
    s = segs[0]
    assert s.speech_end == pytest.approx(1.5, abs=1e-9)
    assert s.closed_at == pytest.approx(2.1, abs=1e-9)
    assert s.t_start == pytest.approx(0.39, abs=1e-9)
    assert len(s.pcm) == (7 + 30 + 7) * FRAME


def test_speech_at_stream_start_has_no_preroll():
    s = run(tone(0.9) + quiet(1.0), chunk=FRAME)[0]
    assert s.t_start == pytest.approx(0.0, abs=1e-9)
    assert len(s.pcm) == (30 + 7) * FRAME


def test_flush_closes_at_the_given_time_and_keeps_the_tail():
    seg = Segmenter("me", loud, itertools.count(1))
    assert seg.feed(tone(0.9) + quiet(0.3), 1.2) == []
    s = seg.flush(1.2)
    assert s.closed_at == 1.2
    assert s.speech_end == pytest.approx(0.9, abs=1e-9)
    assert len(s.pcm) == (30 + 7) * FRAME
    assert seg.flush(1.3) is None


def test_the_12_second_cut_loses_no_audio():
    segs = run(tone(15) + quiet(1), chunk=FRAME)
    assert segs[1].t_start == pytest.approx(segs[0].t_start + segs[0].duration, abs=1e-9)
    assert segs[0].duration + segs[1].duration == pytest.approx(15.0 + 0.21, abs=1e-9)


def test_the_open_segment_can_be_read_while_it_grows():
    seg = Segmenter("them", loud, itertools.count(1))
    pcm = quiet(0.3) + tone(3.0) + quiet(0.3)
    assert seg.feed(pcm, len(pcm) / 32000) == []
    assert seg.active and 3.0 <= seg.spoken_seconds <= 3.3
    assert len(seg.open_audio(1.0)) == FRAME_BYTES * 33
    assert seg.open_audio(10.0) == b"".join(seg.frames)
    seg.flush(len(pcm) / 32000)
    assert seg.spoken_seconds == 0.0 and seg.open_audio(1.0) == b""


def test_discard_drops_the_open_segment_and_what_came_before():
    seg = Segmenter("me", loud, itertools.count(1))
    assert seg.feed(quiet(0.3) + tone(0.5), 0.8) == [] and seg.active and seg.heard_at is not None
    seg.discard()
    assert not seg.active and seg.heard_at is None and not seg.preroll and seg.buffer == b""
    assert seg.feed(quiet(1.0), 1.8) == [] and seg.flush(1.8) is None


def test_after_discard_the_next_sentence_starts_fresh():
    seg = Segmenter("me", loud, itertools.count(1))
    seg.feed(tone(0.5), 0.5)
    seg.discard()
    out = seg.feed(tone(0.6) + quiet(1.0), 2.1)
    [s] = out
    assert s.t_start >= 0.5 - 0.031 and len(s.pcm) < len(tone(0.6) + quiet(0.3))


def test_heard_at_is_the_time_of_the_last_speech():
    seg = Segmenter("me", loud, itertools.count(1))
    seg.feed(quiet(0.3) + tone(0.6), 0.9)
    assert abs(seg.heard_at - 0.9) < 0.031
    seg.feed(quiet(0.3), 1.2)
    assert abs(seg.heard_at - 0.9) < 0.031
