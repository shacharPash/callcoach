"""A side that is not being recorded, as CallRec's Health finds it: the mic as pure digital zero for 15 s (a real mic
always hears some noise, so this is a missing permission or a dead device), or nothing at all from the other side
after 90 s of a call in which I already talked for 15 s (no permission for the system audio; the 15 s keep a waiting
room, where no one plays anything yet, from counting). A side leaves the list as soon as it is heard again.

Only heard audio counts: while the recording is paused or CallCoach does not hear me, my side is not looked at and its
15 s start again, so silence the user asked for is never a warning. Times are the chunks' own, so a replay that runs
faster than the clock gets the same answer as the call did."""
from __future__ import annotations

from callcoach.config import RATE, SAMPLE_BYTES
from callcoach.data import ME, THEM, Chunk

MIC_ZERO_S = 15.0
THEM_UNHEARD_S = 90.0
ME_SPOKE_S = 15.0


def _zero(pcm: bytes) -> bool:
    return pcm.count(0) == len(pcm)


class SilentSides:
    def __init__(self, *, mic_zero_s: float = MIC_ZERO_S, them_unheard_s: float = THEM_UNHEARD_S,
                 me_spoke_s: float = ME_SPOKE_S):
        self.mic_zero_s = mic_zero_s
        self.them_unheard_s = them_unheard_s
        self.me_spoke_s = me_spoke_s
        self.first: float | None = None
        self.last = 0.0
        self.mic_zero_since: float | None = None
        self.mic_last = 0.0
        self.them_heard = False

    def feed(self, chunk: Chunk, heard: bool) -> None:
        """One chunk as it came, and whether it is heard (not paused, and on my side CallCoach hears me)."""
        start = chunk.t_end - len(chunk.pcm) / SAMPLE_BYTES / RATE
        if self.first is None:
            self.first = start
        self.last = max(self.last, chunk.t_end)
        if chunk.channel == THEM:
            if heard and not _zero(chunk.pcm):
                self.them_heard = True
            return
        if not heard or not _zero(chunk.pcm):
            self.mic_zero_since = None
            return
        if self.mic_zero_since is None:
            self.mic_zero_since = start
        self.mic_last = chunk.t_end

    def sides(self, my_speech_s: float) -> list[str]:
        """The sides not being recorded now, mine first; my_speech_s is how long CallCoach heard me talk."""
        found = []
        if self.mic_zero_since is not None and self.mic_last - self.mic_zero_since >= self.mic_zero_s:
            found.append(ME)
        if (not self.them_heard and self.first is not None and self.last - self.first >= self.them_unheard_s
                and my_speech_s >= self.me_spoke_s):
            found.append(THEM)
        return found
