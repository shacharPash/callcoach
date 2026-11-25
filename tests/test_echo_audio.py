import numpy as np
from audio_helpers import RATE, delayed, speech_like, to_pcm

from callcoach.echo_audio import AudioHistory, LevelGate, echo_score, envelope, level, peak_level


def noise(n, seed=9, level=0.002):
    return np.random.default_rng(seed).standard_normal(n) * level


def test_envelope_has_one_value_per_20_ms():
    assert len(envelope(bytes(2 * RATE))) == 50


def test_speaker_echo_scores_high():
    them = speech_like(4, 1)
    mic = delayed(them, 80, 0.2) + noise(len(them))
    assert echo_score(to_pcm(mic), to_pcm(them)) > 0.8


def test_echo_with_a_long_delay_is_still_found():
    them = speech_like(4, 1)
    mic = delayed(them, 250, 0.2) + noise(len(them))
    assert echo_score(to_pcm(mic), to_pcm(them)) > 0.8


def test_independent_speech_scores_low():
    them = speech_like(4, 1)
    mic = speech_like(4, 2) + noise(len(them))
    assert echo_score(to_pcm(mic), to_pcm(them)) < 0.4


def test_own_speech_over_a_quiet_echo_is_kept():
    them = speech_like(4, 1)
    mic = speech_like(4, 2) + delayed(them, 80, 0.03) + noise(len(them))
    assert echo_score(to_pcm(mic), to_pcm(them)) < 0.65


def test_a_silent_other_side_is_never_echo():
    mic = speech_like(4, 2)
    assert echo_score(to_pcm(mic), bytes(len(mic) * 2)) == 0.0


def test_very_short_segments_are_not_judged():
    assert echo_score(to_pcm(speech_like(0.1, 2)), to_pcm(speech_like(0.1, 1))) == 0.0


def test_history_returns_the_same_wall_time_range():
    h = AudioHistory(seconds=5)
    x = np.arange(16000, dtype="<i2")
    for i in range(50):
        h.add(x[i * 320:(i + 1) * 320].tobytes(), 100.0 + (i + 1) * 0.02)
    got = np.frombuffer(h.slice(100.5, 100.6), dtype="<i2")
    assert len(got) == 1600 and got[0] == 8000 and got[-1] == 9599


def test_history_pads_gaps_and_unknown_time_with_silence():
    h = AudioHistory(seconds=5)
    h.add(np.full(320, 7, dtype="<i2").tobytes(), 100.02)
    h.add(np.full(320, 9, dtype="<i2").tobytes(), 101.02)
    middle = np.frombuffer(h.slice(100.3, 100.4), dtype="<i2")
    before = np.frombuffer(h.slice(90.0, 90.1), dtype="<i2")
    assert not middle.any() and not before.any() and len(before) == 1600


def test_history_keeps_only_the_last_seconds():
    h = AudioHistory(seconds=1)
    for i in range(200):
        h.add(np.full(320, 5, dtype="<i2").tobytes(), 100.0 + (i + 1) * 0.02)
    assert not np.frombuffer(h.slice(100.0, 100.1), dtype="<i2").any()
    assert np.frombuffer(h.slice(103.5, 103.6), dtype="<i2").all()


def padded(x):
    pad = np.zeros(round(0.21 * RATE))
    return np.concatenate([pad, x, pad])


def test_short_own_speech_over_the_other_side_is_not_judged():
    for seconds in (0.3, 0.5):
        for seed in range(100):
            mic = padded(speech_like(seconds, seed))
            them = speech_like(len(mic) / RATE, seed + 1000)
            assert echo_score(to_pcm(mic + noise(len(mic))), to_pcm(them)) == 0.0


def test_own_speech_as_loud_as_the_other_side_is_never_echo():
    for seed in range(100):
        mic = padded(speech_like(0.8, seed))
        them = speech_like(len(mic) / RATE, seed + 1000)
        assert echo_score(to_pcm(mic + noise(len(mic))), to_pcm(them)) == 0.0


def test_tones_that_start_together_at_a_similar_level_are_not_echo():
    mic = padded(np.full(RATE, 4000 / 32768))
    them = padded(np.full(RATE, 8000 / 32768))
    assert echo_score(to_pcm(mic), to_pcm(them)) == 0.0


def test_an_echo_must_be_clearly_quieter_than_the_other_side():
    them = speech_like(4, 1)
    assert echo_score(to_pcm(delayed(them, 80, 0.6)), to_pcm(them)) == 0.0
    assert echo_score(to_pcm(delayed(them, 80, 0.25) + noise(len(them))), to_pcm(them)) > 0.8


def at_db(seconds, db, seed=3):
    """Speech-like audio whose loudest 300 ms (the gate's level) are at db dBFS."""
    x = speech_like(seconds, seed, level=0.05)
    return x * 10 ** ((db - peak_level(to_pcm(x))) / 20)


def test_levels_are_in_dbfs():
    tone = to_pcm(np.full(RATE, 0.1))
    assert abs(level(tone) - -20) < 0.1 and abs(peak_level(tone) - -20) < 0.1
    assert level(bytes(2 * RATE)) == -60.0 and peak_level(bytes(2 * RATE)) == -60.0
    assert level(b"") == -60.0 and peak_level(b"") == -60.0


def test_the_peak_level_finds_a_short_word_in_a_long_quiet_segment():
    pcm = to_pcm(np.concatenate([at_db(3, -55), at_db(0.4, -25, seed=4), at_db(3, -55, seed=5)]))
    assert level(pcm) < -50 and peak_level(pcm) > -28


def test_until_my_voice_is_known_only_very_quiet_audio_over_the_other_side_is_echo():
    gate, them = LevelGate(), to_pcm(at_db(2, -20, seed=1))
    assert gate.judge(to_pcm(at_db(2, -55)), them)[0]
    assert not gate.judge(to_pcm(at_db(2, -45)), them)[0]


def test_my_voice_is_learned_while_the_other_side_is_quiet():
    gate, silence, them = LevelGate(), bytes(4 * RATE), to_pcm(at_db(2, -20, seed=1))
    for seed in range(3):
        assert gate.judge(to_pcm(at_db(2, -30, seed)), silence)[0] is False
    assert abs(gate.limit() - -42) < 1.5
    assert gate.judge(to_pcm(at_db(2, -45)), them)[0]
    assert not gate.judge(to_pcm(at_db(2, -33)), them)[0]


def test_short_sentences_do_not_teach_my_voice():
    gate = LevelGate()
    gate.judge(to_pcm(at_db(0.3, -30)), bytes(RATE))
    assert gate.limit() == -50.0


def test_nothing_is_echo_while_the_other_side_is_quiet():
    gate = LevelGate()
    assert not gate.judge(to_pcm(at_db(2, -58)), bytes(4 * RATE))[0]


def test_a_short_reply_inside_a_long_leftover_of_the_echo_is_kept():
    gate, silence, them = LevelGate(), bytes(4 * RATE), to_pcm(at_db(7, -20, seed=1))
    for seed in range(3):
        gate.judge(to_pcm(at_db(2, -25, seed)), silence)
    leftover = at_db(6, -55, seed=6)
    assert gate.judge(to_pcm(leftover), them)[0]
    leftover[3 * RATE:3 * RATE + RATE * 4 // 10] += at_db(0.4, -25, seed=7)
    assert not gate.judge(to_pcm(leftover), them)[0]
