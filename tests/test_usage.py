import json
import threading
from datetime import date

from callcoach.providers import Usage
from callcoach.usage import EMPTY, CallUsage, cost, load_prices, totals

PRICES = {"h": {"input": 1.0, "output": 2.0, "cache_read": 0.1, "cache_write": 1.25}}


def test_cost_counts_every_kind_of_token():
    assert round(cost(Usage(1_000_000, 500_000, 2_000_000, 400_000, 3), PRICES["h"]), 6) == 1 + 1 + 0.2 + 0.5


def test_an_unknown_model_costs_nothing():
    assert cost(Usage(5, 5, 5, 5, 1), None) == 0.0


def test_a_call_adds_up_across_models_and_threads():
    u = CallUsage()
    threads = [threading.Thread(target=lambda: [u.add("h", Usage(1, 1, 0, 0, 1)) for _ in range(100)])
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    u.add("other", Usage(10, 0, 0, 0, 1))
    assert u.total() == Usage(410, 400, 0, 0, 401)
    summary = u.summary(PRICES)
    assert summary["requests"] == 401 and summary["usd"] == round((400 * 1 + 400 * 2) / 1_000_000, 6)


def test_saved_calls_are_summed_by_day_and_month(tmp_path):
    for name, n in (("2026-11-15_1400_a", 1), ("2026-11-15_1500_b", 2), ("2026-11-02_0900_c", 4),
                    ("2026-10-30_0900_d", 8)):
        folder = tmp_path / name
        folder.mkdir()
        u = CallUsage()
        u.add("h", Usage(n, 0, 0, 0, 1))
        u.save(folder / "usage.json", PRICES)
    (tmp_path / "2026-11-15_1600_broken").mkdir()
    (tmp_path / "2026-11-15_1600_broken" / "usage.json").write_text("{", encoding="utf-8")
    today, month = totals(tmp_path, date(2026, 11, 15))
    assert today["input"] == 3 and today["requests"] == 2
    assert month["input"] == 7 and month["requests"] == 3


def test_no_calls_means_zero(tmp_path):
    assert totals(tmp_path / "missing", date(2026, 11, 15)) == (EMPTY, EMPTY)


def test_the_pinned_prices_load():
    prices = load_prices()
    assert prices["claude-haiku-5-5"]["input"] == 0.10 and prices["claude-sonnet-5-5"]["cache_read"] == 0.10


def test_saved_usage_keeps_the_models(tmp_path):
    u = CallUsage()
    u.add("h", Usage(1, 2, 3, 4, 1))
    u.save(tmp_path / "usage.json", PRICES)
    saved = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert saved["models"]["h"] == {"input": 1, "output": 2, "cache_read": 3, "cache_write": 4, "requests": 1}


def test_the_live_tokens_are_split_by_feature_without_cache_reads():
    from callcoach.providers import Usage
    from callcoach.usage import CallUsage

    u = CallUsage()
    step = Usage(input=10, output=20, cache_read=5000, cache_write=70, requests=1)
    u.add("m", step)
    u.add_feature("live", step)
    u.add("m", Usage(input=5, output=5, requests=1))
    assert u.live() == {"tokens": 110, "features": {"live": 100, "other": 10}}
    assert CallUsage().live() == {"tokens": 0, "features": {}}
