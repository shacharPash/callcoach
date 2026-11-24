"""Token counts and their API price equivalent, per call, per day and per month."""
from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

from callcoach.config import DEFAULTS_DIR
from callcoach.providers import Usage

PRICES_FILE = DEFAULTS_DIR / "prices.json"
KEYS = ("input", "output", "cache_read", "cache_write", "requests")
EMPTY = {**{k: 0 for k in KEYS}, "usd": 0.0}


def load_prices(path: Path = PRICES_FILE) -> dict[str, dict[str, float]]:
    return json.loads(path.read_text(encoding="utf-8"))["models"]


def cost(usage: Usage, price: dict | None) -> float:
    if not price:
        return 0.0
    return (usage.input * price["input"] + usage.output * price["output"] + usage.cache_read * price["cache_read"]
            + usage.cache_write * price["cache_write"]) / 1_000_000


FEATURES = ("live", "explain", "translate", "recap", "terms_now", "screen")


def fresh_tokens(usage: Usage) -> int:
    """The tokens a request added: input, output and cache writes. Cache reads are left out: they are most of every
    live step, re-read from the cache, and cost a fraction of the rest."""
    return usage.input + usage.output + usage.cache_write


class CallUsage:
    def __init__(self):
        self.models: dict[str, Usage] = {}
        self.features: dict[str, int] = {}
        self._lock = threading.Lock()

    def add(self, model: str, usage: Usage) -> None:
        with self._lock:
            self.models[model] = self.models.get(model, Usage()) + usage

    def add_feature(self, feature: str, usage: Usage) -> None:
        """The same usage again, counted under the call feature that asked for it; add is still called for it."""
        with self._lock:
            self.features[feature] = self.features.get(feature, 0) + fresh_tokens(usage)

    def live(self) -> dict:
        """The call so far for the panel: its fresh tokens, and those of each feature; what no feature asked for
        (the opening prep, the account check) is under "other"."""
        with self._lock:
            total = sum(fresh_tokens(u) for u in self.models.values())
            features = {f: n for f, n in self.features.items() if n}
        other = total - sum(features.values())
        return {"tokens": total, "features": {**features, **({"other": other} if other > 0 else {})}}

    def total(self) -> Usage:
        with self._lock:
            return sum(self.models.values(), Usage())

    def summary(self, prices: dict) -> dict:
        with self._lock:
            usd = sum(cost(u, prices.get(m)) for m, u in self.models.items())
        return {**self.total().to_dict(), "usd": round(usd, 6)}

    def save(self, path: Path, prices: dict) -> dict:
        summary = self.summary(prices)
        with self._lock:
            models = {m: u.to_dict() for m, u in self.models.items()}
        path.write_text(json.dumps({"models": models, **summary}, ensure_ascii=False), encoding="utf-8")
        return summary


def _add(a: dict, b: dict) -> dict:
    out = {k: a[k] + int(b.get(k, 0)) for k in KEYS}
    out["usd"] = round(a["usd"] + float(b.get("usd", 0.0)), 6)
    return out


def totals(calls: Path, today: date) -> tuple[dict, dict]:
    day, month = dict(EMPTY), dict(EMPTY)
    if not calls.is_dir():
        return day, month
    for folder in calls.iterdir():
        try:
            when = date.fromisoformat(folder.name[:10])
            saved = json.loads((folder / "usage.json").read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if (when.year, when.month) == (today.year, today.month):
            month = _add(month, saved)
            if when == today:
                day = _add(day, saved)
    return day, month
