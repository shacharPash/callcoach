from audio_helpers import quiet, tone

from callcoach.data import ME, THEM, Chunk
from callcoach.silence import SilentSides

T0 = 1000.0


def play(sides, me, them, *, seconds, heard=lambda channel, s: True, step=0.5):
    """Feeds both channels in chunks of step seconds, going on from where the last play stopped; me and them say,
    at each chunk's start in seconds of this play, whether that side has sound."""
    start = getattr(sides, "played", T0)
    t = start
    while t < start + seconds - 1e-9:
        at = t - start
        t += step
        for channel, sound in ((ME, me), (THEM, them)):
            pcm = tone(step, 50) if sound(at) else quiet(step)
            sides.feed(Chunk(channel, pcm, t), heard(channel, at))
    sides.played = t


always, never = (lambda s: True), (lambda s: False)


def test_a_mic_of_pure_zero_for_15_seconds_is_my_side_not_recorded():
    sides = SilentSides()
    play(sides, never, always, seconds=14.5)
    assert sides.sides(my_speech_s=0) == []
    play(sides, never, always, seconds=1.0)
    assert sides.sides(my_speech_s=0) == [ME]


def test_any_sound_on_the_mic_starts_the_15_seconds_again_and_takes_my_side_off():
    sides = SilentSides()
    play(sides, never, always, seconds=20)
    assert sides.sides(my_speech_s=0) == [ME]
    play(sides, lambda s: s < 0.5, always, seconds=14)
    assert sides.sides(my_speech_s=0) == []


def test_while_callcoach_does_not_hear_me_my_side_is_not_looked_at():
    sides = SilentSides()
    play(sides, never, always, seconds=30, heard=lambda channel, s: channel == THEM)
    assert sides.sides(my_speech_s=0) == []
    play(sides, never, always, seconds=10)
    assert sides.sides(my_speech_s=0) == []
    play(sides, never, always, seconds=5)
    assert sides.sides(my_speech_s=0) == [ME]


def test_nothing_from_the_other_side_after_90_seconds_in_which_i_talked_is_their_side_not_recorded():
    sides = SilentSides()
    play(sides, always, never, seconds=89.5)
    assert sides.sides(my_speech_s=60) == []
    play(sides, always, never, seconds=1.0)
    assert sides.sides(my_speech_s=60) == [THEM]


def test_a_waiting_room_where_i_did_not_talk_yet_is_no_warning():
    sides = SilentSides()
    play(sides, always, never, seconds=300)
    assert sides.sides(my_speech_s=14) == []
    assert sides.sides(my_speech_s=15) == [THEM]


def test_the_other_side_heard_once_takes_their_side_off_for_the_rest_of_the_call():
    sides = SilentSides()
    play(sides, always, never, seconds=120)
    assert sides.sides(my_speech_s=60) == [THEM]
    play(sides, always, lambda s: s < 0.5, seconds=600)
    assert sides.sides(my_speech_s=300) == []


def test_the_other_side_paused_is_not_heard():
    sides = SilentSides()
    play(sides, always, always, seconds=120, heard=lambda channel, s: False)
    assert sides.sides(my_speech_s=60) == [THEM]


def test_with_a_dead_mic_no_talk_of_mine_is_heard_so_only_my_side_shows():
    sides = SilentSides()
    play(sides, never, never, seconds=300)
    assert sides.sides(my_speech_s=0) == [ME]


def test_a_call_with_no_audio_yet_has_nothing_missing():
    assert SilentSides().sides(my_speech_s=100) == []
