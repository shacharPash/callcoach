"""The CPU CallCoach's engine uses: this process and everything it started (the whisper servers, the microphone
helper, ffmpeg), as `ps` reports it, measured at most every few seconds so the status line never waits on it."""
from __future__ import annotations

import os
import subprocess
import time
from typing import Callable

SAMPLE_EVERY_S = 5.0


def tree_cpu(root: int, table: str) -> float:
    """The percent of one core used by `root` and its descendants, from `ps -A -o pid=,ppid=,%cpu=` output."""
    children: dict[int, list[int]] = {}
    cpu: dict[int, float] = {}
    for row in table.splitlines():
        parts = row.split()
        if len(parts) != 3:
            continue
        try:
            pid, ppid, share = int(parts[0]), int(parts[1]), float(parts[2].replace(",", "."))
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
        cpu[pid] = share
    total, todo, seen = 0.0, [root], set()
    while todo:
        pid = todo.pop()
        if pid in seen:
            continue
        seen.add(pid)
        total += cpu.get(pid, 0.0)
        todo += children.get(pid, [])
    return total


def ps_table() -> str:
    return subprocess.run(["/bin/ps", "-A", "-o", "pid=,ppid=,%cpu="], capture_output=True, text=True,
                          timeout=2).stdout


class CpuMeter:
    """The engine tree's CPU as a share of the whole Mac (0 to 100), sampled at most every SAMPLE_EVERY_S; None when
    it could not be read."""

    def __init__(self, *, root: int | None = None, read: Callable[[], str] = ps_table,
                 cores: int | None = None, clock: Callable[[], float] = time.monotonic):
        self.root = root or os.getpid()
        self.read = read
        self.cores = max(1, cores or os.cpu_count() or 1)
        self.clock = clock
        self._at = -SAMPLE_EVERY_S
        self._value: float | None = None

    def value(self) -> float | None:
        now = self.clock()
        if now - self._at >= SAMPLE_EVERY_S:
            self._at = now
            try:
                self._value = round(min(100.0, tree_cpu(self.root, self.read()) / self.cores), 1)
            except (OSError, subprocess.SubprocessError):
                self._value = None
        return self._value
