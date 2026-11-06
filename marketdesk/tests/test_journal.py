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


def test_a_trade_recorded_after_the_fact_grades_on_history(j: Journal) -> None:
    """A trade is usually written down after it happened. `opened_at` puts it in
    the past so `grade` walks the real bars instead of finding an empty window."""
    j.add("XAUUSD", "long", 4063.0, 4055.0, 4073.0, opened_at="2026-11-06T08:25:00Z")
    bars = [
        {"open_time": "2026-11-06T08:20:00Z", "high": 4070.53, "low": 4065.31},
        {"open_time": "2026-11-06T08:25:00Z", "high": 4068.82, "low": 4063.06},
        {"open_time": "2026-11-06T08:45:00Z", "high": 4074.14, "low": 4070.53},
    ]
    assert j.grade({"5m": bars}, tf="5m") == 1
    t = j.all_trades()[0]
    assert t.result == "target"
    assert t.r_multiple == pytest.approx(1.25, abs=0.01)


def test_a_pending_order_is_not_a_trade_until_price_reaches_it(j: Journal) -> None:
    """A limit order is a plan. Counting its target before the market fills it
    would book a win the owner never had."""
    j.add("XAUUSD", "long", 4065.0, 4055.0, 4076.0,
                opened_at="2026-11-06T09:00:00Z", pending=True)
    assert j.all_trades()[0].is_pending
    above = [{"open_time": "2026-11-06T09:00:00Z", "high": 4075.0, "low": 4070.0}]
    assert j.grade({"5m": above}, tf="5m") == 0
    assert j.all_trades()[0].is_pending  # untouched: price never came down to it
    assert j.stats() == {"n": 0}

    filled = above + [
        {"open_time": "2026-11-06T09:05:00Z", "high": 4072.0, "low": 4063.0},
        {"open_time": "2026-11-06T09:10:00Z", "high": 4077.0, "low": 4070.0},
    ]
    assert j.grade({"5m": filled}, tf="5m") == 1
    t = j.all_trades()[0]
    assert not t.is_pending
    assert t.fill_time == "2026-11-06T09:05:00Z"
    assert t.result == "target" and t.r_multiple == pytest.approx(1.10, abs=0.01)


def test_a_pending_order_that_fills_and_stops_the_same_bar_takes_the_stop(
        j: Journal) -> None:
    """The bar that reaches the entry may also run through the stop. Read it as
    filled-then-stopped: the conservative order, as everywhere else here."""
    j.add("XAUUSD", "long", 4065.0, 4055.0, 4076.0,
          opened_at="2026-11-06T09:00:00Z", pending=True)
    bars = [{"open_time": "2026-11-06T09:00:00Z", "high": 4066.0, "low": 4050.0}]
    assert j.grade({"5m": bars}, tf="5m") == 1
    t = j.all_trades()[0]
    assert t.result == "stop" and t.r_multiple == -1.0


def test_an_old_store_gains_the_new_columns_without_losing_a_row(
        tmp_path: Path) -> None:
    """`CREATE TABLE IF NOT EXISTS` leaves an existing file alone, so the new
    columns are migrated in. The owner's record must survive the upgrade."""
    import sqlite3
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.executescript(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, "
        "direction TEXT, entry REAL, stop REAL, target REAL, size REAL, "
        "opened_at TEXT, note TEXT, source TEXT, run_id TEXT, status TEXT, "
        "result TEXT, exit_price REAL, bars INTEGER, r_multiple REAL, "
        "closed_at TEXT, graded_at REAL);"
        "INSERT INTO trades (symbol,direction,entry,stop,opened_at,status) "
        "VALUES ('XAUUSD','long',4063,4055,'2026-11-06T08:25:00Z','open');")
    c.commit()
    c.close()
    j = Journal(p)  # runs the migration
    rows = j.all_trades()
    assert len(rows) == 1 and rows[0].entry == 4063
    assert rows[0].pending == 0 and rows[0].fill_time is None


def test_manual_close_is_comparable_to_an_automatic_one(j: Journal) -> None:
    tid = j.add("XAUUSD", "long", 4055.0, 4048.0, 4083.0)
    assert j.close_manual(tid, 4063.0) is True
    t = j.all_trades()[0]
    assert t.status == "closed"
    assert t.r_multiple == pytest.approx(8 / 7, abs=0.01)
    assert j.close_manual(999, 1.0) is False


def test_money_needs_a_size_and_a_size_can_be_filled_in_later(j: Journal) -> None:
    """A trade with no recorded size still grades in R, but it cannot be
    turned into money -- so `money` is None, not a guess."""
    tid = j.add("XAUUSD", "short", 4042.0, 4050.0, 4033.0)
    j.close_manual(tid, 4033.0)
    t = j.all_trades()[0]
    assert t.r_multiple == pytest.approx(9 / 8, abs=0.01)
    assert t.money is None  # no size: money is unknown, not zero

    assert j.set_size(tid, 0.01) is True
    t = j.all_trades()[0]
    # 0.01 lot pays $1 per point, and the trade made 9 points
    assert t.money == pytest.approx(9.0)
    assert j.set_size(999, 0.01) is False


def test_report_totals_money_and_flags_unpriced_trades(j: Journal) -> None:
    a = j.add("XAUUSD", "short", 4042.0, 4050.0, 4033.0, size=0.01)
    j.close_manual(a, 4033.0)
    b = j.add("XAUUSD", "long", 4055.0, 4048.0, 4083.0)  # no size
    j.close_manual(b, 4063.0)
    out = j.report()
    assert "+9.00$" in out
    assert "1 صفقة بلا حجم مسجّل" in out


def test_a_trade_that_risks_more_than_it_pays_is_flagged(j: Journal) -> None:
    """The desk refuses its own plans below min_rr; the owner's log should at
    least say when a trade is on the wrong side of 1R, at the moment it is
    written down rather than after the week is over."""
    weak = j.add("XAUUSD", "short", 4036.0, 4044.0, 4031.0)   # 8 risk, 5 reward
    sound = j.add("XAUUSD", "short", 4036.0, 4044.0, 4020.0)  # 8 risk, 16 reward
    no_target = j.add("XAUUSD", "short", 4036.0, 4044.0)      # nothing to judge
    weak_t, sound_t, no_target_t = j.all_trades()
    assert weak_t.planned_r == pytest.approx(0.625)
    assert weak_t.weak_reward is True
    assert sound_t.weak_reward is False
    assert no_target_t.weak_reward is False  # a missing target is not a weak one
    assert "⚠️" in weak_t.line()
    assert "⚠️" not in sound_t.line()
    assert "3 صفقة" not in j.report() and "1 صفقة بمضاعف أقل من 1" in j.report()
    assert weak == 1 and sound == 2 and no_target == 3
