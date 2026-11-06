"""The trade journal: the owner's trades, graded the desk's way."""
from __future__ import annotations

from pathlib import Path

import pytest

from desk.journal import Journal


@pytest.fixture()
def j(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "journal.db")


def test_add_requires_a_stop_to_be_gradeable(j: Journal) -> None:
    tid = j.add("XAUUSD", "long", 4063.6, 4055.3, 4083.0, note="retest")
    t = j.all_trades()[0]
    assert tid == 1
    assert t.risk == pytest.approx(8.3)
    assert t.planned_r == pytest.approx(2.34, abs=0.01)
    assert t.status == "open"


def test_first_touch_wins_and_a_bar_spanning_both_takes_the_stop(j: Journal) -> None:
    j.add("XAUUSD", "long", 4063.6, 4055.3, 4083.0, opened_at="2026-10-31T00:00:00Z")
    j.add("XAUUSD", "short", 4090.0, 4100.0, 4060.0, opened_at="2026-10-31T00:00:00Z")
    bars = [
        {"open_time": "2026-11-01T00:00:00Z", "high": 4092, "low": 4088},
        # this bar spans both the stop and the target: the stop must win
        {"open_time": "2026-11-01T01:00:00Z", "high": 4101, "low": 4055},
    ]
    n = j.grade({"1h": bars}, tf="1h")
    assert n == 2
    long_t, short_t = j.all_trades()
    assert long_t.result == "target" and long_t.r_multiple == pytest.approx(2.34, abs=0.01)
    # the short's second bar touches both; the conservative reading is the stop
    assert short_t.result == "stop" and short_t.r_multiple == -1.0


def test_an_unresolved_trade_is_never_counted_as_a_win(j: Journal) -> None:
    j.add("XAUUSD", "long", 4063.6, 4055.3, 4083.0, opened_at="2026-10-31T00:00:00Z")
    bars = [{"open_time": "2026-11-01T00:00:00Z", "high": 4070, "low": 4060}]
    assert j.grade({"1h": bars}, tf="1h") == 0
    assert j.all_trades()[0].status == "open"
    assert j.stats() == {"n": 0}


def test_expectancy_is_reported_in_r_not_points(j: Journal) -> None:
    j.add("XAUUSD", "long", 4063.6, 4055.3, 4083.0, opened_at="2026-10-31T00:00:00Z")   # +2.34R
    j.add("XAUUSD", "short", 4090.0, 4100.0, 4060.0, opened_at="2026-10-31T00:00:00Z")  # -1.00R
    bars = [
        {"open_time": "2026-11-01T00:00:00Z", "high": 4092, "low": 4088},
        {"open_time": "2026-11-01T01:00:00Z", "high": 4101, "low": 4055},
    ]
    j.grade({"1h": bars}, tf="1h")
    st = j.stats()
    assert st["n"] == 2
    assert st["win_rate"] == 0.5
    # a 50% win rate at 2.34R is profitable; the headline is the R, not the rate
    assert st["expectancy_r"] == pytest.approx(0.67, abs=0.01)
    assert st["profit_factor"] == pytest.approx(2.34, abs=0.01)


def test_manual_close_is_comparable_to_an_automatic_one(j: Journal) -> None:
    tid = j.add("XAUUSD", "long", 4055.0, 4048.0, 4083.0)
    assert j.close_manual(tid, 4063.0) is True
    t = j.all_trades()[0]
    assert t.status == "closed"
    assert t.r_multiple == pytest.approx(8 / 7, abs=0.01)
    assert j.close_manual(999, 1.0) is False
