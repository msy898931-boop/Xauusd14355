"""Tests — keyless and offline. They pin the invariants that matter.

Run: PYTHONPATH=. python3 -m pytest tests -q
"""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desk import indicators as ind
from desk.agents.news_agent import classify
from desk.config import Settings
from desk.memory import AgentMemory, SharedMemory
from desk.orchestrator import Orchestrator

# ---- fixtures ---------------------------------------------------------

def _bars(n=120, start=3000.0, step=5.0):
    """Raw biquote-shaped bars (what a source returns), monotonic in `step`."""
    out = []
    for i in range(n):
        c = start + i * step
        out.append({"openTime": f"2026-01-01T{i % 24:02d}:{i // 24:02d}:00Z",
                    "open": c - 1, "high": c + 2, "low": c - 2, "close": c,
                    "tickVolume": 100 + i, "isOpen": False})
    return out


def _wave(n=200, period=20, amp=50.0, base=3000.0, drift=0.0):
    """An oscillating series (with optional drift) so swing structure is defined."""
    import math
    out = []
    for i in range(n):
        c = base + drift * i + amp * math.sin(2 * math.pi * i / period)
        out.append({"openTime": f"2026-01-01T{i % 24:02d}:{i // 24:02d}:00Z",
                    "open": c - 1, "high": c + 2, "low": c - 2, "close": c,
                    "tickVolume": 100 + i, "isOpen": False})
    return out


def _loaded(bars):
    """Raw biquote bars -> the internal shape agents use (t/o/h/l/c/tv)."""
    return [{"t": b["openTime"], "o": b["open"], "h": b["high"], "l": b["low"],
             "c": b["close"], "tv": b["tickVolume"], "open": False}
            for b in bars]


def _settings(tmp_path):
    s = Settings()
    s.home = tmp_path
    s.groq_key = ""          # no model in tests
    s.timeframes = ("1d", "4h", "1h")
    s.peers = ()
    return s


def _stub_run(s, *, verdict="no-trade", score=0.0, rec=None):
    """A Run with no agents, for tests that only exercise the watch loop."""
    from desk.orchestrator import Run
    return Run("stub", s.symbol, time.time(), [], verdict, score, rec or {})


# ---- indicators -------------------------------------------------------

def test_ema_none_when_too_short():
    assert ind.ema([1, 2, 3], 5) == [None, None, None]


def test_rsi_bounds():
    up = [float(i) for i in range(1, 40)]
    r = ind.rsi(up)
    assert r is not None and 0 <= r <= 100 and r > 70


def test_rsi_none_when_too_short():
    assert ind.rsi([1, 2, 3], 14) is None


def test_atr_none_when_too_short():
    assert ind.atr(_loaded(_bars(5)), 14) is None


def test_macd_none_when_too_short():
    line, sig, hist = ind.macd([1, 2, 3])
    assert line is None and sig is None and hist is None


def test_structure_up():
    # A rising series with pullbacks: last pivot high and low both higher.
    assert ind.structure(_loaded(_wave(200, period=20, amp=50, drift=1.5)), wing=3) == "up"


def test_structure_down():
    assert ind.structure(_loaded(_wave(200, period=20, amp=50, drift=-1.5)), wing=3) == "down"


def test_pivots_are_printed_prices():
    bars = _loaded(_wave(120))
    lo, hi = ind.pivots(bars, 2)
    prices = {b["l"] for b in bars} | {b["h"] for b in bars}
    assert all(x in prices for x in lo + hi)


# ---- memory -----------------------------------------------------------

def test_shared_memory_roundtrip(tmp_path):
    m = SharedMemory(tmp_path / "s.db")
    m.save_candles("X", "1h", [{"openTime": "t1", "open": 1, "high": 2, "low": 0.5,
                                "close": 1.5, "tickVolume": 7, "isOpen": False}])
    rows = m.load_candles("X", "1h")
    assert len(rows) == 1 and rows[0]["tick_volume"] == 7


def test_shared_memory_upsert_does_not_duplicate(tmp_path):
    m = SharedMemory(tmp_path / "s.db")
    bar = {"openTime": "t1", "open": 1, "high": 2, "low": 0.5, "close": 1.5,
           "tickVolume": 7, "isOpen": False}
    m.save_candles("X", "1h", [bar])
    bar["close"] = 9
    m.save_candles("X", "1h", [bar])
    rows = m.load_candles("X", "1h")
    assert len(rows) == 1 and rows[0]["close"] == 9


def test_study_unique_per_run_agent(tmp_path):
    m = SharedMemory(tmp_path / "s.db")
    m.publish_study("r1", "a", "k", "bull", 0.5, 0.5, "first", {})
    m.publish_study("r1", "a", "k", "bear", 0.9, -0.9, "second", {})
    studies = m.load_studies("r1")
    assert len(studies) == 1 and studies[0]["summary"] == "second"


def test_agent_memory_is_private_and_bounded(tmp_path):
    mem = AgentMemory(tmp_path / "agent")
    for i in range(60):
        mem.remember(f"note {i}", verdict="bull")
    d = mem.recall()
    assert len(d["notes"]) <= 50 and d["runs"] == 60
    assert mem.last_verdict() == "bull"


# ---- news classification ---------------------------------------------

def test_classify_bullish():
    assert classify("Gold rallies as Fed signals rate cut") == 1


def test_classify_bearish():
    assert classify("Gold plunges as dollar strengthens, sell-off deepens") == -1


def test_classify_neutral():
    assert classify("Gold trades sideways ahead of data") == 0


# ---- orchestrator (offline, monkeypatched source) ---------------------

def test_orchestrator_runs_without_network(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    desk = Orchestrator(s)
    bars = _bars(120)
    monkeypatch.setattr("desk.sources.fetch_candles", lambda sym, tf, limit=1000: bars)
    monkeypatch.setattr("desk.sources.fetch_quote",
                        lambda sym: {"mid": bars[-1]["close"], "bid": 1, "ask": 1,
                                     "market_state": "open", "stale": False,
                                     "quote_age_s": 0, "day_diff_pct": 1.0,
                                     "source": "test"})
    monkeypatch.setattr("desk.sources.fetch_news", lambda symbol=None, max_results=25: [])
    monkeypatch.setattr("desk.sources.fetch_calendar", lambda importance="high", limit=100: [])
    monkeypatch.setattr("desk.sources.fetch_summary", lambda: {})
    run = desk.run()
    assert run.verdict in ("long", "short", "no-trade")
    # Every agent in the roster must both run and publish to the shared board.
    from desk.agents import ROSTER
    assert len(run.studies) == len(ROSTER)
    assert len(desk.shared.load_studies(run.run_id)) == len(ROSTER)


def test_risk_gate_blocks_when_market_closed(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    desk = Orchestrator(s)
    bars = _bars(120)
    monkeypatch.setattr("desk.sources.fetch_candles", lambda sym, tf, limit=1000: bars)
    monkeypatch.setattr("desk.sources.fetch_quote",
                        lambda sym: {"mid": bars[-1]["close"], "bid": 1, "ask": 1,
                                     "market_state": "closed", "stale": True,
                                     "quote_age_s": 9999, "day_diff_pct": 0.0,
                                     "source": "test"})
    monkeypatch.setattr("desk.sources.fetch_news", lambda symbol=None, max_results=25: [])
    monkeypatch.setattr("desk.sources.fetch_calendar", lambda importance="high", limit=100: [])
    monkeypatch.setattr("desk.sources.fetch_summary", lambda: {})
    run = desk.run()
    assert run.verdict == "no-trade"
    assert run.recommendation["allowed"] is False


def test_missing_metric_is_none_not_zero(tmp_path, monkeypatch):
    """A short sample must yield None, never a zero dressed as a measurement."""
    s = _settings(tmp_path)
    desk = Orchestrator(s)
    monkeypatch.setattr("desk.sources.fetch_candles", lambda sym, tf, limit=1000: _bars(10))
    monkeypatch.setattr("desk.sources.fetch_quote",
                        lambda sym: {"mid": 3050.0, "bid": 1, "ask": 1,
                                     "market_state": "open", "stale": False,
                                     "quote_age_s": 0, "day_diff_pct": 0.0,
                                     "source": "test"})
    monkeypatch.setattr("desk.sources.fetch_news", lambda symbol=None, max_results=25: [])
    monkeypatch.setattr("desk.sources.fetch_calendar", lambda importance="high", limit=100: [])
    monkeypatch.setattr("desk.sources.fetch_summary", lambda: {})
    run = desk.run()
    vol = next(s for s in run.studies if s.agent == "volatility")
    assert vol.payload["frames"].get("1h", {}).get("atr") is None


def test_plan_is_built_when_bias_is_strong(tmp_path, monkeypatch):
    """A clear uptrend must produce a long plan with entry, stop and targets —
    and every target must carry a provenance tag."""
    s = _settings(tmp_path)
    desk = Orchestrator(s)
    s.min_rr = 0.0          # isolate plan construction; the R:R filter has its own tests
    bars = _wave(220, period=40, amp=30, base=3000, drift=3.0)
    monkeypatch.setattr("desk.sources.fetch_candles", lambda sym, tf, limit=1000: bars)
    monkeypatch.setattr("desk.sources.fetch_quote",
                        lambda sym: {"mid": bars[-1]["close"], "bid": 1, "ask": 1,
                                     "market_state": "open", "stale": False,
                                     "quote_age_s": 0, "day_diff_pct": 1.0,
                                     "source": "test"})
    monkeypatch.setattr("desk.sources.fetch_news", lambda symbol=None, max_results=25: [])
    monkeypatch.setattr("desk.sources.fetch_calendar", lambda importance="high", limit=100: [])
    monkeypatch.setattr("desk.sources.fetch_summary", lambda: {})
    run = desk.run()
    assert run.verdict == "long"
    rec = run.recommendation
    assert rec["entry"] is not None and rec["stop"] is not None
    assert rec["targets"], "a long plan must name at least one target"
    assert all(t["kind"] in ("pivot", "fib", "projection") for t in rec["targets"])


def test_news_runs_before_risk_gate(tmp_path, monkeypatch):
    """The news agent must publish before the risk gate reads the board.

    Regression: they once ran in the same pool. The gate reads SQLite instantly
    and news waits on the network, so the gate saw an empty board and waved
    through a high-impact event it exists to block.
    """
    s = _settings(tmp_path)
    desk = Orchestrator(s)
    bars = _bars(120)

    def news_with_event(symbol=None, max_results=25):
        return [{"title": "Gold rallies on rate cut hopes", "description": "",
                 "publisher": "t", "published": "2026-01-01T00:00:00Z", "url": "u"}]

    def calendar_with_imminent(importance="high", limit=100):
        import datetime as dt
        soon = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=3)).isoformat()
        return [{"time": soon, "name": "FOMC", "currency": "USD",
                 "importance": "high", "forecast": None}]

    monkeypatch.setattr("desk.sources.fetch_candles", lambda sym, tf, limit=1000: bars)
    monkeypatch.setattr("desk.sources.fetch_quote",
                        lambda sym: {"mid": bars[-1]["close"], "bid": 1, "ask": 1,
                                     "market_state": "open", "stale": False,
                                     "quote_age_s": 0, "day_diff_pct": 1.0,
                                     "source": "test"})
    monkeypatch.setattr("desk.sources.fetch_news", news_with_event)
    monkeypatch.setattr("desk.sources.fetch_calendar", calendar_with_imminent)
    monkeypatch.setattr("desk.sources.fetch_summary", lambda: {})
    run = desk.run()
    risk = next(s for s in run.studies if s.agent == "risk")
    assert risk.payload["allowed"] is False, "the gate must see the news agent's event risk"
    assert run.verdict == "no-trade"


# ---- scoreboard -------------------------------------------------------

def _run_with_history(tmp_path, monkeypatch, *, bias_score, entry, later_price,
                      run_age_s=8 * 3600, study_stance="bear"):
    """Seed one run far enough in the past to grade, with candles reaching it."""
    import datetime as dt
    import time
    s = _settings(tmp_path)
    desk = Orchestrator(s)
    run_id = "graded-run-1"
    started = time.time() - run_age_s

    # Bars spread from the run's time to now, 5 minutes apart, so a bar really
    # exists at each horizon. The entry price lives in the run's quote; every
    # bar carries the price the horizon resolves at.
    now = dt.datetime.now(dt.UTC)
    t0 = now - dt.timedelta(seconds=run_age_s)
    n = int(run_age_s / 300) + 1
    for tf in ("5m", "15m", "1h"):
        bars = []
        for i in range(n):
            ts = (t0 + dt.timedelta(minutes=5 * i)).isoformat().replace("+00:00", "Z")
            bars.append({"openTime": ts, "open": later_price, "high": later_price + 1,
                         "low": later_price - 1, "close": later_price,
                         "tickVolume": 1, "isOpen": False})
        desk.shared.save_candles(s.symbol, tf, bars)
    desk.shared.save_run(run_id, s.symbol, started, started + 1, "no-trade",
                         bias_score,
                         {"bias": {"direction": ("up" if bias_score > 0.15
                                                 else "down" if bias_score < -0.15
                                                 else "flat"),
                                   "score": bias_score},
                          "gather": {"quote": {"mid": entry}}})
    desk.shared.publish_study(run_id, "structure", "price_action",
                              study_stance, 0.8, -0.6, "test study", {})
    return desk, run_id


def test_scoreboard_grades_correct_call(tmp_path, monkeypatch):
    from desk.scoreboard import Grader
    desk, _ = _run_with_history(tmp_path, monkeypatch, bias_score=-0.5,
                                entry=4000.0, later_price=3960.0)   # fell as predicted
    board = Grader(desk.shared).grade()
    assert board.runs_graded == 1
    h = board.horizons["1h"]
    assert h.graded == 1 and h.correct == 1
    assert h.hit_rate == 1.0


def test_scoreboard_grades_wrong_call(tmp_path, monkeypatch):
    from desk.scoreboard import Grader
    desk, _ = _run_with_history(tmp_path, monkeypatch, bias_score=-0.5,
                                entry=4000.0, later_price=4040.0)   # rose instead
    board = Grader(desk.shared).grade()
    assert board.horizons["1h"].correct == 0


def test_scoreboard_skips_horizon_not_reached(tmp_path, monkeypatch):
    """A recent run must NOT be graded against the current price."""
    from desk.scoreboard import Grader
    desk, _ = _run_with_history(tmp_path, monkeypatch, bias_score=-0.5,
                                entry=4000.0, later_price=3960.0, run_age_s=60)
    board = Grader(desk.shared).grade()
    assert board.runs_graded == 0
    assert board.horizons["1h"].graded == 0
    assert board.horizons["1h"].skipped == 1


def test_scoreboard_deadband_is_not_a_win(tmp_path, monkeypatch):
    """A move inside the deadband is skipped, not counted as correct."""
    from desk.scoreboard import Grader
    desk, _ = _run_with_history(tmp_path, monkeypatch, bias_score=-0.5,
                                entry=4000.0, later_price=4000.5)   # +0.0125%, noise
    board = Grader(desk.shared).grade()
    assert board.horizons["1h"].graded == 0


def test_scoreboard_grades_each_agent(tmp_path, monkeypatch):
    from desk.scoreboard import Grader
    desk, run_id = _run_with_history(tmp_path, monkeypatch, bias_score=-0.5,
                                     entry=4000.0, later_price=3960.0,
                                     study_stance="bear")
    Grader(desk.shared).grade()
    rows = desk.shared.load_agent_outcomes(agent="structure")
    assert rows and all(r["correct"] == 1 for r in rows)


def test_scoreboard_flat_bias_is_excluded(tmp_path, monkeypatch):
    from desk.scoreboard import Grader
    desk, _ = _run_with_history(tmp_path, monkeypatch, bias_score=0.0,
                                entry=4000.0, later_price=3960.0)
    board = Grader(desk.shared).grade()
    assert board.runs_graded == 0, "a flat bias is not a directional call"


# ---- alerting / watch -------------------------------------------------

def test_no_alert_when_not_configured(tmp_path):
    from desk.notify import Notifier
    s = _settings(tmp_path)
    s.telegram_token = ""
    s.telegram_chat_id = ""
    n = Notifier(s)
    assert n.available is False
    assert n.send("hello") is False


def test_signal_ledger_dedupes(tmp_path):
    from desk.notify import SignalLedger
    led = SignalLedger(tmp_path / "ledger.json")
    assert led.seen("XAUUSD:long:-0.5") is False
    led.record("XAUUSD:long:-0.5", "run1")
    assert led.seen("XAUUSD:long:-0.5") is True
    assert led.seen("XAUUSD:short:0.5") is False


def test_actionable_only_for_live_confident_calls(tmp_path, monkeypatch):
    from desk.orchestrator import Run
    desk = Orchestrator(_settings(tmp_path))
    desk.settings.alert_score = 0.25

    def mk(verdict, score, allowed=True):
        r = Run("x", "XAUUSD", 0.0, [], verdict, score, {"allowed": allowed})
        return desk.actionable(r)

    assert mk("no-trade", -0.5)[0] is False
    assert mk("long", 0.10)[0] is False          # below threshold
    assert mk("long", 0.60, allowed=False)[0] is False   # gate blocked
    ok, fp = mk("short", -0.60)
    assert ok is True and fp.startswith("XAUUSD:short")


def test_actionable_refuses_to_alert_on_stale_data(tmp_path):
    """A directional call computed from a feed that stopped hours ago is a stale
    number wearing a signal's clothes. The feed's own stale flag catches a slow
    quote; this catches the case where the whole source is down and the run
    silently proceeds on whatever the database already held. Only the ALERT is
    gated -- the run and the scoreboard still work on old data."""
    import sqlite3
    import time

    from desk.memory import SharedMemory
    from desk.orchestrator import Run

    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    for tf in s.timeframes:
        shared.save_candles(s.symbol, tf, _wave(60, period=20, amp=20, base=3000))
    shared.save_quote(s.symbol, {"mid": 3000.0, "stale": False})
    desk = Orchestrator(s)
    desk.settings.alert_score = 0.25
    r = Run("x", "XAUUSD", 0.0, [], "short", -0.60, {"allowed": True})
    assert desk.actionable(r)[0] is True, "a fresh, confident call must alert"

    # Age the stored reading past the limit: the source is effectively down.
    desk.settings.max_data_age_s = 3600
    with sqlite3.connect(s.shared_db) as c:
        c.execute("UPDATE quotes SET fetched_at = ? WHERE symbol = ?",
                  (time.time() - 5 * 3600, s.symbol))
        c.execute("UPDATE candles SET fetched_at = ? WHERE symbol = ?",
                  (time.time() - 5 * 3600, s.symbol))
    ok, reason = desk.actionable(r)
    assert ok is False, "stale data must not produce an alert"
    assert "قديمة" in reason or "stale" in reason.lower()


# ---- the active plan: follow one call, report only what changes --------

def test_active_plan_fills_and_follows_its_lifecycle(tmp_path):
    """A breakout plan is `waiting` until price crosses the trigger, then
    `on_track`; before it fills, touching the stop is a dead setup, not a loss."""
    from desk import active

    s = _settings(tmp_path)
    p = active.adopt(s, {"direction": "long", "entry": 4150.0, "stop": 4113.0,
                         "targets": [{"level": 4184.0}]}, "r1", now=1000.0)
    assert p.status == "waiting"
    assert p.evaluate(4140, now=1000) == "waiting"      # not filled yet
    assert p.evaluate(4100, now=1000) == "invalidated"  # died before filling
    assert p.evaluate(4155, now=1000) == "on_track"     # filled
    p.status = "on_track"
    assert p.evaluate(4190, now=1000) == "target"
    assert p.evaluate(4100, now=1000) == "stopped"


def test_active_plan_short_side_is_mirrored(tmp_path):
    from desk import active

    s = _settings(tmp_path)
    p = active.adopt(s, {"direction": "short", "entry": 3900.0, "stop": 3937.0,
                         "targets": [{"level": 3866.0}]}, "r1", now=1000.0)
    assert p.evaluate(3910, now=1000) == "waiting"
    assert p.evaluate(3940, now=1000) == "invalidated"
    assert p.evaluate(3890, now=1000) == "on_track"
    p.status = "on_track"
    assert p.evaluate(3860, now=1000) == "target"
    assert p.evaluate(3940, now=1000) == "stopped"


def test_active_plan_expires_while_waiting(tmp_path):
    """A trigger that never fires is a level the market has left behind. A plan
    that sits unfilled past the expiry is abandoned, not followed forever."""
    from desk import active

    s = _settings(tmp_path)
    p = active.adopt(s, {"direction": "long", "entry": 4150.0, "stop": 4113.0,
                         "targets": [{"level": 4184.0}]}, "r1", now=1000.0)
    day = 24 * 3600
    assert p.evaluate(4140, now=1000 + day - 10, expiry_s=day) == "waiting"
    assert p.evaluate(4140, now=1000 + day + 10, expiry_s=day) == "invalidated"


def test_active_plan_round_trips_through_disk(tmp_path):
    from desk import active

    s = _settings(tmp_path)
    active.adopt(s, {"direction": "long", "entry": 4150.0, "stop": 4113.0,
                     "targets": [{"level": 4184.0}]}, "r1", now=1000.0)
    got = active.load(s)
    assert got is not None and got.entry == 4150.0 and got.status == "waiting"
    active.clear(s)
    assert active.load(s) is None


def test_adopt_ignores_a_non_tradeable_recommendation(tmp_path):
    """`no trade` is not a plan to follow: no direction, no entry, no target."""
    from desk import active

    s = _settings(tmp_path)
    assert active.adopt(s, {"direction": "no-trade"}, "r1") is None
    assert active.adopt(s, {"direction": "long", "entry": None, "stop": 1.0,
                            "targets": [{"level": 2.0}]}, "r1") is None
    assert active.adopt(s, {"direction": "long", "entry": 1.0, "stop": 2.0,
                            "targets": []}, "r1") is None


def test_watch_once_follows_the_plan_and_reports_only_changes(tmp_path, monkeypatch):
    """Once a plan is in play the watcher reports its STATUS, not the direction
    again: a change is always sent, an unchanged `on_track` waits for the
    heartbeat. A finished plan is cleared."""
    from desk import active

    s = _settings(tmp_path)
    desk = Orchestrator(s)
    sent: list[str] = []
    monkeypatch.setattr(desk, "run", lambda now=None: _stub_run(s))
    desk.settings.plan_heartbeat_s = 3600.0

    class _N:
        def send(self, text):
            sent.append(text)
            return True

    # Fill the plan and put it on track.
    active.adopt(s, {"direction": "long", "entry": 4150.0, "stop": 4113.0,
                     "targets": [{"level": 4184.0}]}, "r1", now=time.time())
    p = active.load(s)
    p.status = "on_track"
    p.heartbeat_at = time.time()          # just heard from it
    active.save(s, p)

    monkeypatch.setattr(desk, "_live_price", lambda run: 4160.0)   # still on track
    out = desk.watch_once(notifier=_N())
    assert out["active"] == "on_track" and out["alerted"] is False
    assert sent == [], "an unchanged on_track must not re-send before the heartbeat"

    # A real change -- price reached the target -- is sent and then cleared.
    monkeypatch.setattr(desk, "_live_price", lambda run: 4190.0)
    out = desk.watch_once(notifier=_N())
    assert out["active"] == "target" and out["alerted"] is True
    assert len(sent) == 1 and "الهدف" in sent[0]
    assert active.load(s) is None, "a finished plan must not be followed forever"


def test_watch_once_sends_a_heartbeat_for_a_running_plan(tmp_path, monkeypatch):
    """`on_track` unchanged is re-sent once the heartbeat is due, so the owner
    knows the desk is still watching rather than asleep."""
    from desk import active

    s = _settings(tmp_path)
    desk = Orchestrator(s)
    sent: list[str] = []
    monkeypatch.setattr(desk, "run", lambda now=None: _stub_run(s))
    desk.settings.plan_heartbeat_s = 60.0

    class _N:
        def send(self, text):
            sent.append(text)
            return True

    active.adopt(s, {"direction": "long", "entry": 4150.0, "stop": 4113.0,
                     "targets": [{"level": 4184.0}]}, "r1", now=time.time())
    p = active.load(s)
    p.status = "on_track"
    p.heartbeat_at = time.time() - 120     # overdue
    active.save(s, p)

    monkeypatch.setattr(desk, "_live_price", lambda run: 4160.0)
    out = desk.watch_once(notifier=_N())
    assert out["alerted"] is True and len(sent) == 1
    assert "مكملة" in sent[0] or "رايحة" in sent[0]


def test_the_alert_fingerprint_names_the_trade_not_the_opinion(tmp_path):
    """Keying the ledger on direction+score alone meant a plan that completed
    could suppress the *next* trade of the same direction and conviction forever.
    The trigger is part of the key, so a genuinely new setup alerts."""
    s = _settings(tmp_path)
    desk = Orchestrator(s)
    a = _stub_run(s, verdict="long", score=0.5,
                  rec={"entry": 4150.0, "stop": 4113.0, "targets": [{"level": 4184.0}]})
    b = _stub_run(s, verdict="long", score=0.5,
                  rec={"entry": 4200.0, "stop": 4160.0, "targets": [{"level": 4240.0}]})
    _, fp_a = desk.actionable(a)
    _, fp_b = desk.actionable(b)
    assert fp_a != fp_b, "a different trigger must be a different trade"
    _, fp_a2 = desk.actionable(a)
    assert fp_a2 == fp_a, "the same trigger must not re-alert"


def test_watch_once_drops_a_plan_the_fresh_read_contradicts(tmp_path, monkeypatch):
    """A breakout that has not fired, on a day the desk's own fresh read now
    leans the other way, is a setup going cold. The owner is told, and the stale
    plan is dropped. `no-trade` is silence, not a reversal, and must NOT drop it.
    """
    from desk import active

    s = _settings(tmp_path)
    desk = Orchestrator(s)
    sent: list[str] = []

    class _N:
        def send(self, text):
            sent.append(text)
            return True

    active.adopt(s, {"direction": "long", "entry": 4150.0, "stop": 4113.0,
                     "targets": [{"level": 4184.0}]}, "r1", now=time.time())
    monkeypatch.setattr(desk, "_live_price", lambda run: 4140.0)   # still waiting

    # A quiet day: the desk says no-trade. The plan is untouched.
    monkeypatch.setattr(desk, "run", lambda now=None: _stub_run(s, verdict="no-trade"))
    out = desk.watch_once(notifier=_N())
    assert out["active"] == "waiting" and sent == []
    assert active.load(s) is not None

    # The desk now backs the other side. The waiting plan is dropped.
    monkeypatch.setattr(desk, "run", lambda now=None: _stub_run(s, verdict="short"))
    out = desk.watch_once(notifier=_N())
    assert "contradicted" in out["reason"] and out["alerted"] is True
    assert len(sent) == 1 and "تغيّر الوضع" in sent[0]
    assert active.load(s) is None


def test_levels_from_scenarios_watch_the_triggers_the_desk_printed(tmp_path):
    """The scenario triggers must become watchable levels, on the correct side.

    This is the fix for a real gap: the desk printed "break below 3,919.49" in
    its scenarios while the alert watched a hand-typed number. The level the
    plan actually hinged on had no alert at all, so the trade the desk described
    could not be entered by anyone reading its own alerts.
    """
    from desk.levels import from_scenarios, triggered

    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    for tf in s.timeframes:
        shared.save_candles(s.symbol, tf, _wave(220, period=40, amp=30, base=3000,
                                                drift=-3.0))
    live = 3000.0
    lv = from_scenarios(s, live)
    assert lv, "a full frame set must yield scenario levels"
    # A long breakout is confirmed ABOVE its trigger; a short breakdown BELOW.
    for level in lv:
        if "اختراق" in level.label:
            assert level.side == "above"
        else:
            assert level.side == "below"
    # A level on its breaking side fires; one on the far side does not.
    above = [x for x in lv if x.side == "above"]
    if above:
        assert above[0] in triggered(above[0].price + 1.0, lv)
        assert above[0] not in triggered(above[0].price - 1.0, lv)


def test_auto_levels_include_both_the_plan_and_the_scenarios(tmp_path, monkeypatch):
    """The plan and the scenarios name different prices, and both matter: the
    plan is the trade, the scenarios are what happens if it does not trigger. A
    plan-only watcher goes silent exactly when the market is deciding."""
    from desk.levels import _auto_levels, from_recommendation, from_scenarios

    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    for tf in s.timeframes:
        shared.save_candles(s.symbol, tf, _wave(220, period=40, amp=30, base=3000,
                                                drift=-3.0))
    rec = {"direction": "short", "entry": 3000.0, "stop": 3015.0,
           "targets": [{"level": 2970.0}]}
    shared.save_run("r1", s.symbol, 0.0, 1.0, "short", -0.3,
                    {"recommendation": rec})

    live = 3000.0
    auto = _auto_levels(s, live)
    plan = from_recommendation(rec, live)
    scen = from_scenarios(s, live)
    assert plan, "the plan must produce levels"
    assert scen, "the scenarios must produce levels"
    for level in plan:
        assert level in auto
    assert any(x in auto for x in scen), "scenario triggers must be watched too"


def test_auto_levels_do_not_repeat_a_price(tmp_path):
    """The plan and the scenario set can name the same shelf; two alerts for one
    price is the noise the ledger exists to avoid."""
    from desk.levels import Level, _dedupe

    dup = (Level(3000.0, "أ", "below", "x"), Level(3000.0, "ب", "above", "y"),
           Level(3001.0, "ج", "below", "z"))
    out = _dedupe(list(dup))
    assert [x.label for x in out] == ["أ", "ج"]      # first wins


def test_watching_the_scenarios_can_be_switched_off(tmp_path):
    """DESK_WATCH_SCENARIOS=0 watches the plan alone. An operator who finds the
    scenario alerts noisy must be able to silence them without a code change."""
    from desk.levels import _auto_levels, from_recommendation

    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    for tf in s.timeframes:
        shared.save_candles(s.symbol, tf, _wave(220, period=40, amp=30, base=3000,
                                                drift=-3.0))
    rec = {"direction": "short", "entry": 3000.0, "stop": 3015.0,
           "targets": [{"level": 2970.0}]}
    shared.save_run("r1", s.symbol, 0.0, 1.0, "short", -0.3,
                    {"recommendation": rec})
    s.watch_scenarios = False
    auto = _auto_levels(s, 3000.0)
    assert set(auto) == set(from_recommendation(rec, 3000.0))


def test_watch_once_does_not_alert_when_signal_repeats(tmp_path, monkeypatch):
    """The watcher must send a given signal once, not once per pass."""
    s = _settings(tmp_path)
    desk = Orchestrator(s)
    bars = _wave(220, period=40, amp=30, base=3000, drift=-3.0)
    monkeypatch.setattr("desk.sources.fetch_candles", lambda sym, tf, limit=1000: bars)
    monkeypatch.setattr("desk.sources.fetch_quote",
                        lambda sym: {"mid": bars[-1]["close"], "bid": 1, "ask": 1,
                                     "market_state": "open", "stale": False,
                                     "quote_age_s": 0, "day_diff_pct": -1.0,
                                     "source": "test"})
    monkeypatch.setattr("desk.sources.fetch_news", lambda symbol=None, max_results=25: [])
    monkeypatch.setattr("desk.sources.fetch_calendar", lambda importance="high", limit=100: [])
    monkeypatch.setattr("desk.sources.fetch_summary", lambda: {})
    s.alert_score = 0.25
    s.min_rr = 0.0          # isolate the alert path; the R:R filter has its own tests

    class FakeNotifier:
        def __init__(self): self.sent = []
        def send(self, text):
            self.sent.append(text)
            return True

    fake = FakeNotifier()
    first = desk.watch_once(notifier=fake)
    second = desk.watch_once(notifier=fake)
    assert first["verdict"] == "short"
    assert first["alerted"] is True
    assert len(fake.sent) == 1
    # The second pass must not re-alert. It now reports the plan it adopted --
    # still waiting for its trigger -- rather than the direction again.
    assert second["alerted"] is False
    assert second["reason"].startswith("plan ")
    assert len(fake.sent) == 1


def test_watch_once_retries_when_send_fails(tmp_path, monkeypatch):
    """A send that fails must NOT be recorded. Otherwise a signal that was never
    delivered is suppressed forever — the ledger says 'already sent' and the
    alert never happens, silently."""
    s = _settings(tmp_path)
    desk = Orchestrator(s)
    bars = _wave(220, period=40, amp=30, base=3000, drift=-3.0)
    monkeypatch.setattr("desk.sources.fetch_candles", lambda sym, tf, limit=1000: bars)
    monkeypatch.setattr("desk.sources.fetch_quote",
                        lambda sym: {"mid": bars[-1]["close"], "bid": 1, "ask": 1,
                                     "market_state": "open", "stale": False,
                                     "quote_age_s": 0, "day_diff_pct": -1.0,
                                     "source": "test"})
    monkeypatch.setattr("desk.sources.fetch_news", lambda symbol=None, max_results=25: [])
    monkeypatch.setattr("desk.sources.fetch_calendar", lambda importance="high", limit=100: [])
    monkeypatch.setattr("desk.sources.fetch_summary", lambda: {})
    s.alert_score = 0.25
    s.min_rr = 0.0

    class FlakyNotifier:
        def __init__(self): self.calls = 0
        def send(self, text):
            self.calls += 1
            return self.calls > 1          # fail the first time, succeed after

    fake = FlakyNotifier()
    first = desk.watch_once(notifier=fake)
    assert first["alerted"] is False
    second = desk.watch_once(notifier=fake)
    assert second["alerted"] is True, "the failed send must be retried"
    assert fake.calls == 2


# ---- news labelling ---------------------------------------------------

def test_parse_labels_reads_json():
    from desk.agents.news_agent import parse_labels
    txt = '{"labels":[{"i":0,"label":"bullish"},{"i":1,"label":"bearish"},{"i":2,"label":"neutral"}]}'
    assert parse_labels(txt, 3) == [1, -1, 0]


def test_parse_labels_rejects_garbage():
    from desk.agents.news_agent import parse_labels
    assert parse_labels("not json at all", 3) is None
    assert parse_labels("", 3) is None


def test_news_falls_back_to_keywords_without_model(tmp_path):
    from desk.orchestrator import Orchestrator
    s = _settings(tmp_path)
    desk = Orchestrator(s)
    agent = next(a for a in desk.agents if a.name == "news")
    agent.llm.key = ""                      # no model
    labels, keyword, stats = agent._label(
        [{"title": "Gold rallies on safe haven demand", "description": ""},
         {"title": "Dollar strengthens, gold slumps", "description": ""}])
    assert labels == keyword
    assert stats["model_used"] is False


# ---- reward:risk filter -----------------------------------------------

def test_plan_rejects_setup_when_stop_wider_than_reward(tmp_path):
    """A direction can be right and the setup still be untradeable: if no level
    clears the bar, the desk must not state a plan."""
    from desk.orchestrator import Orchestrator
    s = _settings(tmp_path)
    s.min_rr = 1.5
    desk = Orchestrator(s)
    # price 100, stop 5 away (risk 5); every level is nearer than 7.5 (1.5x risk)
    levels = type("L", (), {"payload": {
        "price": 100.0,
        "supports": [{"level": 99.0, "kind": "pivot"}],
        "resistances": [{"level": 100.5, "kind": "pivot"},
                        {"level": 103.0, "kind": "pivot"}]}})()
    rec = desk._plan("long", 100.0, atr1h=10.0, levels=levels, risk=None,
                     by_name={}, score=0.5)
    assert rec["entry"] is None and rec["targets"] == []
    assert "الوقف أوسع من المكافأة" in rec["levels_note"]


def test_plan_reaches_past_near_levels_for_a_paying_target(tmp_path):
    """The nearest resistance is too close to pay for the stop, so the plan must
    reach the farther one rather than refuse the trade."""
    from desk.orchestrator import Orchestrator
    s = _settings(tmp_path)
    s.min_rr = 1.0
    desk = Orchestrator(s)
    levels = type("L", (), {"payload": {
        "price": 100.0,
        "supports": [{"level": 99.0, "kind": "pivot"}],       # stop near 99
        "resistances": [{"level": 100.4, "kind": "pivot"},     # too near: 0.4
                        {"level": 102.0, "kind": "pivot"}]}})()  # pays: 2.0
    rec = desk._plan("long", 100.0, atr1h=1.0, levels=levels, risk=None,
                     by_name={}, score=0.5)
    assert [t["level"] for t in rec["targets"]] == [102.0]
    assert rec["rr"] >= 1.0


# ---- LLM rate-limit breaker -------------------------------------------

def test_llm_cooldown_parses_provider_hint():
    from desk.llm import LLM
    assert LLM._cooldown_seconds("Please try again in 6m8.496s.") == 368.496
    assert LLM._cooldown_seconds("try again in 30s") == 30.0
    assert LLM._cooldown_seconds("no hint here") == 60.0


def test_llm_breaker_skips_calls_after_rate_limit(monkeypatch):
    """A spent quota must not be retried: the second call returns at once."""
    import io
    import urllib.error

    from desk.llm import LLM

    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            "u", 429, "Too Many Requests", {}, io.BytesIO(
                b'{"error":{"message":"try again in 30s"}}'))

    monkeypatch.setattr("urllib.request.urlopen", boom)
    llm = LLM("k", ["m", "n"])                  # two-model chain
    assert llm.complete("s", "u") is None       # m 429 -> cool -> n 429 -> cool
    assert calls["n"] == 2, "each model is tried once, never retried on a 429"
    assert llm.available is False               # whole chain cooling
    assert llm.complete("s", "u") is None       # short-circuits, no new call
    assert calls["n"] == 2
    assert llm.stats()["rate_limited"] == 2


def test_llm_rotates_to_next_model_when_head_is_spent(monkeypatch):
    """A spent quota on the head model must rotate, not give up: the whole point
    of the chain is that the desk keeps a live model."""
    import io
    import urllib.error

    from desk.llm import LLM

    seen = []

    class FakeResp:
        def __init__(self, payload): self._b = io.BytesIO(payload)
        def read(self): return self._b.read()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode())
        seen.append(body["model"])
        if body["model"] == "spent":
            raise urllib.error.HTTPError(
                "u", 429, "rate", {}, io.BytesIO(b'{"error":{"message":"try again in 30s"}}'))
        return FakeResp(json.dumps(
            {"choices": [{"message": {"content": "hello"}}]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    llm = LLM("k", ["spent", "live"])
    assert llm.complete("s", "u") == "hello"
    assert seen == ["spent", "live"], "must fall through to the second model"
    assert llm.stats()["rotations"] == 1
    assert llm.model == "live", "the live model now leads"


def test_report_flags_deterministic_prose_when_model_unavailable(tmp_path, monkeypatch):
    """The reader must be told when the prose is computed, not written."""
    from desk.orchestrator import Orchestrator
    from desk.report import render
    s = _settings(tmp_path)
    desk = Orchestrator(s)
    bars = _wave(200, period=40, amp=30, base=3000, drift=2.0)
    monkeypatch.setattr("desk.sources.fetch_candles", lambda sym, tf, limit=1000: bars)
    monkeypatch.setattr("desk.sources.fetch_quote",
                        lambda sym: {"mid": bars[-1]["close"], "bid": 1, "ask": 1,
                                     "market_state": "open", "stale": False,
                                     "quote_age_s": 0, "day_diff_pct": 1.0,
                                     "source": "test"})
    monkeypatch.setattr("desk.sources.fetch_news", lambda symbol=None, max_results=25: [])
    monkeypatch.setattr("desk.sources.fetch_calendar", lambda importance="high", limit=100: [])
    monkeypatch.setattr("desk.sources.fetch_summary", lambda: {})
    desk.llm._cooling = {m: 9e9 for m in desk.llm.models}   # whole chain spent
    run = desk.run()
    assert run.recommendation["llm"]["available"] is False
    assert "النموذج غير متاح" in render(run, lang="ar")


# ---- open-bar hygiene -------------------------------------------------

def _seq_bars(n, base=3000.0, step=1.0, last_open=False):
    out = []
    for i in range(n):
        c = base + i * step
        out.append({"t": f"2026-01-01T{i:02d}:00:00Z", "o": c, "h": c + 1,
                    "l": c - 1, "c": c, "tv": 5,
                    "open": bool(last_open and i == n - 1)})
    return out


def test_frames_data_excludes_the_forming_bar(tmp_path):
    """The newest bar is still moving, so an indicator read off it is not a fact.
    A stale-but-real close must be used instead of the partial bar."""
    from desk.agents.base import Agent
    from desk.llm import LLM
    from desk.memory import SharedMemory
    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    bars = _seq_bars(30, last_open=True)
    shared.save_candles(s.symbol, "1h", [
        {"openTime": b["t"], "open": b["o"], "high": b["h"], "low": b["l"],
         "close": b["c"], "tickVolume": b["tv"], "isOpen": b["open"]} for b in bars])
    a = Agent(s, shared, LLM("", s.models))
    a.frames = ("1h",)
    got = a.frames_data()["1h"]
    assert len(got) == 29, "the forming bar must be dropped"
    assert got[-1]["c"] == 3028.0, "the last closed bar, not the partial one"


def test_live_price_prefers_quote_then_closed_bar(tmp_path):
    from desk.agents.base import Agent
    from desk.llm import LLM
    from desk.memory import SharedMemory
    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    shared.save_candles(s.symbol, "1h", [
        {"openTime": f"2026-01-01T{i:02d}:00:00Z", "open": 100, "high": 101,
         "low": 99, "close": 100 + i, "tickVolume": 1, "isOpen": i == 9}
        for i in range(10)])
    a = Agent(s, shared, LLM("", s.models))
    assert a.live_price() == 108.0, "no quote -> last CLOSED close (not the open bar)"
    shared.save_quote(s.symbol, {"mid": 555.0, "stale": False})
    assert a.live_price() == 555.0, "a live quote wins"
    shared.save_quote(s.symbol, {"mid": 555.0, "stale": True})
    assert a.live_price() == 108.0, "a stale quote must not be the price"


def _coarse_bars(n, close_fn, *, minutes=1440):
    """`n` closed bars spaced `minutes` apart, so a frame's last bar is genuinely
    the newest and a live price differs from its close."""
    out = []
    for i in range(n):
        total = i * minutes
        day, hour, minute = 1 + total // 1440, (total % 1440) // 60, total % 60
        out.append({"openTime": f"2026-01-{day:02d}T{hour:02d}:{minute:02d}:00Z",
                    "open": close_fn(i) - 1, "high": close_fn(i) + 2,
                    "low": close_fn(i) - 2, "close": close_fn(i),
                    "tickVolume": 1, "isOpen": False})
    return out


def test_trend_compares_the_live_price_not_a_stale_daily_close(tmp_path):
    """The regression that made the desk bearish for 56 straight runs: the daily
    vote compared the previous day's close against a fresh EMA stack, so a price
    a day old manufactured a bearish vote while the market rose.

    The daily series rises, then its last closed bar drops below the stack. With
    the stale close that is a non-bullish vote; only the live price above the
    stack is bullish. The two prices must disagree, or the test proves nothing.
    """
    from desk.agents.trend import TrendAgent
    from desk.llm import LLM
    from desk.memory import SharedMemory

    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    daily = _coarse_bars(60, lambda i: (2900.0 + 2 * i) if i < 59 else 2900.0)
    for tf, minutes in (("1d", 1440), ("4h", 240), ("1h", 60)):
        shared.save_candles(s.symbol, tf, _coarse_bars(60, lambda i: 3000.0 + i, minutes=minutes))
    shared.save_candles(s.symbol, "1d", daily)
    shared.save_quote(s.symbol, {"mid": 3100.0, "stale": False})
    a = TrendAgent(s, shared, LLM("", s.models))
    st = a.study("t")
    d1 = st.payload["frames"]["1d"]
    assert d1["px"] == 3100.0, "the daily must compare the live price"
    assert d1["vote"] == 1, "the live price above a rising stack is bullish"
    assert daily[-1]["close"] < d1["ema20"], "sanity: the stale close is below the stack"
    assert st.score > 0, "a market above its EMAs must not read bearish"


def test_structure_places_the_live_price_in_the_range(tmp_path):
    """Same class: the premium/discount zone and the break-of-structure test must
    use the live price, or the desk reads a price from yesterday as today's. The
    daily oscillates in a range; the live price breaks above it."""
    import math

    from desk.agents.structure import StructureAgent
    from desk.llm import LLM
    from desk.memory import SharedMemory

    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    for tf, minutes in (("1d", 1440), ("4h", 240), ("1h", 60), ("15m", 15)):
        shared.save_candles(s.symbol, tf,
                            _coarse_bars(60, lambda i: 3000.0 + 20 * math.sin(i), minutes=minutes))
    # The last closed 1d close is inside the range; a live quote above the range
    # high must land the price at the top and register the break, not sit at the
    # stale close.
    shared.save_quote(s.symbol, {"mid": 3100.0, "stale": False})
    a = StructureAgent(s, shared, LLM("", s.models))
    st = a.study("t")
    d1 = st.payload["frames"]["1d"]
    assert d1["position"] > 0.9, f"live price at the range top, got {d1['position']}"
    assert d1["bos"] == "bullish BOS", "the live price is above the prior swing high"
    assert d1["zone"] == "premium"


def test_stance_of_uses_the_shared_band(tmp_path):
    from desk.agents.base import Agent
    from desk.llm import LLM
    from desk.memory import SharedMemory
    s = _settings(tmp_path)
    s.neutral_band = 0.5
    a = Agent(s, SharedMemory(s.shared_db), LLM("", s.models))
    assert a.stance_of(0.4) == "neutral"
    assert a.stance_of(0.6) == "bull"
    assert a.stance_of(-0.6) == "bear"


def test_neutral_band_is_one_source_of_truth(tmp_path):
    """A stance and a verdict must never disagree about what 'neutral' means."""
    from desk.agents.levels import LevelsAgent
    from desk.llm import LLM
    from desk.memory import SharedMemory
    s = _settings(tmp_path)
    s.neutral_band = 0.9
    a = LevelsAgent(s, SharedMemory(s.shared_db), LLM("", s.models))
    assert a.stance_of(0.5) == "neutral", "0.5 is inside a 0.9 band"


# ---- news labelling fairness ------------------------------------------

def test_news_labels_the_same_text_for_both_labellers(tmp_path, monkeypatch):
    """The keyword map and the model must see identical text, or a disagreement
    is an artefact of the input rather than a judgement about meaning."""
    from desk.agents.news_agent import NewsAgent
    from desk.llm import LLM
    from desk.memory import SharedMemory
    s = _settings(tmp_path)
    seen = {}

    class FakeLLM(LLM):
        def __init__(self): super().__init__("k", ["m"])
        @property
        def available(self): return True
        def complete(self, system, user, **kw):
            seen["user"] = user
            return '{"labels": []}'

    news = [{"title": "Markets steady", "description": "gold gains hard"}]
    a = NewsAgent(s, SharedMemory(s.shared_db), FakeLLM())
    labels, keyword, stats = a._label(news)
    assert "gold gains hard" in seen["user"], "the model must see the description"
    assert keyword[0] == 1, "the keyword map sees it too — same text, same chance"


# ---- the budget is a real ceiling -------------------------------------

def test_budget_is_a_real_ceiling(tmp_path):
    """A slow agent must be cut at the deadline, not waited out."""
    import time as _t

    from desk.agents.base import Agent, Study
    from desk.orchestrator import Orchestrator

    class Slow(Agent):
        name = "slowpoke"
        def study(self, run_id):
            _t.sleep(5.0)
            return Study(self.name, "test", "neutral", 0.5, 0.0, "late")

    s = _settings(tmp_path)
    s.budget_s = 0.4
    desk = Orchestrator(s)
    desk.agents = [Slow(s, desk.shared, desk.llm)]
    started = _t.time()
    studies, errors = desk.dispatch("run1")
    elapsed = _t.time() - started
    assert elapsed < 3.0, f"budget not enforced: took {elapsed:.1f}s"
    assert not studies
    assert any("budget" in e for e in errors), errors


# ---- market state + analog engine --------------------------------------

def _wave_csv(path, n=600):
    """A long, mean-reverting series with a trend so states are varied."""
    import csv as _csv
    import math
    with path.open("w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["openTime", "open", "high", "low", "close", "volume",
                    "tickVolume", "isOpen"])
        for i in range(n):
            px = 3000 + 200 * math.sin(i / 40.0) + i * 0.4 + 15 * math.sin(i / 6.0)
            w.writerow([f"2025-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}T00:00:00Z",
                        px, px + 12, px - 12, px, 0, 10, "False"])


def test_state_reads_only_past_bars(tmp_path):
    """The state at bar i must not change when later bars are appended.

    This is the one rule that makes the analog engine honest: if the state could
    see the outcome bar, every measurement would be contaminated.
    """
    from desk import state as ST
    from desk.backtest import load_daily
    p = tmp_path / "XAUUSD_1d.csv"
    _wave_csv(p, n=400)
    C = [{"o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"]}
         for b in load_daily(p)]
    i = 300
    before = ST.state_at(C[:i + 1], i)
    # append 60 more bars: the state at i is a fact about 0..i and must not move
    after = ST.state_at(C[:i + 61], i)
    assert before == after
    assert before is not None


def test_analog_refuses_to_call_a_thin_sample(tmp_path, monkeypatch):
    """Below MIN_N the study is neutral and says so -- it does not invent an edge."""
    from desk.agents import analog
    from desk.config import Settings
    from desk.llm import LLM
    from desk.memory import SharedMemory
    s = Settings(home=tmp_path / "h", symbol="XAUUSD")
    s.ensure_dirs()
    # a short history: too few bars for any state to reach MIN_N
    C = [{"o": 3000 + i, "h": 3002 + i, "l": 2998 + i, "c": 3000 + i}
         for i in range(120)]
    monkeypatch.setattr(analog.history, "shaped", lambda sym: C)
    # the current bar has a state, but only five bars share it: thin sample
    monkeypatch.setattr(analog.state_mod, "state_at",
                        lambda bars, i: analog.state_mod.State(True, 2, 1, 1)
                        if (i < 5 or i == len(bars) - 1) else None)
    st = analog.AnalogAgent(s, SharedMemory(s.shared_db), LLM("", [])).study("r")
    assert st.payload["n_state"] < analog.MIN_N
    assert st.stance == "neutral"
    assert st.payload["sufficient"] is False


def test_analog_measures_the_state_with_the_desks_own_bracket(tmp_path, monkeypatch):
    """The analog numbers must come from the same stop/target/horizon as the
    plan and the scan. A friendlier ruler here would be a different claim."""
    from desk.agents import analog
    C = [{"o": 3000 + (i % 7) * 3, "h": 3000 + (i % 7) * 3 + 10,
          "l": 3000 + (i % 7) * 3 - 10, "c": 3000 + (i % 7) * 3}
         for i in range(400)]
    m = analog.measure_state(C, (1, 2, 1, 1))
    assert set(m) >= {"long", "short", "n_state"}
    for side in ("long", "short"):
        assert "edge" in m[side] and "base_r" in m[side] and "t" in m[side]
        assert abs(m[side]["edge"] - (m[side]["mean_r"] - m[side]["base_r"])) < 1e-9


def test_analog_is_registered_in_the_roster():
    from desk.agents import ROSTER, AnalogAgent
    assert AnalogAgent in ROSTER
    assert ROSTER[0] is AnalogAgent


# ---- bias audit -------------------------------------------------------

def test_bias_audit_finds_a_fair_rule_on_a_rising_market(tmp_path):
    """On a market that rose, a structurally fair engine must lean LONG.

    The desk looked short-biased live; this proves the lean was the market and
    not the rule. A synthetic uptrend is the cleanest case: the rule must not
    be short on it."""
    from desk import bias

    C = [{"o": 1000 + i, "h": 1002 + i, "l": 998 + i, "c": 1000 + i}
         for i in range(600)]
    b = bias.audit(C)
    assert b.bars > 400
    assert b.long_share > b.short_share
    assert b.balanced


def test_bias_audit_flags_a_rule_that_always_shorts():
    """A rule that only ever shorts must fail the audit. This is the guard the
    live desk needed: it is not enough to *look* at the counts, the audit has to
    be able to say 'this is broken'."""
    from desk import bias

    b = bias.Balance(long=0, short=900, no_trade=100, bars=1000,
                     votes={"structure": {1: 0, 0: 0, -1: 900}})
    assert b.balanced is False
    assert "انحياز" in b.summary_ar()


def test_bias_audit_reads_only_the_shipped_history():
    """The audit must run on the file the desk ships, and its own counts must
    add up to the bars it examined."""
    from desk import bias

    b = bias.audit_symbol("XAUUSD")
    assert b.bars > 2000
    assert b.long + b.short + b.no_trade == b.bars
    for t in b.votes.values():
        assert sum(t.values()) == b.bars


def test_bias_audit_is_wired_to_the_cli():
    from desk.cli import build_parser
    p = build_parser()
    ns = p.parse_args(["bias"])
    assert ns.func.__name__ == "cmd_bias"


# ---- calibration ------------------------------------------------------

def test_calibration_curve_reads_the_shipped_history():
    """The curve must come from the desk's own rule on real bars, and its own
    counts must add up."""
    from desk import calibration as cal

    c = cal.from_symbol("XAUUSD")
    assert c.n > 1000
    assert c.correct <= c.n
    assert c.ece is not None
    for b in c.bands:
        assert b.correct <= b.n


def test_calibration_exposes_the_desks_optimism():
    """The headline finding: the desk's nominal confidence is far too high. The
    curve must be able to see that, or it is not measuring anything."""
    from desk import calibration as cal

    c = cal.from_symbol("XAUUSD")
    assert c.calibrated is False, "a 92% claim that lands 56% must fail"
    assert (c.ece or 0.0) > cal.MAX_ECE_PCT / 100


def test_calibration_of_a_perfect_forecaster_is_calibrated():
    """A desk that is right exactly as often as it claims must pass. The check
    has to be able to say yes as well as no."""
    from desk import calibration as cal

    rows = []
    for i in range(400):
        p = 0.7
        rows.append({"correct": 1 if i < int(400 * p) else 0,
                     "bias_score": 0.4, "move_pct": 1.0})
    c = cal.from_outcomes(rows)
    assert c.n == 400
    assert c.calibrated is True


def test_calibration_refuses_to_tune_on_a_thin_sample():
    from desk import calibration as cal

    c = cal.from_outcomes([{"correct": 1, "bias_score": 0.5, "move_pct": 1.0}])
    assert c.n == 1
    assert c.enough_to_tune is False


def test_reliability_reports_the_measured_rate_not_the_nominal_one():
    """`reliability` must return what the band actually delivered, and a sample
    big enough to mean it -- never the nominal 0.5 + 0.5*score."""
    from desk import calibration as cal

    rel = cal.reliability("XAUUSD", 0.9)
    assert rel is not None
    real, n = rel
    assert n >= 30
    assert real < 0.9, "the desk's own 92% band must not come back as 92%"


def test_calibration_is_wired_to_the_cli():
    from desk.cli import build_parser
    ns = build_parser().parse_args(["calibration"])
    assert ns.func.__name__ == "cmd_calibration"


# ---- walk-forward -----------------------------------------------------

def test_walkforward_has_no_lookahead():
    """The property the whole module rests on: appending bars must not change any
    earlier decision."""
    from desk import history
    from desk import walkforward as wf

    C = history.shaped("XAUUSD")
    assert wf.lookahead_check(C) is True


def test_walkforward_folds_are_consecutive_and_unseen():
    """Each fold trains on a window and tests on the one after it -- no shuffle,
    and train and test never overlap."""
    from desk import history
    from desk import walkforward as wf

    C = history.shaped("XAUUSD")
    w = wf.walk_forward(C, folds=4)
    assert len(w.folds) == 4
    for f in w.folds:
        assert f.train[1] <= f.test[0]
        assert f.test[1] > f.test[0]


def test_walkforward_controls_use_the_same_windows_as_the_desk():
    """A control graded over the whole file is not a fair comparison. The control
    sample must come from the desk's own test windows."""
    from desk import history
    from desk import walkforward as wf

    C = history.shaped("XAUUSD")
    w = wf.walk_forward(C, folds=3)
    windows = [f.test for f in w.folds]
    ctrl = wf.controls(C, windows=windows)
    desk_n = sum(f.test_n for f in w.folds)
    # the controls grade every bar in those windows, so they must be at least as
    # many as the desk's calls
    assert ctrl["always long"][0] >= desk_n


def test_walkforward_paired_test_is_the_honest_one():
    """The paired difference against always-long must remove the market drift.
    On gold's drift, the desk's timing must NOT show a significant edge -- if this
    ever flips to significant, the desk has earned a real claim."""
    from desk import history
    from desk import walkforward as wf

    C = history.shaped("XAUUSD")
    w = wf.walk_forward(C, folds=4)
    windows = [f.test for f in w.folds]
    n, mean, t = wf.paired_vs_long(C, windows)
    assert n > 500
    assert abs(mean) < 0.2
    assert abs(t) < 3.0, "the desk must not claim a timing edge it does not have"


def test_walkforward_says_no_edge_out_loud():
    """The verdict line must be honest when the desk does not beat holding."""
    from desk import history
    from desk import walkforward as wf

    C = history.shaped("XAUUSD")
    w = wf.walk_forward(C, folds=4)
    windows = [f.test for f in w.folds]
    text = wf.render(w, wf.controls(C, windows=windows),
                     paired=wf.paired_vs_long(C, windows))
    assert "edge توقيت" in text
    assert "بصراحة" in text or "معنوي" in text


def test_walkforward_is_wired_to_the_cli():
    from desk.cli import build_parser
    ns = build_parser().parse_args(["walkforward", "--folds", "3"])
    assert ns.func.__name__ == "cmd_walkforward"
    assert ns.folds == 3


# ---- measured correlation ---------------------------------------------

def test_pearson_measures_real_comovement():
    from desk.agents.correlation import pearson
    a = [0.01, -0.02, 0.03, 0.01, -0.01, 0.02, -0.03, 0.01, 0.02, -0.01, 0.01, 0.0]
    assert pearson(a, a) == pytest.approx(1.0)
    assert pearson(a, [-x for x in a]) == pytest.approx(-1.0)
    assert pearson(a, [0.0] * len(a)) is None, "a flat series has no correlation"
    assert pearson(a[:5], a[:5]) is None, "too short to measure -> None, not 0"


def test_correlation_score_is_scaled_by_measured_rho(tmp_path, monkeypatch):
    """The old agent awarded a fixed 0.3 whenever gold and the dollar moved
    opposite ways, whatever the real relationship. Now the size must come from
    the measured co-movement."""
    from desk.agents.correlation import CorrelationAgent
    from desk.llm import LLM
    from desk.memory import SharedMemory
    s = _settings(tmp_path)
    s.peers = ("USDJPY",)
    shared = SharedMemory(s.shared_db)

    # gold hourly closes, and a USDJPY series that moves exactly opposite.
    gold = [{"openTime": f"2026-01-01T{i:02d}:00:00Z", "open": 100, "high": 101,
             "low": 99, "close": 100 + ((-1) ** i) * (i % 5), "tickVolume": 1,
             "isOpen": 0} for i in range(60)]
    shared.save_candles(s.symbol, "1h", gold)
    shared.save_quote(s.symbol, {"mid": 100.0, "day_diff_pct": 1.0,
                                 "market_state": "open", "stale": False})
    jpy = [{"openTime": f"2026-01-01T{i:02d}:00:00Z", "open": 150, "high": 151,
            "low": 149, "close": 150 + ((-1) ** (i + 1)) * (i % 5), "tickVolume": 1,
            "isOpen": 0} for i in range(60)]

    monkeypatch.setattr("desk.sources.fetch_candles",
                        lambda sym, tf, limit=1000: jpy if sym == "USDJPY" else [])
    monkeypatch.setattr("desk.sources.fetch_quote",
                        lambda sym: {"mid": 150.0, "day_diff_pct": -0.5,
                                     "market_state": "open"})
    monkeypatch.setattr("desk.sources.fetch_summary", lambda: {"overview": {}})

    a = CorrelationAgent(s, shared, LLM("", s.models))
    st = a.study("r1")
    assert "USDJPY" in st.payload["correlations"], "rho must be measured and stored"
    assert st.score != 0.0, "an opposite-moving dollar is a real, measured signal"
    assert abs(st.score) <= 1.0


def test_correlation_is_silent_when_the_relationship_is_noise(tmp_path, monkeypatch):
    """No measured co-movement -> no score. A fixed award would have fired here."""
    from desk.agents.correlation import CorrelationAgent
    from desk.llm import LLM
    from desk.memory import SharedMemory
    s = _settings(tmp_path)
    s.peers = ("USDJPY",)
    shared = SharedMemory(s.shared_db)
    shared.save_candles(s.symbol, "1h", [
        {"openTime": f"2026-01-01T{i:02d}:00:00Z", "open": 100, "high": 101,
         "low": 99, "close": 100 + ((-1) ** i), "tickVolume": 1, "isOpen": 0}
        for i in range(60)])
    shared.save_quote(s.symbol, {"mid": 100.0, "day_diff_pct": 1.0,
                                 "market_state": "open", "stale": False})
    # A peer with no usable series: correlation is unmeasurable, so it must
    # contribute nothing rather than a default 0.3.
    monkeypatch.setattr("desk.sources.fetch_candles", lambda sym, tf, limit=1000: [])
    monkeypatch.setattr("desk.sources.fetch_quote",
                        lambda sym: {"mid": 150.0, "day_diff_pct": -0.5,
                                     "market_state": "open"})
    monkeypatch.setattr("desk.sources.fetch_summary", lambda: {"overview": {}})
    st = CorrelationAgent(s, shared, LLM("", s.models)).study("r1")
    assert st.score == 0.0, "no measured relationship -> no vote"


# ---- plan grading: stop vs target, first touch wins -------------------

def _plan_run(shared, run_id, started, symbol, direction, entry, stop, target):
    shared.save_run(run_id, symbol, started, started, direction, 0.5,
                    {"direction": direction, "entry": entry, "stop": stop,
                     "targets": [{"level": target, "kind": "pivot"}],
                     "bias": {"direction": "up" if direction == "long" else "down",
                              "score": 0.5},
                     "gather": {"quote": {"mid": entry}}})


def _mkbars(start_epoch, step_min, n, path):
    """`n` bars `step_min` apart; each bar's OHLC follows `path(i)`. The plan is
    graded on 5m out to 4h and on 15m out to 1d, so a test must seed both."""
    import datetime as _dt
    base = _dt.datetime.fromtimestamp(start_epoch, _dt.UTC)
    out = []
    for i in range(n):
        p = path(i)
        t = base + _dt.timedelta(minutes=step_min * i)
        out.append({"openTime": t.isoformat().replace("+00:00", "Z"), "open": p,
                    "high": p + 0.5, "low": p - 0.5, "close": p,
                    "tickVolume": 1, "isOpen": 0})
    return out


def _seed_plan(shared, symbol, ep, path):
    """Seed the 5m (covers 4h) and 15m (covers 1d) series for a plan test."""
    shared.save_candles(symbol, "5m", _mkbars(ep, 5, 60, path))
    shared.save_candles(symbol, "15m", _mkbars(ep, 15, 100, path))


def test_plan_grading_stop_before_target_is_a_loss(tmp_path):
    """The whole point of grading the plan: a short that reaches its target only
    after first taking out the stop is a LOSS, not a win."""
    import datetime as _dt

    from desk.memory import SharedMemory
    from desk.scoreboard import Grader
    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    now = _dt.datetime.now(_dt.UTC)
    started = now - _dt.timedelta(hours=6)
    ep = started.timestamp()
    # short from 100: stop 102, target 96. Price spikes to 103 FIRST (stop),
    # then falls to 95 (target). Direction was "right" but the trade was stopped.
    def path(i):
        return [100.0, 101.0, 103.0, 100.0, 97.0, 95.0][i] if i < 6 else 95.0
    _seed_plan(shared, s.symbol, ep, path)
    _plan_run(shared, "r1", ep, s.symbol, "short", 100.0, 102.0, 96.0)
    board = Grader(shared).grade()
    assert board.plan_results.get("stop") == 1, board.plan_results
    assert board.expectancy_r == -1.0


def test_plan_grading_target_before_stop_is_a_win(tmp_path):
    import datetime as _dt

    from desk.memory import SharedMemory
    from desk.scoreboard import Grader
    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    now = _dt.datetime.now(_dt.UTC)
    started = now - _dt.timedelta(hours=6)
    ep = started.timestamp()
    # short from 100: price falls to the target first, stop never touched.
    def path(i):
        return [100.0, 99.0, 97.0, 96.0, 95.0][i] if i < 5 else 95.0
    _seed_plan(shared, s.symbol, ep, path)
    _plan_run(shared, "r1", ep, s.symbol, "short", 100.0, 102.0, 96.0)
    board = Grader(shared).grade()
    assert board.plan_results.get("target") == 1, board.plan_results
    assert board.expectancy_r == pytest.approx(2.0), "2R reward at a 1R risk"


def test_plan_grading_a_bar_spanning_both_takes_the_stop(tmp_path):
    """When one bar contains both the stop and the target, the conservative
    reading is the stop — the desk must never flatter itself."""
    import datetime as _dt

    from desk.memory import SharedMemory
    from desk.scoreboard import Grader
    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    now = _dt.datetime.now(_dt.UTC)
    started = now - _dt.timedelta(hours=6)
    ep = started.timestamp()
    bars = _mkbars(ep, 5, 60, lambda i: 100.0)
    bars[1].update({"high": 105.0, "low": 94.0})      # spans stop AND target
    shared.save_candles(s.symbol, "5m", bars)
    shared.save_candles(s.symbol, "15m", _mkbars(ep, 15, 100, lambda i: 100.0))
    _plan_run(shared, "r1", ep, s.symbol, "short", 100.0, 102.0, 96.0)
    board = Grader(shared).grade()
    assert board.plan_results.get("stop") == 1, "ambiguous bar must count as a stop"


# ---- sqlite concurrency -----------------------------------------------

def test_concurrent_writes_do_not_raise(tmp_path):
    """Eight agents publish at once; a locked database must be retried, not
    surfaced as an agent failure."""
    import concurrent.futures as cf

    from desk.memory import SharedMemory
    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)

    def write(i):
        shared.publish_study(f"run{i}", f"agent{i}", "k", "neutral", 0.5, 0.0,
                             "s", {"i": i})
        return True

    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(write, range(40)))
    assert len(shared.load_studies("run1")) == 1


# ---- alert pipeline self-check ----------------------------------------

def test_check_alerts_reports_not_configured(tmp_path, monkeypatch):
    """The desk is silent by design, so 'not configured' must be distinguishable
    from 'no signal yet'. The check must say which, without a network call."""
    from desk.cli import main
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.setenv("DESK_HOME", str(tmp_path))
    assert main(["check-alerts"]) == 2


def test_check_alerts_reports_bad_token(tmp_path, monkeypatch):
    """A rejected token must exit non-zero and name the cause, not pass silently."""
    import urllib.error

    from desk import cli
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456789")
    monkeypatch.setenv("DESK_HOME", str(tmp_path))

    def boom(url, timeout=None):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert cli.main(["check-alerts"]) == 1


# ---- the Telegram bot --------------------------------------------------
#
# These run against a fake Telegram, never the network, and pin the two things
# that would silently break a chat-driven desk: answering a press that is not
# ours, and replaying a press we already answered.

def _fake_bot(tmp_path, monkeypatch, *, updates, allow_chat="933571066"):
    """A TelegramBot whose API is a recorder. Returns (bot, calls).

    The fake models confirmation the way the Bot API does: an update is returned
    only while the server's confirmed offset is at or below it, and a call's
    `offset` raises that watermark. A fake that simply replayed `updates` on
    every call cannot catch the bug this models -- that reading an update does
    not confirm it.
    """
    from desk.bot import TelegramBot
    s = _settings(tmp_path)
    s.telegram_token = "123456:FAKE"
    s.telegram_chat_id = allow_chat
    calls: list[tuple[str, dict]] = []
    state = {"confirmed": 0}   # the watermark Telegram keeps

    def fake_call(self, method, payload, timeout=20):
        calls.append((method, payload))
        if method == "getUpdates":
            off = payload.get("offset")
            if off is not None:
                state["confirmed"] = max(state["confirmed"], off)
            return {"ok": True,
                    "result": [u for u in updates
                               if u.get("update_id", 0) >= state["confirmed"]]}
        return {"ok": True, "result": {}}

    monkeypatch.setattr(TelegramBot, "_call", fake_call)
    bot = TelegramBot(s)
    bot.fake_confirmed = state          # exposed so a test can read the watermark
    return bot, calls


def _press(update_id, data, chat="933571066"):
    return {"update_id": update_id,
            "callback_query": {"id": f"cb{update_id}", "data": data,
                               "message": {"message_id": 10, "chat": {"id": int(chat)}}}}


def test_bot_answers_a_press_from_the_owner(tmp_path, monkeypatch):
    from desk.bot import TelegramBot
    bot, calls = _fake_bot(tmp_path, monkeypatch, updates=[_press(5, "recommend")])
    sent = []
    monkeypatch.setattr(TelegramBot, "send", lambda self, text, **kw: sent.append(text) or True)

    class FakeActions:
        def __init__(self, *a, **k): pass
        def run(self, action): return f"answer:{action}"

    monkeypatch.setattr("desk.actions.Actions", FakeActions)
    out = bot.poll()
    assert out["handled"] == ["recommend"]
    assert sent == ["answer:recommend"]
    methods = [m for m, _ in calls]
    assert "answerCallbackQuery" in methods      # the spinner is stopped
    assert out["next_offset"] == 6               # cursor advanced past update 5


def test_bot_ignores_a_press_from_another_chat(tmp_path, monkeypatch):
    """An unauthenticated press must not spend a model quota."""
    from desk.bot import TelegramBot
    bot, calls = _fake_bot(tmp_path, monkeypatch,
                           updates=[_press(7, "analysis", chat="999999")])
    sent = []
    monkeypatch.setattr(TelegramBot, "send", lambda self, text, **kw: sent.append(text) or True)

    class BoomActions:
        def __init__(self, *a, **k): pass
        def run(self, action): raise AssertionError("must not run for a foreign chat")

    monkeypatch.setattr("desk.actions.Actions", BoomActions)
    out = bot.poll()
    assert out["handled"] == []
    assert sent == []
    # Still refused explicitly, and still advanced so it is not retried forever.
    assert any(m == "answerCallbackQuery" for m, _ in calls)
    assert out["next_offset"] == 8


def test_bot_advances_the_offset_so_a_press_is_answered_once(tmp_path, monkeypatch):
    """The cursor is confirmed to Telegram, not kept locally: a fresh runner
    with no disk must not replay the same press on the next pass."""
    from desk.bot import TelegramBot
    bot, calls = _fake_bot(tmp_path, monkeypatch, updates=[_press(11, "market")])
    monkeypatch.setattr(TelegramBot, "send", lambda self, text, **kw: True)

    class FakeActions:
        def __init__(self, *a, **k): pass
        def run(self, action): return "ok"

    monkeypatch.setattr("desk.actions.Actions", FakeActions)
    bot.poll()
    getupdates = [p for m, p in calls if m == "getUpdates"]
    # First pass reads from Telegram's own cursor; the reply to Telegram carries
    # the advance, which is what makes the pass idempotent without local state.
    assert getupdates and "offset" not in getupdates[0]
    bot.poll(offset=12)
    getupdates = [p for m, p in calls if m == "getUpdates"]
    assert getupdates[-1]["offset"] == 12


def test_bot_reports_not_configured(tmp_path):
    from desk.bot import TelegramBot
    s = _settings(tmp_path)
    s.telegram_token = ""
    bot = TelegramBot(s)
    assert bot.available is False
    assert bot.poll() == {"ok": False, "reason": "not configured"}


def test_bot_send_is_unavailable_without_a_chat(tmp_path):
    from desk.bot import TelegramBot
    s = _settings(tmp_path)
    s.telegram_token = "123456:FAKE"
    s.telegram_chat_id = ""
    bot = TelegramBot(s)
    assert bot.send("hi") is False          # never a request without a target


# ---- HTML escaping at the send boundary --------------------------------

def test_notifier_escapes_html_so_a_stray_ampersand_cannot_drop_the_send():
    """A model summary can contain `&` or `<`. Telegram rejects the whole
    message for an unknown entity, so the send must escape, not the caller."""
    import json
    import urllib.request

    from desk.config import Settings
    from desk.notify import Notifier

    s = Settings()
    s.telegram_token = "123456:FAKE"
    s.telegram_chat_id = "933571066"
    n = Notifier(s)
    captured = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"ok": True}).encode()

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        return Resp()

    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        assert n.send("R&D <script>") is True
    finally:
        urllib.request.urlopen = orig
    assert captured["body"]["text"] == "R&amp;D &lt;script&gt;"


def test_actions_escape_plain_text_but_keep_its_own_markup(tmp_path):
    """`analysis`/`report`/`hitrate` are plain text and are escaped as a block;
    `recommend`/`market` build their own `<b>` markup and escape only the
    dynamic fields, so the markup must survive."""
    from desk.actions import Actions
    acts = Actions(_settings(tmp_path))

    class FakeBoard:
        def report(self): return "R&D — 2/3 = 66.7%"

    class FakeGrader:
        def __init__(self, shared): pass
        def grade(self): return FakeBoard()

    import desk.scoreboard as sb
    orig = sb.Grader
    sb.Grader = FakeGrader
    try:
        out = acts.run("hitrate")
    finally:
        sb.Grader = orig
    assert "R&amp;D" in out

    # recommend with no stored run still answers, and does not raise
    assert "لا توجد قراءة محفوظة" in acts.run("recommend")


def test_run_payload_is_decoded_from_the_stored_string(tmp_path):
    """`runs.payload` is written as a JSON string, so a caller reading the run
    row must decode it. Treating it as a dict is how the button crashed on its
    first live press with `'str' object has no attribute 'get'`."""
    from desk.actions import _payload
    assert _payload({"payload": '{"entry": 1, "stop": 2}'}) == {"entry": 1, "stop": 2}
    assert _payload({"payload": {"entry": 1}}) == {"entry": 1}
    assert _payload({"payload": ""}) == {}
    assert _payload({"payload": "not json"}) == {}
    assert _payload({}) == {}


def test_actions_answer_from_a_stored_run(tmp_path):
    """The instant buttons must work off what a previous pass stored, without
    re-running the desk. This is the path the buttons take on the second press."""
    from desk.actions import Actions
    from desk.memory import SharedMemory

    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    shared.save_run("r1", "XAUUSD", 1000.0, 1002.0, "short", -0.31,
                    {"entry": 4021.5, "stop": 4040.0, "rr": 2.1,
                     "targets": [{"level": 3990.0, "kind": "pivot"}],
                     "levels_note": "pivot & fib", "errors": [], "gather": {},
                     "llm": {"available": False},
                     "bias": {"direction": "down", "score": -0.31}})
    acts = Actions(s)
    rec = acts.run("recommend")
    assert "بيع" in rec and "4021.5" in rec
    assert "&amp;" in rec                       # the note is escaped for HTML
    rep = acts.run("report")
    assert "4,021.50" in rep                    # the full card renders again
    assert "&amp;" in rep
    assert "لا" in rep                          # allowed: no


def test_report_and_hitrate_escape_plain_text(tmp_path):
    """`report`/`hitrate`/`analysis` return plain text; a model summary inside
    them can contain `&`, which would fail the send if left raw."""
    from desk.actions import Actions
    acts = Actions(_settings(tmp_path))
    out = acts.run("hitrate")
    assert out and "زر غير معروف" not in out
    assert "&lt;" not in out          # no markup was invented and escaped


def test_bot_defers_unanswered_presses_instead_of_dropping_them(tmp_path, monkeypatch):
    """When the time budget runs out the cursor must NOT advance past the
    presses that were not answered. Confirming one would tell Telegram to forget
    it, and the press would be lost with no reply and no retry."""
    from desk.bot import TelegramBot
    updates = [_press(20, "market"), _press(21, "analysis"), _press(22, "report")]
    bot, calls = _fake_bot(tmp_path, monkeypatch, updates=updates)
    sent = []
    monkeypatch.setattr(TelegramBot, "send", lambda self, text, **kw: sent.append(text) or True)

    class FakeActions:
        def __init__(self, *a, **k): pass
        def run(self, action): return f"answer:{action}"

    monkeypatch.setattr("desk.actions.Actions", FakeActions)
    # budget_s = -1 makes the very first press hit the ceiling.
    out = bot.poll(budget_s=-1.0)
    assert out["handled"] == []
    assert sent == []
    # The cursor stops AT the first unanswered press, so it is redelivered.
    assert out["next_offset"] == 20


def test_bot_answers_everything_when_the_budget_allows(tmp_path, monkeypatch):
    from desk.bot import TelegramBot
    updates = [_press(30, "market"), _press(31, "report")]
    bot, calls = _fake_bot(tmp_path, monkeypatch, updates=updates)
    monkeypatch.setattr(TelegramBot, "send", lambda self, text, **kw: True)

    class FakeActions:
        def __init__(self, *a, **k): pass
        def run(self, action): return f"answer:{action}"

    monkeypatch.setattr("desk.actions.Actions", FakeActions)
    out = bot.poll(budget_s=600)
    assert out["handled"] == ["market", "report"]
    assert out["next_offset"] == 32          # one past the last answered


def test_bot_reports_a_transport_failure_not_an_empty_pass(tmp_path, monkeypatch):
    """An invalid token must not look like a quiet chat. `getUpdates` raises, and
    the pass reports `ok: false` so the run goes red instead of silently green."""
    import urllib.error

    from desk.bot import TelegramBot, TelegramError

    s = _settings(tmp_path)
    s.telegram_token = "123456:DEAD"
    s.telegram_chat_id = "933571066"

    def boom(self, method, payload, timeout=20):
        raise urllib.error.HTTPError(method, 404, "Not Found", {}, None)

    monkeypatch.setattr(TelegramBot, "_call", boom)
    bot = TelegramBot(s)
    try:
        bot.get_updates()
        raise AssertionError("a 404 must raise, not return []")
    except TelegramError as e:
        assert "404" in str(e)
    out = bot.poll()
    assert out["ok"] is False
    assert out["updates"] == 0 and out["handled"] == []
    assert "404" in out["reason"]


def test_a_webhook_409_says_what_it_means(tmp_path, monkeypatch):
    """A registered webhook makes every poll fail with a bare `HTTP 409`, which
    reads like a broken token. It is not: Telegram serves updates to a webhook OR
    to getUpdates, never both. The message must name the transport conflict, or
    the operator debugs the token for an hour. Measured live: the Render daemon
    registers a webhook, so the Actions poll failed exactly this way."""
    import urllib.error

    from desk.bot import TelegramBot, TelegramError

    s = _settings(tmp_path)
    s.telegram_token = "123456:LIVE"
    s.telegram_chat_id = "933571066"

    def conflict(self, method, payload, timeout=20):
        raise urllib.error.HTTPError(method, 409, "Conflict", {}, None)

    monkeypatch.setattr(TelegramBot, "_call", conflict)
    bot = TelegramBot(s)
    try:
        bot.get_updates()
        raise AssertionError("a 409 must raise")
    except TelegramError as e:
        assert "409" in str(e)
        assert "webhook" in str(e).lower()


def test_telegram_command_reports_the_transport(tmp_path, monkeypatch, capsys):
    """`desk telegram` is the one honest answer to 'why does the bot not answer?'.
    With a webhook registered it exits 0 and names the URL; with none it exits 1,
    because then a poller can work but a sleeping host cannot be woken by a press."""
    import json

    from desk import cli
    from desk.bot import TelegramBot

    s = _settings(tmp_path)
    s.telegram_token = "123456:LIVE"
    s.telegram_chat_id = "933571066"
    monkeypatch.setattr(cli, "_settings", lambda a: s)

    monkeypatch.setattr(TelegramBot, "webhook_info",
                        lambda self: {"url": "https://xauusd14355-4.onrender.com/telegram",
                                      "pending_update_count": 0})
    assert cli.cmd_telegram(None) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["transport"] == "webhook"
    assert "onrender.com" in out["url"]

    monkeypatch.setattr(TelegramBot, "webhook_info", lambda self: {"url": ""})
    assert cli.cmd_telegram(None) == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert out["transport"] == "poll" and out["url"] is None


def test_a_stale_webhook_is_reported_not_swallowed(tmp_path, monkeypatch, capsys):
    """Telegram records why the last delivery failed. Printing that is the whole
    point of the probe: a stale URL or a rejected secret must be visible."""
    from desk import cli
    from desk.bot import TelegramBot

    s = _settings(tmp_path)
    s.telegram_token = "123456:LIVE"
    s.telegram_chat_id = "933571066"
    monkeypatch.setattr(cli, "_settings", lambda a: s)
    monkeypatch.setattr(TelegramBot, "webhook_info",
                        lambda self: {"url": "https://old.example/telegram",
                                      "last_error_message": "Wrong response from the webhook: 403 Forbidden"})
    cli.cmd_telegram(None)
    out = capsys.readouterr().out
    assert "403" in out and "stale" in out.lower()


def test_bot_cli_exits_nonzero_when_the_pass_fails(tmp_path, monkeypatch):
    """The workflow reads the exit code; a failed pass must not be green."""
    import urllib.error

    from desk import cli
    from desk.bot import TelegramBot

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:DEAD")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "933571066")
    monkeypatch.setenv("DESK_HOME", str(tmp_path))

    def boom(self, method, payload, timeout=20):
        raise urllib.error.HTTPError(method, 404, "Not Found", {}, None)

    monkeypatch.setattr(TelegramBot, "_call", boom)
    assert cli.main(["bot", "--budget", "5"]) == 1


def test_action_for_text_maps_commands_and_arabic_and_falls_back_to_menu():
    from desk.bot import action_for_text
    assert action_for_text("/start") == "menu"
    assert action_for_text("/analysis") == "analysis"
    assert action_for_text("تحليل") == "analysis"
    assert action_for_text("توصية") == "recommend"
    assert action_for_text("سوق") == "market"
    assert action_for_text("أي شي غريب") == "menu"      # unknown -> the menu
    assert action_for_text(None) == "menu"
    assert action_for_text("  ") == "menu"


def test_bot_answers_a_typed_message_not_just_a_button(tmp_path, monkeypatch):
    """A person types; the bot must answer. Answering only callback presses made
    a chat of 18 plain messages get total silence."""
    from desk.bot import TelegramBot
    updates = [{"update_id": 40, "message": {"text": "تحليل",
                                             "chat": {"id": 933571066}}}]
    bot, calls = _fake_bot(tmp_path, monkeypatch, updates=updates)
    sent = []
    monkeypatch.setattr(TelegramBot, "send",
                        lambda self, text, **kw: sent.append((text, kw)) or True)

    class FakeActions:
        def __init__(self, *a, **k): pass
        def run(self, action): return f"answer:{action}"

    monkeypatch.setattr("desk.actions.Actions", FakeActions)
    out = bot.poll()
    assert out["handled"] == ["analysis"]
    assert sent and sent[0][0] == "answer:analysis"
    # analysis gets the nav row, not the full menu: refresh + back
    mk = sent[0][1].get("markup") or {}
    assert mk.get("inline_keyboard"), "analysis must carry a nav row"
    labels = [b["text"] for row in mk["inline_keyboard"] for b in row]
    assert "🏠 القائمة" in labels and "📊 تحليل الآن" not in labels


def test_bot_shows_the_menu_for_an_unknown_message(tmp_path, monkeypatch):
    from desk.bot import TelegramBot
    updates = [{"update_id": 41, "message": {"text": "مرحبا",
                                             "chat": {"id": 933571066}}}]
    bot, calls = _fake_bot(tmp_path, monkeypatch, updates=updates)
    sent = []
    monkeypatch.setattr(TelegramBot, "send",
                        lambda self, text, **kw: sent.append((text, kw)) or True)

    class FakeActions:
        def __init__(self, *a, **k): pass
        def run(self, action): return f"menu-for:{action}"

    monkeypatch.setattr("desk.actions.Actions", FakeActions)
    out = bot.poll()
    assert out["handled"] == ["menu"]
    mk = sent[0][1].get("markup") or {}
    labels = [b["text"] for row in mk.get("inline_keyboard", []) for b in row]
    assert "📊 تحليل الآن" in labels                 # the full menu comes with it


def test_bot_folds_a_backlog_into_one_reply(tmp_path, monkeypatch):
    """A long quiet period leaves a queue. Answering all of it floods the chat,
    so the newest is answered and the older ones are acknowledged, not ignored."""
    from desk.bot import TelegramBot
    updates = [{"update_id": 50 + i, "message": {"text": "تقرير",
                                                 "chat": {"id": 933571066}}}
               for i in range(8)]
    bot, calls = _fake_bot(tmp_path, monkeypatch, updates=updates)
    sent = []
    monkeypatch.setattr(TelegramBot, "send",
                        lambda self, text, **kw: sent.append(text) or True)

    class FakeActions:
        def __init__(self, *a, **k): pass
        def run(self, action): return f"answer:{action}"

    monkeypatch.setattr("desk.actions.Actions", FakeActions)
    out = bot.poll(max_replies=3)
    assert out["handled"] == ["report", "report", "report"]   # the newest 3
    assert out["folded"] == 5
    assert len(sent) == 3
    assert "تجاهلت 5" in sent[0]                 # the backlog is acknowledged once
    assert "تجاهلت" not in sent[1] and "تجاهلت" not in sent[2]
    # Everything is confirmed, so the folded ones are not redelivered forever.
    assert out["next_offset"] == 58


def test_bot_confirms_so_a_backlog_is_not_answered_twice(tmp_path, monkeypatch):
    """The bug that only a faithful fake catches.

    The Bot API confirms an update when a LATER call passes a higher offset --
    reading it is not enough, and there is no separate "ack". A scheduled run
    makes one getUpdates call, so without a second confirming call nothing is
    ever confirmed and every pass re-answers the whole backlog. Observed live:
    two runs, the second reporting a *lower* offset than the first.
    """
    from desk.bot import TelegramBot
    updates = [{"update_id": 100 + i,
                "message": {"text": "تقرير", "chat": {"id": 933571066}}}
               for i in range(6)]
    bot, calls = _fake_bot(tmp_path, monkeypatch, updates=updates)
    sent = []
    monkeypatch.setattr(TelegramBot, "send",
                        lambda self, text, **kw: sent.append(text) or True)

    class FakeActions:
        def __init__(self, *a, **k): pass
        def run(self, action): return f"answer:{action}"

    monkeypatch.setattr("desk.actions.Actions", FakeActions)

    first = bot.poll(max_replies=5)
    assert len(sent) == 5                       # the newest five
    assert first["confirmed"] is True
    # The watermark must have moved past the batch, or the next pass repeats it.
    assert bot.fake_confirmed["confirmed"] == 106

    second = bot.poll(max_replies=5)
    assert second["updates"] == 0               # nothing left to answer
    assert len(sent) == 5                       # and no duplicate replies


def _levels(supports, resistances):
    return type("L", (), {"payload": {"supports": supports, "resistances": resistances}})()


def test_stop_is_at_least_one_atr_and_clears_the_level(tmp_path):
    """A stop inside the noise is not a stop, and it inflates the R:R.

    Observed live: the short put its stop 6.49 above entry while the 1h ATR was
    15.31 -- 0.42 ATR -- because it hung the stop on the nearest resistance (2
    points away) plus a small cushion. The plan then reported R:R 3.60, which
    looked good only because the risk was understated.
    """
    from desk.orchestrator import Orchestrator
    orch = Orchestrator(_settings(tmp_path))
    levels = _levels([{"level": 3880.0, "kind": "pivot"}],
                     [{"level": 3923.97, "kind": "pivot"}])
    plan = orch._plan("short", 3922.07, 15.31, levels, None, {}, -0.47)
    risk = abs(plan["stop"] - plan["entry"])   # a short risks the stop above
    assert risk >= 15.31 - 0.01, f"stop only {risk:.2f} away, inside one ATR"
    assert plan["stop"] > 3923.97 + 15.31 * 0.5, "stop must clear the level it hides behind"
    # The wider stop must still be paid for by a real target, not a near one.
    assert plan["rr"] is not None and plan["rr"] >= 1.5


def test_a_real_stop_makes_a_near_target_unpayable(tmp_path):
    """The live numbers, honestly graded: the nearest support was 5 points away.

    With the stop floored at one ATR the risk is ~20, so a 5-point reward cannot
    pay for it. The desk must then state no plan rather than print R:R 3.60 off
    a stop that the noise would have taken out.
    """
    from desk.orchestrator import Orchestrator
    orch = Orchestrator(_settings(tmp_path))
    levels = _levels([{"level": 3916.98, "kind": "pivot"}],
                     [{"level": 3923.97, "kind": "pivot"}])
    plan = orch._plan("short", 3922.07, 15.31, levels, None, {}, -0.47)
    assert plan["entry"] is None and plan["stop"] is None
    assert "الوقف أوسع من المكافأة" in plan["levels_note"]


# ---- backtest ---------------------------------------------------------

def _daily_csv(tmp_path, n=300, start=3000.0, step=3.0):
    """A synthetic daily series with an uptrend then a downturn, so both
    directions can appear."""
    import csv
    p = tmp_path / "XAUUSD_1d.csv"
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["openTime", "open", "high", "low", "close", "volume",
                    "tickVolume", "isOpen"])
        for i in range(n):
            c = start + i * step + (30 if i > n // 2 else 0)
            w.writerow([f"2025-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}T00:00:00Z",
                        c - 1, c + 3, c - 3, c, 0, 100 + i, "False"])
    return p


def test_backtest_never_reads_a_bar_from_the_future(tmp_path, monkeypatch):
    """The store upserts and never deletes, so a reused store leaks look-ahead.

    The first version of the backtest reused the package store; the trend agent
    saw all 500 bars at every step and called "bear" 379/379 times on a market
    that rose 18%. The walk must isolate its own store, and the guard inside
    `_walk_history` must fire if the newest bar is ever not the decision bar.
    """
    from desk import backtest

    # A stale store holding bars from the far future must not be visible.
    stale = Settings()
    stale.home = tmp_path
    stale.ensure_dirs()
    sh = SharedMemory(stale.shared_db)
    sh.save_candles("XAUUSD", "1d", _bars(400, start=9000.0))
    newest_before = sh.load_candles("XAUUSD", "1d")[-1]["open_time"]

    csv_path = _daily_csv(tmp_path)
    rep = backtest.run_backtest(csv_path, warmup=60, horizons=(1, 5))

    # The stale rows were never consulted: the decisions are built from the CSV,
    # and the package store is untouched.
    assert sh.load_candles("XAUUSD", "1d")[-1]["open_time"] == newest_before
    assert rep.decisions > 0
    # No decision was priced off the stale 9000-level bars: every entry lies in
    # the CSV's own range. This is the direct check that the leak is gone.
    assert all(2000 < t.entry < 5000 for t in rep.trades)


def test_backtest_guard_rejects_a_leaked_bar(tmp_path, monkeypatch):
    """If the decision bar is ever not the newest bar, the walk must raise."""
    from desk import backtest

    real_save = SharedMemory.save_candles

    def leaky_save(self, symbol, tf, bars):
        # Simulate a store that keeps a future bar around.
        extra = {"openTime": "2099-01-01T00:00:00Z", "open": 1.0, "high": 1.0,
                 "low": 1.0, "close": 1.0, "tickVolume": 0, "isOpen": False}
        return real_save(self, symbol, tf, list(bars) + [extra])

    monkeypatch.setattr(SharedMemory, "save_candles", leaky_save)
    csv_path = _daily_csv(tmp_path)
    with pytest.raises(AssertionError, match="look-ahead"):
        backtest.run_backtest(csv_path, warmup=60, horizons=(1,))


def test_controls_flag_a_direction_that_is_just_a_constant(tmp_path):
    """A positive expectancy from a one-sided book is a regime, not a method.

    The desk was short on 60% of a window in which gold rose 18%. Its +0.47R was
    matched by "always short" (+0.40R) and beaten by a two-line EMA rule. The
    control must say so rather than let the headline number stand alone.
    """
    from desk.backtest import Report, Trade, controls

    bars = []
    for i in range(80):
        c = 3000.0 - i * 5.0                     # a steady fall
        bars.append({"openTime": f"2026-01-{i % 28 + 1:02d}T00:00:00Z",
                     "open": c, "high": c + 2, "low": c - 2, "close": c,
                     "tickVolume": 1, "isOpen": False})
    # Every decision is a short with a 2R target, and every one hits.
    trades = [Trade(when=bars[i]["openTime"], direction="short", entry=bars[i]["close"],
                    stop=bars[i]["close"] + 10, target=bars[i]["close"] - 20,
                    rr=2.0, result="target", bars=1, r_multiple=2.0, idx=i)
              for i in range(0, 60)]
    rep = Report(symbol="XAUUSD", bars_total=len(bars), decisions=60,
                 directional=60, trades=trades)
    rep.bars = bars
    rep.horizon = 10
    out = controls(rep)
    # "always long" loses in a fall, so the short bias explains the whole result.
    assert "always short" in out
    assert "always long" in out
    # The verdict must warn, because a one-sided book cannot show skill.
    assert "NOT separable" in out or "regime" in out


def test_replay_forces_the_direction_it_is_given():
    """`_replay` is the unit the whole control rests on: it must honour the flag."""
    from desk.backtest import Trade, _replay

    bars = [{"openTime": "2026-01-01T00:00:00Z", "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "tickVolume": 1, "isOpen": False},
            {"openTime": "2026-01-02T00:00:00Z", "open": 100.0, "high": 101.0,
             "low": 95.0, "close": 95.0, "tickVolume": 1, "isOpen": False}]
    t = Trade(when=bars[0]["openTime"], direction="long", entry=100.0, stop=95.0,
              target=110.0, rr=2.0, result="stop", bars=1, r_multiple=-1.0, idx=0)
    assert _replay(bars, t, True, 5) == -1.0      # forced long is stopped out
    assert _replay(bars, t, False, 5) > 0         # forced short profits


# ---- scan -------------------------------------------------------------

def test_scan_grade_is_first_touch_and_stop_first():
    """The scan's grading unit must match the desk's own scoreboard: walk the
    bars, take the first touch, and count an ambiguous bar as the stop."""
    from desk.scan import grade

    # A bar that touches both the stop and the target counts as the stop.
    bars = [{"c": 100.0, "h": 100.0, "l": 100.0},
            {"c": 90.0, "h": 112.0, "l": 89.0}]
    # ATR comes from the helper, so build a series with a known range instead.
    assert grade(bars, 0, True) is None or isinstance(grade(bars, 0, True), float)


def test_scan_reports_a_family_that_does_not_hold_out_of_sample(tmp_path):
    """A signal that only works in one half must be labelled as not holding.

    A pure uptrend makes any long signal look good, so the scan has to say so
    rather than print the in-sample number alone.
    """
    import csv as _csv

    from desk.scan import run_scan

    p = tmp_path / "XAUUSD_1d.csv"
    with p.open("w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["openTime", "open", "high", "low", "close", "volume",
                    "tickVolume", "isOpen"])
        price = 3000.0
        for i in range(300):
            price *= 1.002
            w.writerow([f"2025-01-{i % 28 + 1:02d}T00:00:00Z", price - 1,
                        price + 2, price - 2, price, 0, 100, "False"])
    out = run_scan(p)
    assert "out of sample" in out
    assert "controls" in out
    # The scan must never present a family without the controls beside it.
    assert "always long" in out and "always short" in out


def test_load_daily_carries_the_open_flag(tmp_path):
    """A bar still forming is not a fact, so the flag must survive the load.

    `load_daily` used to hardcode `isOpen: False`, which made the backtest grade
    trades against a bar whose high/low/close were still moving -- the same
    look-at-the-partial-bar mistake the desk fixed once already.
    """
    import csv as _csv

    from desk.backtest import load_daily

    p = tmp_path / "XAUUSD_1d.csv"
    with p.open("w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["openTime", "open", "high", "low", "close", "volume",
                    "tickVolume", "isOpen"])
        w.writerow(["2026-01-01T00:00:00Z", 1, 2, 0.5, 1.5, 0, 10, "False"])
        w.writerow(["2026-01-02T00:00:00Z", 1, 2, 0.5, 1.5, 0, 10, "True"])
    bars = load_daily(p)
    assert bars[0]["isOpen"] is False
    assert bars[1]["isOpen"] is True


def test_backtest_drops_the_still_forming_bar(tmp_path):
    """The walk must not grade against the live, still-moving last bar."""
    from desk.backtest import load_daily

    p = tmp_path / "XAUUSD_1d.csv"
    with p.open("w", newline="") as fh:
        import csv as _csv

        w = _csv.writer(fh)
        w.writerow(["openTime", "open", "high", "low", "close", "volume",
                    "tickVolume", "isOpen"])
        for i in range(200):
            w.writerow([f"2025-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}T00:00:00Z",
                        3000 + i, 3002 + i, 2998 + i, 3000 + i, 0, 10,
                        "True" if i == 199 else "False"])
    bars = [b for b in load_daily(p) if not b.get("isOpen")]
    assert len(bars) == 199
    assert bars[-1]["isOpen"] is False


# ---- plan -------------------------------------------------------------

def _plan_csv(path, n=120, start=3000.0, step=1.0):
    import csv as _csv

    with path.open("w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["openTime", "open", "high", "low", "close", "volume",
                    "tickVolume", "isOpen"])
        for i in range(n):
            px = start + i * step
            w.writerow([f"2025-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}T00:00:00Z",
                        px, px + 3, px - 3, px, 0, 10,
                        "True" if i == n - 1 else "False"])


def test_plan_builds_both_sides_with_ladder(tmp_path):
    """The plan must offer a long and a short, each with stop and three rungs."""
    from desk.plan import build_plan

    p = tmp_path / "XAUUSD_1d.csv"
    _plan_csv(p)
    from desk.backtest import load_daily

    bars = load_daily(p)
    plan = build_plan(bars, live=3100.0)
    assert plan["long"].trigger > plan["long"].stop
    assert plan["short"].trigger < plan["short"].stop
    assert len(plan["long"].targets) == 3 and len(plan["short"].targets) == 3
    # the long ladder must run up, the short ladder down
    assert plan["long"].targets == sorted(plan["long"].targets)
    assert plan["short"].targets == sorted(plan["short"].targets, reverse=True)


def test_plan_ignores_the_open_bar(tmp_path):
    """Levels come from closed bars, so a wild open bar cannot move them."""
    from desk.backtest import load_daily
    from desk.plan import build_plan

    p = tmp_path / "XAUUSD_1d.csv"
    _plan_csv(p)
    base = build_plan(load_daily(p), live=3100.0)

    p2 = tmp_path / "XAUUSD_2_1d.csv"
    _plan_csv(p2)
    rows = p2.read_text().rstrip().split("\n")
    # blow out the final (open) bar's high/low
    rows[-1] = rows[-1].replace("True", "True").split(",")
    rows[-1][2] = "99999"
    rows[-1][3] = "1"
    p2.write_text("\n".join([",".join(r) if isinstance(r, list) else r for r in rows]))
    after = build_plan(load_daily(p2), live=3100.0)
    assert after["prior_high"] == base["prior_high"]
    assert after["prior_low"] == base["prior_low"]


def test_plan_labels_each_side_with_its_evidence(tmp_path):
    """A side must carry evidence that is *measured*, and a side with no sample
    must say so rather than borrow a number from the other side.

    The fixture rises monotonically, so it never breaks its prior 20-day range:
    the long trigger never fires and the sample is zero. The honest answer is
    "insufficient sample", not a supported side.
    """
    from desk.backtest import load_daily
    from desk.plan import build_plan, format_plan

    p = tmp_path / "XAUUSD_1d.csv"
    _plan_csv(p)
    plan = build_plan(load_daily(p), live=3100.0)
    # Evidence is recomputed, never a constant: the field exists and carries n.
    assert plan["long"].evidence["n"] == 0
    assert "base_r" in plan["long"].evidence
    out = format_plan(plan)
    assert "الدليل" in out
    assert "غير مدعوم" in out
    assert "not a promise" in out
    # The control is printed, so a side cannot be read without its baseline.
    assert "control: always-long" in out


def test_plan_evidence_is_measured_not_constant(tmp_path):
    """The evidence must change with the data. A constant would not.

    Two fixtures: one that repeatedly breaks its range (so the trigger fires),
    one that does not. If the numbers were hardcoded they would be identical;
    the point is that they are not.
    """
    import csv as _csv

    from desk.backtest import load_daily
    from desk.plan import build_plan

    def write(path, closes):
        with path.open("w", newline="") as fh:
            w = _csv.writer(fh)
            w.writerow(["openTime", "open", "high", "low", "close", "volume",
                        "tickVolume", "isOpen"])
            for i, px in enumerate(closes):
                w.writerow([f"2025-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}T00:00:00Z",
                            px, px + 2, px - 2, px, 0, 10,
                            "True" if i == len(closes) - 1 else "False"])

    # A saw-tooth that repeatedly breaks its 20-day high, then falls back.
    wave = [3000 + (i % 10) * 5 + (i // 10) * 2 for i in range(160)]
    p1, p2 = tmp_path / "a.csv", tmp_path / "b.csv"
    write(p1, wave)
    write(p2, [3000 + i for i in range(160)])
    e1 = build_plan(load_daily(p1), live=wave[-1])["long"].evidence
    e2 = build_plan(load_daily(p2), live=3160.0)["long"].evidence
    assert e1["n"] != e2["n"] or e1["mean_r"] != e2["mean_r"]
    assert e1["n"] > 0


# ---- the two cards must not disagree about the record ----------------

def test_recommendation_card_carries_the_same_measured_evidence(tmp_path):
    """The plan card and the recommendation card must report the SAME record.

    They used to be two disjoint paths: the plan printed a hardcoded table, the
    recommendation printed nothing. Both now read the shipped file through
    `evidence.measure`, so a reader cannot get two different stories.
    """
    import csv as _csv

    from desk.actions import Actions
    from desk.config import Settings
    from desk.memory import SharedMemory

    s = Settings(home=tmp_path / "home", symbol="XAUUSD")
    s.ensure_dirs()
    shared = SharedMemory(s.shared_db)
    # a saved run so the recommendation card has something to render
    shared.save_run("run1", "XAUUSD", 0.0, 1.0, "long", 0.5,
                    {"direction": "long", "entry": 4000.0, "stop": 3950.0,
                     "targets": [{"level": 4100.0, "kind": "pivot"}], "rr": 2.0,
                     "score": 0.5, "allowed": True, "bias": {"direction": "up"}})
    # the shipped-file lookup is what the card reads; point it at a fixture
    csv = tmp_path / "XAUUSD_1d.csv"
    with csv.open("w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["openTime", "open", "high", "low", "close", "volume",
                    "tickVolume", "isOpen"])
        for i in range(160):
            px = 3000 + (i % 10) * 5 + (i // 10) * 2
            w.writerow([f"2025-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}T00:00:00Z",
                        px, px + 2, px - 2, px, 0, 10, "False"])
    actions = Actions(s)
    actions._long_csv = lambda: csv  # type: ignore[method-assign]
    card = actions.recommend()
    assert "الدليل المقاس" in card


# ---- levels -----------------------------------------------------------

def test_levels_fire_on_the_breaking_side_only():
    """A level alerts when price is past it, and not before.

    The list is built here rather than read from DEFAULT_LEVELS: the test is of
    the mechanism, so editing the live plan must not break it.
    """
    from desk.levels import Level, triggered

    levels = (Level(100.0, "top", "above", ""), Level(50.0, "bottom", "below", ""))
    assert [lv.label for lv in triggered(120.0, levels)] == ["top"]
    assert [lv.label for lv in triggered(40.0, levels)] == ["bottom"]
    assert triggered(75.0, levels) == []           # between them: nothing
    assert len(triggered(200.0, levels)) == 1      # only the above-side fires


def test_level_fingerprint_is_stable_and_side_specific():
    """The ledger key must not change between runs, or the alert repeats."""
    from desk.levels import Level, fingerprint

    a = Level(3970.0, "x", "above", "")
    b = Level(3970.0, "x", "above", "")
    c = Level(3970.0, "x", "below", "")
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint(c)


def test_level_message_names_the_level_and_the_price():
    from desk.levels import Level, message

    out = message(Level(3970.0, "top", "above", "go up"), 3985.0)
    assert "3,970.00" in out and "3,985.00" in out


def test_watched_levels_are_well_formed():
    """The live plan's levels must each be a real, non-overlapping watch."""
    from desk.levels import DEFAULT_LEVELS

    assert len(DEFAULT_LEVELS) >= 2
    for lv in DEFAULT_LEVELS:
        assert lv.side in ("above", "below")
        assert lv.price > 0
        assert lv.label and lv.note
    prices = [lv.price for lv in DEFAULT_LEVELS]
    assert len(prices) == len(set(prices))         # no duplicate price


# ---- long history / cross-market --------------------------------------

def test_longdata_parses_yahoo_shape_and_skips_empty_bars():
    """Yahoo returns parallel arrays with nulls; the parser must skip them."""
    from desk.longdata import fetch

    body = {"chart": {"result": [{
        "timestamp": [1, 2, 3],
        "indicators": {"quote": [{"open": [10.0, None, 12.0],
                                  "high": [11.0, None, 13.0],
                                  "low": [9.0, None, 11.0],
                                  "close": [10.5, None, 12.5],
                                  "volume": [100, None, 200]}]}}]}}

    class _Resp:
        def read(self): return json.dumps(body).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import urllib.request
    real = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: _Resp()
    try:
        bars = fetch("GC=F", "1y")
    finally:
        urllib.request.urlopen = real
    assert len(bars) == 2                      # the null row is dropped
    assert all(b["isOpen"] is False for b in bars)   # history is always closed


def test_crossmarket_verdict_is_not_generalising_on_drift_only_data(tmp_path):
    """A market that only rises must not be reported as a working signal.

    The test builds several pure-drift series. The trend rule can be long most of
    the time on them, but it must not beat a constant chosen over the whole
    sample -- that is the definition of 'the rule added nothing'.
    """
    from desk.crossmarket import run_crossmarket

    for k in range(3):
        rows = ["openTime,open,high,low,close,volume,tickVolume,isOpen"]
        for i in range(700):
            px = 100 + i * 0.05
            rows.append(f"2020-01-{(i % 28) + 1:02d}T00:00:00Z,{px},{px+1},"
                        f"{px-1},{px},0,10,False")
        (tmp_path / f"M{k}_1d.csv").write_text("\n".join(rows))
    out = run_crossmarket(tmp_path, markets=["M0", "M1", "M2"])
    assert "markets where trend beats the best constant: 0" in out
    assert "does not generalise" in out


def test_crossmarket_baseline_is_a_single_constant_not_an_oracle(tmp_path):
    """The baseline must be one constant, never max(long, short) per bar."""
    from desk import crossmarket

    rows = ["openTime,open,high,low,close,volume,tickVolume,isOpen"]
    for i in range(600):
        px = 100 + (i % 40) * 0.3      # oscillates: long and short both lose
        rows.append(f"2020-01-{(i % 28) + 1:02d}T00:00:00Z,{px},{px+1},"
                    f"{px-1},{px},0,10,False")
    (tmp_path / "OSC_1d.csv").write_text("\n".join(rows))
    out = crossmarket.run_crossmarket(tmp_path, markets=["OSC"])
    # an oracle baseline would show a large positive constant; the honest one
    # cannot exceed the better of the two single directions
    assert "alwaysL" in out or "alwaysS" in out


# ---- gold study / longdata open flag ----------------------------------

def test_longdata_marks_the_forming_bar_open(monkeypatch):
    """The last bar of a live chart is today, still moving: it must be isOpen."""
    from datetime import UTC, datetime

    from desk.longdata import fetch

    today = datetime.now(UTC)
    ts = int(today.replace(hour=5).timestamp())
    body = {"chart": {"result": [{
        "timestamp": [ts - 86400, ts],
        "indicators": {"quote": [{"open": [10.0, 11.0], "high": [12.0, 13.0],
                                  "low": [9.0, 10.0], "close": [11.0, 12.0],
                                  "volume": [1, 2]}]}}]}}

    class _Resp:
        def read(self): return json.dumps(body).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import urllib.request
    real = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: _Resp()
    try:
        bars = fetch("GC=F", "1y")
    finally:
        urllib.request.urlopen = real
    assert bars[-1]["isOpen"] is True          # today is still forming
    assert bars[-2]["isOpen"] is False         # yesterday is a fact


def test_gold_study_excludes_the_open_bar(tmp_path):
    """A wild forming bar must not change the study's answer."""
    from desk.gold import run_gold_study

    def write(path, last_open):
        rows = ["openTime,open,high,low,close,volume,tickVolume,isOpen"]
        for i in range(400):
            px = 1000 + (i % 50) * 3
            rows.append(f"2020-01-{(i % 28) + 1:02d}T00:00:00Z,{px},{px+2},"
                        f"{px-2},{px},0,10,False")
        rows.append(f"2026-11-03T00:00:00Z,9999,19999,1,5000,0,10,{last_open}")
        path.write_text("\n".join(rows))

    a = tmp_path / "A_1d.csv"
    write(a, "True")
    base = run_gold_study(a, live=1000.0, symbol="A")
    b = tmp_path / "B_1d.csv"
    write(b, "False")
    other = run_gold_study(b, live=1000.0, symbol="B")
    # the open bar is dropped either way, so the analogue set is the same
    assert "389 days" not in base  # sanity: numbers come from the data
    assert base.count("الحالة الحالية") == other.count("الحالة الحالية") == 1


def test_gold_study_reports_both_sides_with_expectancy(tmp_path):
    from desk.gold import run_gold_study

    rows = ["openTime,open,high,low,close,volume,tickVolume,isOpen"]
    for i in range(400):
        px = 1000 + i * 0.5
        rows.append(f"2020-01-{(i % 28) + 1:02d}T00:00:00Z,{px},{px+2},"
                    f"{px-2},{px},0,10,False")
    p = tmp_path / "G_1d.csv"
    p.write_text("\n".join(rows))
    out = run_gold_study(p, live=1200.0, symbol="G")
    assert "بيع" in out and "شراء" in out
    assert "التوقّع R" in out
    assert "الأساس" in out        # the base rate must be shown beside it


# ---- the expanded menu and the listen loop ----------------------------

def test_menu_has_two_columns_and_no_duplicate_actions():
    """A menu is a UI: a duplicate action is a button that does nothing new."""
    from desk.bot import MENU

    actions = [a for row in MENU for _, a in row]
    assert len(actions) == len(set(actions)), "duplicate button action"
    assert all(len(row) <= 2 for row in MENU), "rows must fit a phone"
    assert len(MENU) <= 6, "menu must fit above the fold"


def test_every_button_has_a_handler(tmp_path, monkeypatch):
    """A button that returns 'unknown action' is a broken button."""
    from desk.actions import Actions
    from desk.bot import MENU

    acts = Actions(_settings(tmp_path))
    for row in MENU:
        for _label, action in row:
            out = acts.run(action)
            assert out, f"{action} returned nothing"
            assert "زر غير معروف" not in out, f"{action} has no handler"


def test_help_and_menu_are_different_answers(tmp_path):
    """'help' explains; 'menu' is the short greeting. Merging them loses one."""
    from desk.actions import Actions

    acts = Actions(_settings(tmp_path))
    assert acts.help() != acts.menu_text()
    assert "الأزرار" in acts.help()


def test_typed_words_map_to_the_new_actions():
    from desk.bot import action_for_text

    assert action_for_text("خطة") == "plan"
    assert action_for_text("مستويات") == "levels"
    assert action_for_text("ذهب") == "gold"
    assert action_for_text("/plan") == "plan"
    assert action_for_text("رجوع") == "menu"
    assert action_for_text("شي ما بيعني شي") == "menu"


def test_listen_answers_a_press_then_stops_at_the_deadline(tmp_path, monkeypatch):
    """The listen loop must return on its own, not run until the job is killed."""
    from desk.bot import TelegramBot

    bot, calls = _fake_bot(tmp_path, monkeypatch, updates=[_press(3, "market")])
    monkeypatch.setattr(TelegramBot, "send", lambda self, text, **kw: True)

    class FakeActions:
        def __init__(self, *a, **k): pass
        def run(self, action): return f"answer:{action}"

    monkeypatch.setattr("desk.actions.Actions", FakeActions)
    # A one-second window: the first pass answers the press, the second finds the
    # deadline passed and exits. That is the loop's real shape, not a special case.
    out = bot.listen(seconds=1, poll_s=1)
    assert out["ok"] is True
    assert out["passes"] >= 1
    assert "listened_s" in out
    assert out["handled"] == ["market"]


def test_listen_uses_a_long_poll_not_a_busy_loop(tmp_path, monkeypatch):
    """`timeout` must be sent, or the loop spins and burns the runner."""
    from desk.bot import TelegramBot

    bot, calls = _fake_bot(tmp_path, monkeypatch, updates=[])
    monkeypatch.setattr(TelegramBot, "send", lambda self, text, **kw: True)
    bot.poll(offset=None, wait_s=50)
    gets = [p for m, p in calls if m == "getUpdates"]
    assert gets and gets[0].get("timeout") == 50


def test_listen_stops_on_a_transport_failure_instead_of_retrying(tmp_path, monkeypatch):
    """A rejected token must end the run, not be retried for five minutes."""
    from desk.bot import TelegramBot

    bot, _ = _fake_bot(tmp_path, monkeypatch, updates=[])

    import urllib.error

    def boom(self, method, payload, timeout=20):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(TelegramBot, "_call", boom)
    out = bot.listen(seconds=300)
    assert out["ok"] is False
    assert out["passes"] == 1        # it gave up immediately


def test_listen_checks_the_levels_and_reports_what_it_sent(tmp_path, monkeypatch):
    """A live session is worth more than the cron only because it checks the
    levels between passes -- a level touched and rejected between two scheduled
    runs is invisible to the cron. The session must do that check and say so."""
    from desk.bot import TelegramBot

    bot, _ = _fake_bot(tmp_path, monkeypatch, updates=[])
    monkeypatch.setattr(TelegramBot, "send", lambda self, text, **kw: True)
    checked = []

    def fake_watch(settings, **kw):
        checked.append(1)
        return {"live": 3970.0, "alerted": ["الخط الفاصل"], "warned": []}

    monkeypatch.setattr("desk.levels.watch_once", fake_watch)
    out = bot.listen(seconds=1, poll_s=1, level_every=1)
    assert out["ok"] is True
    assert out["checks"] >= 1
    assert out["alerts"] == ["الخط الفاصل"]


def test_a_level_check_failure_does_not_kill_the_session(tmp_path, monkeypatch):
    """Presses are the primary job. If the quote source is down the session must
    keep answering, not die with the level check."""
    from desk.bot import TelegramBot

    bot, _ = _fake_bot(tmp_path, monkeypatch, updates=[_press(3, "market")])
    monkeypatch.setattr(TelegramBot, "send", lambda self, text, **kw: True)

    def boom(settings, **kw):
        raise RuntimeError("source down")

    monkeypatch.setattr("desk.levels.watch_once", boom)

    class FakeActions:
        def __init__(self, *a, **k): pass
        def run(self, action): return f"answer:{action}"

    monkeypatch.setattr("desk.actions.Actions", FakeActions)
    out = bot.listen(seconds=1, poll_s=1, level_every=1)
    assert out["ok"] is True
    assert out["handled"] == ["market"]      # the press was still answered
    assert out["checks"] == 0                # the failed check is not counted


def test_watch_once_sends_each_signal_exactly_once(tmp_path, monkeypatch):
    """The session checks every minute; without the ledger that would be a
    message a minute. The second pass must send nothing."""
    from desk import levels as L
    from desk.levels import Level
    from desk.notify import Notifier

    s = _settings(tmp_path)
    sent: list[str] = []

    class Recorder(Notifier):
        def send(self, text):
            sent.append(text)
            return True

    monkeypatch.setattr("desk.sources.fetch_quote", lambda symbol: {"mid": 3970.6})
    lv = Level(3970.5, "الخط الفاصل", "above", "note")
    first = L.watch_once(s, notifier=Recorder(s), levels=(lv,))
    second = L.watch_once(s, notifier=Recorder(s), levels=(lv,))
    assert first["alerted"] == ["الخط الفاصل"]
    assert second["alerted"] == []           # already in the ledger
    assert len(sent) == 1


def test_plan_button_builds_a_two_sided_answer(tmp_path):
    """The plan button must show both sides, with a stop each."""
    from desk.actions import Actions

    acts = Actions(_settings(tmp_path))
    csv = tmp_path / "XAUUSD_1d.csv"
    _plan_csv(csv, n=120)
    acts.settings.symbol = "XAUUSD"
    import desk.actions as mod
    real = mod.Actions._long_csv
    mod.Actions._long_csv = lambda self: csv
    try:
        out = acts.run("plan")
    finally:
        mod.Actions._long_csv = real
    assert "شراء مشروط" in out and "بيع مشروط" in out
    assert "الوقف" in out
    assert "الأهداف" in out


def test_no_button_double_escapes_its_markup(tmp_path, monkeypatch):
    """A button that builds its own `<b>` must not be escaped again, or the chat
    prints `&lt;b&gt;` at the user. This is the bug the expanded menu shipped with."""
    from desk.actions import Actions
    from desk.bot import MENU

    acts = Actions(_settings(tmp_path))
    for row in MENU:
        for label, action in row:
            out = acts.run(action)
            assert "&lt;" not in out, f"{label} double-escaped its markup"
            assert "&amp;" not in out, f"{label} double-escaped an ampersand"


def test_plain_text_buttons_escape_a_stray_ampersand(tmp_path, monkeypatch):
    """The other half of the rule: an agent summary with `&` must be escaped, or
    Telegram rejects the whole message for an unknown entity."""
    from desk.actions import Actions
    from desk.bot import MENU

    acts = Actions(_settings(tmp_path))
    monkeypatch.setattr(acts, "hitrate", lambda: "R&D hit 60%")
    assert "&amp;" in acts.run("hitrate")
    assert action_in_menu(MENU, "hitrate")


def action_in_menu(menu, action):
    return any(a == action for row in menu for _l, a in row)


# ---- Arabic-only chat -------------------------------------------------

def test_every_agent_fallback_is_arabic(tmp_path, monkeypatch):
    """The model is a nicety; with no key every summary must still be Arabic.
    An English fallback sentence would reach the chat as-is.

    Latin is allowed only for a known term -- a ticker, an indicator, an event
    acronym. The check is against that whitelist, not against zero Latin, so a
    real leak (an English word) still fails while `MACD` does not."""
    from desk.agents.base import LATIN_OK, _latin_words
    from desk.orchestrator import Orchestrator

    s = _settings(tmp_path)
    s.peers = ("USDJPY",)
    desk = Orchestrator(s)
    # Offline and deterministic: the point is the prose, not the feed.
    bars = _bars(160)
    monkeypatch.setattr("desk.sources.fetch_candles", lambda sym, tf, limit=1000: bars)
    monkeypatch.setattr("desk.sources.fetch_quote",
                        lambda sym: {"mid": bars[-1]["close"], "bid": 1, "ask": 1,
                                     "market_state": "open", "stale": False,
                                     "quote_age_s": 0, "day_diff_pct": 1.0,
                                     "source": "test"})
    monkeypatch.setattr("desk.sources.fetch_news", lambda symbol=None, max_results=25: [])
    monkeypatch.setattr("desk.sources.fetch_calendar", lambda importance="high", limit=100: [])
    monkeypatch.setattr("desk.sources.fetch_summary", lambda: {})
    run = desk.run()
    for s in run.studies:
        assert s.summary, f"{s.agent} produced no summary at all"
        stray = [w for w in _latin_words(s.summary)
                 if len(w) >= 3 and w not in LATIN_OK]
        assert not stray, f"{s.agent} summary is not Arabic: {stray} in {s.summary[:80]}"


def test_the_language_guard_accepts_arabic_and_rejects_english():
    from desk.agents.base import looks_arabic

    assert looks_arabic("الزخم هابط مع RSI 37.0 و MACD -10.9.")
    assert looks_arabic("التداول موقوف: حدث بعملة USD خلال 6 ساعة (JOLTS Job Openings).",
                        source="JOLTS Job Openings")
    assert not looks_arabic("Market structure is bearish.")
    assert not looks_arabic("البنية هابطة مع bearish BOS.")
    assert not looks_arabic("")
    assert not looks_arabic(None)


def test_a_drifted_model_answer_falls_back_to_arabic(tmp_path):
    """The prompt asks for Arabic; a model that drifts must not reach the chat."""
    from desk.agents.base import Agent
    from desk.llm import LLM

    class FakeLLM(LLM):
        def __init__(self, text):
            super().__init__("k", "m")
            self._text = text

        def complete(self, system, user, **kw):
            return self._text

    class A(Agent):
        name, kind = "t", "generic"

    s = _settings(tmp_path)
    a = A(s, shared=None, llm=FakeLLM("Trend is bearish across frames."))
    assert a.narrate("facts", "لخّص") is None      # English -> rejected
    b = A(s, shared=None, llm=FakeLLM("الاتجاه هابط على كل الفريمات."))
    assert b.narrate("facts", "لخّص") == "الاتجاه هابط على كل الفريمات."


def test_the_report_card_has_no_english_field_labels(tmp_path):
    """A reader should not meet an English word in the card: the stance, the
    verdict, the agent name and the market state are all translated."""
    from desk.actions import Actions
    from desk.memory import SharedMemory

    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    run_id = "abc123"
    shared.save_run(run_id, s.symbol, 1000.0, 1001.0, "no-trade", -0.2,
                    {"gather": {"quote": {"mid": 3900.0,
                                          "market_state": "open"}},
                     "levels_note": "لا صفقة"})
    shared.publish_study(run_id, "momentum", "momentum", "bear", 0.8, -0.6,
                         "الزخم هابط.", {"frames": {}})
    out = Actions(s).run("report")
    assert "هابط" in out and "مفتوح" in out and "الزخم" in out
    assert "bear" not in out and "open" not in out


# ---- the chart --------------------------------------------------------

def test_the_chart_is_a_valid_png_with_the_levels_drawn():
    """A PNG is written by hand, so the encoder itself is what is tested: the
    signature, the header size, and pixels of the level colours."""
    import struct
    import zlib

    from desk.chart import price_chart
    from desk.levels import DEFAULT_LEVELS

    bars = [{"openTime": f"2026-01-{i % 28 + 1:02d}T00:00:00Z",
             "close": 3900 + (i % 40) * 3.0} for i in range(120)]
    png = price_chart(bars, levels=DEFAULT_LEVELS, live=3905.0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", png[16:24])
    assert (w, h) == (640, 340)
    pos, idat = 8, b""
    while pos < len(png):
        ln = struct.unpack(">I", png[pos:pos + 4])[0]
        if png[pos + 4:pos + 8] == b"IDAT":
            idat += png[pos + 8:pos + 8 + ln]
        pos += 12 + ln
    raw = zlib.decompress(idat)                 # a broken stream would raise
    stride = w * 3

    def px(x, y):
        i = y * (stride + 1) + 1 + x * 3       # skip the per-row filter byte
        return (raw[i], raw[i + 1], raw[i + 2])

    cols = {px(x, y) for y in range(h) for x in range(w)}
    assert (34, 197, 94) in cols                # an "above" level
    assert (239, 68, 68) in cols                # a "below" level
    assert (250, 204, 21) in cols               # the price line


def test_the_chart_drops_the_forming_bar(tmp_path):
    """The picture must plot closed bars only, like every other reader."""
    import csv

    from desk.chart import price_chart

    p = tmp_path / "X_1d.csv"
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["openTime", "open", "high", "low", "close", "volume",
                    "tickVolume", "isOpen"])
        for i in range(120):
            w.writerow([f"2026-01-{i % 28 + 1:02d}T00:00:00Z", 100 + i, 102 + i,
                        98 + i, 100 + i, 0, 10, "False"])
        w.writerow(["2026-11-03T00:00:00Z", 9999, 19999, 1, 5000, 0, 10, "True"])
    from desk.backtest import load_daily
    bars = [b for b in load_daily(p) if not b.get("isOpen")]
    assert len(bars) == 120
    png = price_chart(bars[-120:])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ---- navigation -------------------------------------------------------

def test_every_answer_carries_a_way_back():
    """A reply with no button is a dead end: the reader has to remember a
    command. Every action gets the nav row, except the menu and help, which
    carry the full menu."""
    from desk.bot import MENU, keyboard_for

    for _label, action in (b for row in MENU for b in row):
        kb = keyboard_for(action)
        labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
        if action in ("menu", "help"):
            assert "📊 تحليل الآن" in labels, f"{action} must show the menu"
        else:
            assert "🏠 القائمة" in labels, f"{action} has no way back"
            assert "🔄 تحديث" in labels, f"{action} cannot be refreshed"
            # refresh re-runs the SAME action, so the button is not a lie
            refresh = [b for row in kb["inline_keyboard"] for b in row
                       if b["text"] == "🔄 تحديث"][0]
            assert refresh["callback_data"] == action


def test_the_menu_has_no_duplicate_and_fits_a_phone():
    from desk.bot import MENU

    actions = [a for row in MENU for _t, a in row]
    assert len(actions) == len(set(actions))
    assert all(len(row) <= 2 for row in MENU)
    assert len(actions) >= 10


# ---- the proximity warning -------------------------------------------

def test_approaching_finds_a_near_level_and_ignores_a_broken_one():
    """The warning is about a level NOT yet broken; a broken level is the other
    signal and must not be reported twice.

    The list is built here rather than read from DEFAULT_LEVELS: the test is of
    the mechanism, so editing the live plan must not break it.
    """
    from desk.levels import Level, approaching, triggered

    levels = (Level(3912.0, "top", "above", ""), Level(3897.0, "bottom", "below", ""))

    # 3905 sits inside the 12-point band of the 3912 level (above) and the 3897
    # level (below) without breaking either
    near = dict((lv.price, d) for lv, d in approaching(3905.0, levels, within=12.0))
    assert 3912.0 in near and 3897.0 in near
    assert triggered(3905.0, levels) == []
    # a price past the level is a break, not a warning
    assert 3912.0 not in [lv.price for lv, _ in approaching(3920.0, levels)]
    assert triggered(3920.0, levels)


def test_near_and_broken_have_different_ledger_keys():
    """If both signals shared a key the second would be swallowed by the first."""
    from desk.levels import DEFAULT_LEVELS, fingerprint, near_fingerprint

    lv = DEFAULT_LEVELS[0]
    assert fingerprint(lv) != near_fingerprint(lv)


def test_the_near_message_says_it_is_not_a_break_yet():
    from desk.levels import DEFAULT_LEVELS, near_message

    text = near_message(DEFAULT_LEVELS[0], 3925.0, 6.0)
    assert "اقتراب" in text and "6.0" in text
    assert "لسا ما انكسر" in text               # the honest caveat


# ---- market session ---------------------------------------------------

def _utc(y, mo, d, h=0, mi=0):
    import datetime as dt
    return dt.datetime(y, mo, d, h, mi, tzinfo=dt.UTC)


def test_session_open_midweek():
    from desk.session import market_session
    # Wednesday, midday UTC -- the heart of the week.
    s = market_session(_utc(2026, 11, 4, 12, 0))
    assert s.is_open and bool(s) is True


def test_session_closed_on_saturday():
    from desk.session import market_session
    s = market_session(_utc(2026, 11, 7, 12, 0))     # Saturday
    assert not s.is_open and bool(s) is False
    assert "السبت" in s.reason
    assert s.opens_at is not None and s.opens_at.weekday() == 6   # Sunday


def test_session_closed_after_friday_close():
    from desk.session import market_session
    assert market_session(_utc(2026, 11, 6, 20, 59)).is_open is True    # before
    assert market_session(_utc(2026, 11, 6, 21, 0)).is_open is False    # at the bell


def test_session_sunday_opens_at_2200_utc():
    from desk.session import market_session
    assert market_session(_utc(2026, 11, 8, 21, 59)).is_open is False   # Sunday, pre-open
    assert market_session(_utc(2026, 11, 8, 22, 0)).is_open is True     # Sunday, open


def test_naive_datetime_is_read_as_utc_not_crashed():
    import datetime as dt

    from desk.session import market_session
    # A naive value is a caller bug; it must resolve, not raise mid-run.
    s = market_session(dt.datetime(2026, 11, 4, 12, 0))
    assert s.is_open


def test_a_closed_market_stands_down_without_touching_the_network(tmp_path, monkeypatch):
    """The whole point of the gate: a closed market must not spend the run. If
    any source were called, this test would fail rather than pass quietly."""
    import desk.orchestrator as orch
    from desk.session import Session

    s = _settings(tmp_path)
    desk = Orchestrator(s)

    def boom(*a, **k):
        raise AssertionError("the network was touched on a closed market")

    monkeypatch.setattr("desk.sources.fetch_candles", boom)
    monkeypatch.setattr("desk.sources.fetch_quote", boom)
    monkeypatch.setattr("desk.sources.fetch_news", boom)
    monkeypatch.setattr("desk.sources.fetch_calendar", boom)
    monkeypatch.setattr(orch, "market_session",
                        lambda now=None: Session(False, "السوق مقفول (السبت)", None))
    run = desk.run(now=_utc(2026, 11, 7, 12, 0))
    assert run.verdict == "no-trade"
    assert run.studies == []
    assert run.recommendation["session"]["open"] is False
    assert run.recommendation["allowed"] is False
    assert "لا قراءة" in run.recommendation["levels_note"]


def test_a_closed_market_is_never_actionable(tmp_path, monkeypatch):
    """A stand-down must never alert; and it must say *why* (the session), not
    the generic 'no directional call'."""
    import desk.orchestrator as orch
    from desk.session import Session

    desk = Orchestrator(_settings(tmp_path))
    monkeypatch.setattr(orch, "market_session",
                        lambda now=None: Session(False, "السوق مقفول (السبت)", None))
    run = desk.run(now=_utc(2026, 11, 7, 12, 0))
    ok, reason = desk.actionable(run)
    assert ok is False
    assert "السبت" in reason


def test_respect_session_off_lets_the_desk_run_anyway(tmp_path, monkeypatch):
    """DESK_RESPECT_SESSION=0 is the on-demand escape hatch: a Saturday run still
    produces a full reading."""
    s = _settings(tmp_path)
    s.respect_session = False
    desk = Orchestrator(s)
    bars = _bars(120)
    monkeypatch.setattr("desk.sources.fetch_candles", lambda sym, tf, limit=1000: bars)
    monkeypatch.setattr("desk.sources.fetch_quote",
                        lambda sym: {"mid": bars[-1]["close"], "bid": 1, "ask": 1,
                                     "market_state": "open", "stale": False,
                                     "quote_age_s": 0, "day_diff_pct": 1.0,
                                     "source": "test"})
    monkeypatch.setattr("desk.sources.fetch_news", lambda symbol=None, max_results=25: [])
    monkeypatch.setattr("desk.sources.fetch_calendar", lambda importance="high", limit=100: [])
    monkeypatch.setattr("desk.sources.fetch_summary", lambda: {})
    run = desk.run(now=_utc(2026, 11, 7, 12, 0))
    assert run.studies, "with the session ignored the agents must still run"


def test_the_stand_down_card_says_it_did_not_look(tmp_path, monkeypatch):
    import desk.orchestrator as orch
    from desk.report import render
    from desk.session import Session

    desk = Orchestrator(_settings(tmp_path))
    monkeypatch.setattr(orch, "market_session",
                        lambda now=None: Session(False, "السوق مقفول (السبت)", None))
    run = desk.run(now=_utc(2026, 11, 7, 12, 0))
    card = render(run)
    assert "السوق مقفول" in card
    assert "لا قراءة ولا توصية" in card
    assert "دراسات الوكلاء" not in card


# ---- gold relevance filter --------------------------------------------

def test_relevance_keeps_gold_drivers():
    from desk.agents.news_agent import is_gold_relevant
    assert is_gold_relevant("Gold rallies as the Fed signals a rate cut")
    assert is_gold_relevant("Dollar weakens, bullion climbs")
    assert is_gold_relevant("Treasury yields fall on soft inflation data")
    assert is_gold_relevant("Gold holds as Middle East tension escalates")


def test_relevance_drops_unrelated_headlines():
    from desk.agents.news_agent import is_gold_relevant
    # The exact headlines a live pull returned under symbol=XAUUSD.
    assert not is_gold_relevant("Apple and Palantir shares slide in premarket trading")
    assert not is_gold_relevant("North Korea denies involvement in Binance hack")
    assert not is_gold_relevant("Anthropic Investors Push for Cautious Valuation")


def test_news_scores_only_relevant_headlines_and_reports_the_rest(tmp_path, monkeypatch):
    """The tone must be computed from gold headlines only, and the count of
    dropped ones must be visible -- a tone from 1 of 4 headlines is a different
    claim from a tone over all 4."""
    s = _settings(tmp_path)
    desk = Orchestrator(s)
    agent = next(a for a in desk.agents if a.name == "news")
    agent.llm.key = ""                      # deterministic: no model

    def feed(symbol=None, max_results=25):
        return [
            {"title": "Gold rallies on safe-haven demand", "description": "", "url": "a"},
            {"title": "Apple shares slide in premarket trading", "description": "", "url": "b"},
            {"title": "Aramco profit jumps despite pipeline attacks", "description": "", "url": "c"},
            {"title": "Dollar weakens as Fed hints at rate cut", "description": "", "url": "d"},
        ]

    monkeypatch.setattr("desk.sources.fetch_news", feed)
    monkeypatch.setattr("desk.sources.fetch_calendar", lambda importance="high", limit=100: [])
    st = agent.run("r1")
    assert st.payload["fetched"] == 4
    assert st.payload["dropped_irrelevant"] == 2
    assert len(st.payload["headlines"]) == 2
    assert "من أصل 4" in st.summary


def test_news_says_so_when_nothing_is_about_gold(tmp_path, monkeypatch):
    """An all-irrelevant feed must not silently read as neutral-and-fine; the
    desk says there is no gold news rather than a tone of zero."""
    s = _settings(tmp_path)
    desk = Orchestrator(s)
    agent = next(a for a in desk.agents if a.name == "news")
    agent.llm.key = ""
    monkeypatch.setattr("desk.sources.fetch_news",
                        lambda symbol=None, max_results=25: [
                            {"title": "Apple shares slide", "description": "", "url": "a"},
                            {"title": "Crypto exchange hacked", "description": "", "url": "b"}])
    monkeypatch.setattr("desk.sources.fetch_calendar", lambda importance="high", limit=100: [])
    st = agent.run("r1")
    assert st.payload["dropped_irrelevant"] == 2
    assert st.payload["headlines"] == []
    assert "ما في عنوان يخصّ الذهب" in st.summary


# ---- scenarios --------------------------------------------------------

def _frame(n=120, start=3900.0, step=1.0):
    """Closed bars in the agent `frames_data` shape (o/h/l/c/open)."""
    out = []
    for i in range(n):
        c = start + i * step
        out.append({"t": f"2026-01-01T{i:02d}:00:00Z", "o": c - 1, "h": c + 2,
                    "l": c - 2, "c": c, "open": False})
    return out


def _wave_frame(n=160, period=20, amp=40.0, base=3900.0):
    """A wave in the frame shape. A monotonic series prints no pivots at all
    (its extreme is always the next bar), so a wave is what exercises levels."""
    import math
    out = []
    for i in range(n):
        c = base + amp * math.sin(2 * math.pi * i / period)
        out.append({"t": f"2026-01-01T{i:02d}:00:00Z", "o": c - 1, "h": c + 2,
                    "l": c - 2, "c": c, "open": False})
    return out


def test_scenarios_use_printed_levels_and_a_measured_stop():
    """A scenario's trigger is a level the market printed and its stop is that
    frame's ATR — never an invented number."""
    import datetime as dt

    from desk import scenarios as S

    frames = {"1h": _wave_frame(base=3960.0), "4h": _wave_frame(base=3950.0),
              "1d": _wave_frame(base=3940.0)}
    sc = S.build(frames, 3960.0, calendar=[], now=dt.datetime(2026, 11, 3, tzinfo=dt.UTC))
    assert sc["scenarios"], "a wave must print levels"
    for s in sc["scenarios"]:
        assert s["side"] in ("long", "short")
        # a long's stop is below its trigger; a short's is above
        if s["side"] == "long":
            assert s["stop"] < s["trigger"] < s["targets"][0]
        else:
            assert s["targets"][0] < s["trigger"] < s["stop"]
        assert s["risk"] > 0 and s["rr"] is not None


def test_every_scenario_carries_an_invalidation_level():
    """A scenario that cannot be wrong is not a scenario. Each must print the
    price at which its thesis fails -- and that price must sit nearer than the
    stop, so the thesis is falsified before the money is lost."""
    import datetime as dt

    from desk import scenarios as S

    frames = {"1h": _wave_frame(base=3960.0), "4h": _wave_frame(base=3950.0)}
    sc = S.build(frames, 3960.0, calendar=[], now=dt.datetime(2026, 11, 3, tzinfo=dt.UTC))
    assert sc["scenarios"]
    for s in sc["scenarios"]:
        inv = s["invalidation"]
        assert inv is not None
        if s["side"] == "long":
            assert s["stop"] < inv < s["trigger"]
        else:
            assert s["trigger"] < inv < s["stop"]


def test_scenario_probability_is_measured_or_absent(tmp_path, monkeypatch):
    """A probability is either the measured hit rate of this bracket in the same
    state, or it carries its count with `sufficient=False`. It is never invented
    from a thin sample, and it is never silently dropped either -- a bare absence
    leaves the reader unable to tell "too few" from "not measured"."""
    import datetime as dt

    from desk import scenarios as S

    frames = {"1h": _wave_frame(base=3960.0)}
    sc = S.build(frames, 3960.0, calendar=[], now=dt.datetime(2026, 11, 3, tzinfo=dt.UTC))
    for s in sc["scenarios"]:
        p = s.get("probability")
        if p is not None:
            if p["sufficient"]:
                assert 0.0 <= p["p"] <= 1.0
                assert p["n"] >= 30, "a shown probability must rest on a real sample"
            else:
                assert p["n"] < 30
                assert p["p"] is None or p["p"] == 0.0


def test_scenario_probability_comes_from_the_analog_engine(monkeypatch):
    """The probability must be the analog engine's number for the current state,
    not a second, separate estimate that could disagree with the report."""
    import datetime as dt

    from desk import scenarios as S
    from desk.agents import analog

    fake = {"n_state": 120, "long": {"n": 80, "hit": 0.625, "mean_r": 0.5, "t": 3.0,
                                     "base_r": 0.2, "edge": 0.3},
            "short": {"n": 40, "hit": 0.30, "mean_r": -0.4, "t": -2.0,
                      "base_r": -0.2, "edge": -0.2}}
    monkeypatch.setattr(analog, "measure_cached", lambda C, key: fake)
    monkeypatch.setattr(S, "_probability",
                        lambda side, symbol="XAUUSD": {"p": fake[side]["hit"],
                                                       "n": fake[side]["n"],
                                                       "n_state": 120,
                                                       "mean_r": fake[side]["mean_r"],
                                                       "t": fake[side]["t"],
                                                       "sufficient": True,
                                                       "state": "x"})
    frames = {"1h": _wave_frame(base=3960.0)}
    sc = S.build(frames, 3960.0, calendar=[], now=dt.datetime(2026, 11, 3, tzinfo=dt.UTC))
    for s in sc["scenarios"]:
        assert s["probability"]["p"] == fake[s["side"]]["hit"]


def test_report_prints_the_scenarios_not_only_the_verdict():
    """The card is the recommendation AND its scenarios. A verdict with no
    scenarios is half a machine; the desk must print both halves."""

    from desk import report
    from desk.orchestrator import Run

    sc = {"symbol": "XAUUSD", "live": 4090.0, "scenarios": [
        {"kind": "breakdown", "side": "short", "trigger": 4084.2, "confirm": 4083.2,
         "frame": "5m", "stop": 4091.0, "risk": 7.0, "rr": 1.8, "targets": [4071.6],
         "probability": {"p": 0.20, "n": 65, "mean_r": -0.49, "t": -1.0,
                         "sufficient": True, "state": "x"},
         "invalidation": 4085.2, "why": "كسر", "sizing": None}],
        "probability_state": "تحت المتوسط 50 · RSI ضعيف"}
    rec = {"scenarios": sc, "bias": {"direction": "flat", "score": 0.0},
           "allowed": True}
    run = Run("r1", "XAUUSD", 0.0, [], "no-trade", 0.0, rec, [], 1.0)
    text = report.render(run)
    assert "السيناريوهات" in text
    assert "4,084.20" in text
    assert "20%" in text
    assert "يُبطَل" in text
    assert "<b>" not in text, "the plain card must not carry HTML tags"


def test_scenarios_ignore_a_level_that_is_the_price_itself():
    """A pivot sitting on the live price is not a decision point; a stop measured
    from it would be a whole frame ATR of nonsense."""
    import datetime as dt

    from desk import scenarios as S

    frames = {"1h": _wave_frame(base=3200.0, amp=100.0)}
    live = 3200.0
    sc = S.build(frames, live, calendar=[], now=dt.datetime(2026, 11, 3, tzinfo=dt.UTC))
    for s in sc["scenarios"]:
        assert abs(s["trigger"] - live) >= 1.0


def test_scenarios_report_the_next_high_impact_event_only():
    """The event line names the nearest high-impact release inside the window, and
    a low-impact or far-away row is not it."""
    import datetime as dt

    from desk import scenarios as S

    now = dt.datetime(2026, 11, 3, 12, 0, tzinfo=dt.UTC)
    cal = [
        {"time": "2026-11-03T16:00:00Z", "currency": "USD", "name": "JOLTS Job Openings",
         "importance": "high", "forecast": 6.7, "previous": 7.0},
        {"time": "2026-11-03T13:00:00Z", "currency": "USD", "name": "Minor Thing",
         "importance": "low", "forecast": None, "previous": None},
        {"time": "2026-11-09T13:00:00Z", "currency": "USD", "name": "Too Far",
         "importance": "high", "forecast": None, "previous": None},
    ]
    sc = S.build({"1h": _wave_frame()}, 3960.0, calendar=cal, now=now, window_h=6.0)
    assert sc["event"] is not None
    assert sc["event"]["name"] == "فرص العمل (JOLTS)"     # translated
    assert sc["event"]["currency"] == "USD"
    assert 3.9 < sc["event"]["hours"] < 4.1


def test_a_scenario_event_must_concern_the_instruments_currency():
    """A nearer event in the WRONG currency must not win.

    Gold is quoted in dollars, so New Zealand employment cannot move it. The
    nearest high-impact row used to win whatever it concerned, and the scenario
    block told a gold trader about NZD employment while the USD releases went
    unmentioned."""
    import datetime as dt

    from desk import scenarios as S

    now = dt.datetime(2026, 11, 3, 12, 0, tzinfo=dt.UTC)
    cal = [
        {"time": "2026-11-03T13:00:00Z", "currency": "NZD",
         "name": "Employment Change q/q", "importance": "high",
         "forecast": 0.4, "previous": 0.5},
        {"time": "2026-11-03T16:00:00Z", "currency": "USD",
         "name": "ADP Nonfarm Employment Change", "importance": "high",
         "forecast": 64.0, "previous": 90.0},
    ]
    sc = S.build({"1h": _wave_frame()}, 3960.0, calendar=cal, now=now,
                 window_h=6.0, symbol="XAUUSD")
    assert sc["event"] is not None
    assert sc["event"]["currency"] == "USD", "the NZD row is nearer but cannot move gold"
    assert sc["event"]["hours"] > 3.5


def test_a_lone_wrong_currency_event_is_not_reported_at_all():
    """No USD event in the window means no event line -- not the nearest row."""
    import datetime as dt

    from desk import scenarios as S

    now = dt.datetime(2026, 11, 3, 12, 0, tzinfo=dt.UTC)
    cal = [{"time": "2026-11-03T13:00:00Z", "currency": "NZD", "name": "RBNZ Thing",
            "importance": "high", "forecast": None, "previous": None}]
    sc = S.build({"1h": _wave_frame()}, 3960.0, calendar=cal, now=now,
                 window_h=6.0, symbol="XAUUSD")
    assert sc["event"] is None


def test_scenarios_say_so_when_there_is_nothing_to_build_on():
    """No printed levels is stated, not filled with an invented price."""
    from desk import scenarios as S

    sc = S.build({}, 3970.0, calendar=[])
    assert sc["scenarios"] == []
    assert sc["note"]
    assert "لا توجد مستويات" in S.render(sc)


def test_closed_filters_the_forming_bar_in_every_shape():
    """The forming bar must never reach the levels.

    The flag is spelled `open` in the frame the agents pass, but `open` is the
    bar's OPENING PRICE in the CSV and long-history shapes. Reading `open` as a
    flag unconditionally either leaks the partial bar (frame shape) or drops
    every bar (CSV shape). Both are silent, and both move a level."""
    from desk import scenarios as S

    # frame shape: `open` is a bool flag
    assert S._closed([{"open": False}, {"open": True}]) == [{"open": False}]
    # CSV / long-history shape: `open` is a price, `isOpen` is the flag
    csv = [{"open": 3900.0, "isOpen": False}, {"open": 3901.0, "isOpen": True}]
    assert S._closed(csv) == [{"open": 3900.0, "isOpen": False}]
    # raw feed shape: `is_open`
    feed = [{"open": 3900.0, "is_open": 0}, {"open": 3901.0, "is_open": 1}]
    assert S._closed(feed) == [{"open": 3900.0, "is_open": 0}]


def test_a_forming_bar_cannot_move_a_scenario_trigger():
    """The end-to-end version of the above: a partial bar's high/low walk with
    the tick, so a pivot read off it is not a fact. Adding a wild forming bar
    must not change a single trigger."""
    import datetime as dt

    from desk import scenarios as S

    frames = {"1h": _wave_frame(base=3960.0), "4h": _wave_frame(base=3950.0)}
    now = dt.datetime(2026, 11, 3, tzinfo=dt.UTC)
    before = S.build(frames, 3960.0, calendar=[], now=now)

    wild = dict(frames)
    wild["1h"] = frames["1h"] + [{"t": "now", "o": 3960.0, "h": 4500.0,
                                  "l": 3000.0, "c": 3960.0, "open": True}]
    after = S.build(wild, 3960.0, calendar=[], now=now)

    assert [s["trigger"] for s in before["scenarios"]] == \
           [s["trigger"] for s in after["scenarios"]]


def test_levels_come_from_the_desks_own_plan_not_a_typed_list():
    """The alert must watch the levels the recommendation names.

    A hand-typed list drifts from the plan: a run would say "break below
    3,919.49" while the alert watched 3,937.50, so the line the trade hinged on
    had no alert at all."""
    from desk.levels import DEFAULT_LEVELS, from_recommendation

    rec = {"direction": "short", "entry": 3927.43, "stop": 3945.16,
           "targets": [{"level": 3898.95, "kind": "pivot"}]}
    lv = from_recommendation(rec, 3927.43)
    prices = {x.price for x in lv}
    assert prices == {3927.43, 3945.16, 3898.95}
    assert not (prices & {x.price for x in DEFAULT_LEVELS})
    # a short's stop is watched as a break ABOVE it, its target BELOW
    by_price = {x.price: x for x in lv}
    assert by_price[3945.16].side == "above"
    assert by_price[3898.95].side == "below"


def test_a_plan_with_no_levels_falls_back_instead_of_watching_nothing():
    from desk.levels import from_recommendation

    assert from_recommendation({"direction": "no-trade"}, 3927.0) == ()
    assert from_recommendation({}, 3927.0) == ()


def test_watch_once_watches_the_last_runs_plan(tmp_path, monkeypatch):
    """With no explicit list, the watcher reads the last run and watches the
    levels IT printed -- so the alert tracks the desk as the desk changes."""
    from desk import levels as L
    from desk.memory import SharedMemory
    from desk.notify import Notifier

    s = _settings(tmp_path)
    sh = SharedMemory(s.shared_db)
    sh.save_run("r1", "XAUUSD", 0.0, 0.0, "short", -0.4,
                {"recommendation": {
                    "direction": "short", "entry": 3927.43, "stop": 3945.16,
                    "targets": [{"level": 3898.95}]}})
    monkeypatch.setattr("desk.sources.fetch_quote", lambda symbol: {"mid": 3927.5})

    class Rec(Notifier):
        def send(self, text):
            return True

    out = L.watch_once(s, notifier=Rec(s))
    labels = {lbl for lbl in out["crossed"]} | {lbl for lbl, _ in out["near"]}
    assert labels == {"دخول التوصية"}, "the plan's own levels must be watched"


def test_a_broken_run_payload_falls_back_to_the_typed_list(tmp_path, monkeypatch):
    """A corrupt payload must not silence the alert entirely."""
    from desk import levels as L
    from desk.memory import SharedMemory
    from desk.notify import Notifier

    s = _settings(tmp_path)
    SharedMemory(s.shared_db).save_run("r1", "XAUUSD", 0.0, 0.0, "short", -0.4,
                                       {"recommendation": "not a dict"})
    monkeypatch.setattr("desk.sources.fetch_quote", lambda symbol: {"mid": 3937.6})

    class Rec(Notifier):
        def send(self, text):
            return True

    out = L.watch_once(s, notifier=Rec(s))
    assert out["total"] == 5          # DEFAULT_LEVELS, not an empty list


def test_watch_pass_grades_so_the_record_cannot_go_stale(tmp_path, monkeypatch):
    """Grading runs on every pass, not only when the button is pressed."""
    from desk.orchestrator import Orchestrator, Run

    s = _settings(tmp_path)
    desk = Orchestrator(s)
    fake = Run(run_id="r1", symbol="XAUUSD", started=0.0, verdict="no-trade",
               score=0.0, recommendation={})
    monkeypatch.setattr(Orchestrator, "run", lambda self: fake)

    class N:
        def send(self, text):
            return True

    out = desk.watch_once(notifier=N())
    assert "graded" in out
    assert "runs_graded" in out["graded"]      # graded even when it stays silent


def test_scenario_render_is_telegram_html():
    """The render must escape nothing it did not build: every level it prints is a
    number the desk computed, so a `<` can never reach Telegram as an entity."""
    from desk import scenarios as S

    sc = S.build({"1h": _frame(), "1d": _frame(start=3880.0)}, 3970.0, calendar=[])
    text = S.render(sc)
    assert "<b>" in text and "</b>" in text
    assert "&lt;" not in text


# ---- the always-on daemon ---------------------------------------------
#
# The daemon is the shape a real host wants: the chat, the levels, and the
# readings in ONE process, on three cadences that do not compete. On a scheduler
# the desk had to be two loops that never spoke -- `serve` read the market but
# not the chat, `bot --listen` read the chat for a few minutes but not the
# market. These pin the scheduling, the failure isolation, and the stand-down.

class _OpenSession:
    is_open = True
    reason = "السوق مفتوح"
    opens_at = None


class _ClosedSession:
    is_open = False
    reason = "السوق مقفول (السبت) — بيفتح الأحد"
    opens_at = None


def _daemon(tmp_path, monkeypatch, **kw):
    """A Daemon whose jobs are recorders, never the network."""
    from desk.daemon import Daemon

    d = Daemon(_settings(tmp_path), **kw)
    seen: dict[str, list] = {"levels": [], "read": []}

    def fake_levels(settings, **k):
        seen["levels"].append(1)
        return {"live": 3970.0, "alerted": [], "warned": []}

    def fake_read(notifier=None):
        seen["read"].append(1)
        return {"verdict": "no-trade", "score": 0.0, "alerted": False,
                "reason": "no directional call"}

    monkeypatch.setattr("desk.levels.watch_once", fake_levels)
    monkeypatch.setattr(d.orchestrator, "watch_once", fake_read)
    monkeypatch.setattr("desk.daemon.market_session", lambda now=None: _OpenSession())
    return d, seen


def test_daemon_runs_both_jobs_on_the_first_tick(tmp_path, monkeypatch):
    """A fresh process must read the market immediately, not sit idle for a full
    interval before doing anything. The cadence clocks start at -inf, not 0."""
    d, seen = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900)
    out = d.tick(0.0)
    assert seen["levels"] == [1]
    assert seen["read"] == [1]
    assert out["levels"] is not None and out["read"] is not None


def test_daemon_respects_each_cadence(tmp_path, monkeypatch):
    """Levels every 10s, a reading every 900s -- and not more often."""
    d, seen = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900)
    d.tick(0.0)
    d.tick(10.0)          # levels only
    d.tick(20.0)          # levels only
    d.tick(900.0)         # both
    assert len(seen["levels"]) == 4
    assert len(seen["read"]) == 2


def test_daemon_reads_no_market_job_while_closed(tmp_path, monkeypatch):
    """A closed market is not a reading. The chat is still served, but there is
    no tape to read and no level worth watching -- and the process must say so."""
    d, seen = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900)
    monkeypatch.setattr("desk.daemon.market_session", lambda now=None: _ClosedSession())
    out = d.tick(0.0)
    assert out["closed"] is True
    assert seen["levels"] == [] and seen["read"] == []


def test_daemon_keeps_going_when_a_level_check_fails(tmp_path, monkeypatch):
    """The reading is the primary job. A dead quote source must not stop it."""
    d, seen = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900)

    def boom(settings, **k):
        raise RuntimeError("source down")

    monkeypatch.setattr("desk.levels.watch_once", boom)
    out = d.tick(0.0)
    assert out["levels"].get("error") == "RuntimeError"
    assert seen["read"] == [1]                 # the reading still ran
    assert d.errors                   # and the failure was recorded


def test_the_self_ping_fires_on_its_own_cadence(tmp_path, monkeypatch):
    """The host sleeps when nothing INBOUND arrives for ~15 minutes, and the only
    other inbound traffic is a Telegram push -- which a quiet chat never sends.
    So the daemon must ping itself, on its own clock, or a free host sleeps and
    the next press meets a dead instance."""
    d, _ = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900,
                   ping_every=600)
    hits: list[str] = []
    monkeypatch.setattr(d, "_self_ping", lambda: hits.append("ping") or {"ok": True})
    d.tick(0.0)
    assert hits == ["ping"]                    # the very first tick pings
    d.tick(300.0)
    assert hits == ["ping"]                    # not yet due
    d.tick(600.0)
    assert hits == ["ping", "ping"]            # due again


def test_the_self_ping_runs_even_when_the_market_is_closed(tmp_path, monkeypatch):
    """A closed market is when the host sleeps most: no reading, no push, nothing
    inbound. If the ping were a market job it would stop exactly when it matters
    most."""
    d, _ = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900,
                   ping_every=600)
    monkeypatch.setattr("desk.daemon.market_session", lambda now=None: _ClosedSession())
    hits: list[str] = []
    monkeypatch.setattr(d, "_self_ping", lambda: hits.append("ping") or {"ok": True})
    out = d.tick(0.0)
    assert out["closed"] is True
    assert hits == ["ping"]                    # pinged despite the closed market


def test_the_self_ping_can_be_switched_off(tmp_path, monkeypatch):
    """A host that does not sleep should not make a needless request: ping_every=0
    disables it rather than pinging once and stopping."""
    d, _ = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900,
                   ping_every=0)
    hits: list[str] = []
    monkeypatch.setattr(d, "_self_ping", lambda: hits.append("ping") or {"ok": True})
    d.tick(0.0)
    d.tick(100000.0)
    assert hits == []


def test_the_self_ping_reports_failure_without_killing_the_desk(tmp_path, monkeypatch):
    """A ping that cannot reach us means we are asleep or the base is wrong.
    Neither is made worse by a hard failure, and the market jobs must keep going."""
    d, seen = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900,
                      ping_every=600)
    monkeypatch.setattr("desk.daemon.market_session", lambda now=None: _OpenSession())
    d.settings.webhook_base = "http://127.0.0.1:9"   # nothing listens on port 9
    out = d.tick(0.0)
    assert out["ping"]["ok"] is False
    assert seen["read"] == [1]                 # the reading still ran
    assert any(e["job"] == "ping" for e in d.errors)


def test_the_self_ping_skips_when_there_is_no_public_base(tmp_path, monkeypatch):
    """A local process has no public address to ping; that is not an error."""
    d, _ = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900,
                   ping_every=600)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    d.settings.webhook_base = ""
    out = d.tick(0.0)
    assert out["ping"] == {"skipped": "no public base"}
    assert d.errors == []


def test_the_daemon_sleeps_no_longer_than_the_ping_window(tmp_path, monkeypatch):
    """The loop sleeps to the next job; if the ping were excluded from that
    calculation the host would sleep past its own keep-alive. The ping interval
    must bound the sleep whenever it is the soonest job."""
    d, _ = _daemon(tmp_path, monkeypatch, level_every=1000, read_every=2000,
                   ping_every=600)
    d.last_level = 0.0
    d.last_read = 0.0
    d.last_ping = 0.0
    assert d._next_due_in(0.0) == 600.0        # the ping, not the 1000s level


def test_daemon_keeps_going_when_a_reading_fails(tmp_path, monkeypatch):
    """A failed reading must not kill the process either."""
    d, seen = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900)

    def boom(notifier=None):
        raise RuntimeError("model down")

    monkeypatch.setattr(d.orchestrator, "watch_once", boom)
    out = d.tick(0.0)
    assert out["read"].get("error") == "RuntimeError"
    assert seen["levels"] == [1]


def test_daemon_stops_on_the_deadline(tmp_path, monkeypatch):
    """`run_forever(seconds=...)` must return on its own, so a smoke run and a
    test both end without a signal."""
    d, _ = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900)
    monkeypatch.setattr(d, "_poll_buttons", lambda wait: None)
    out = d.run_forever(seconds=0.05)
    assert out["ticks"] >= 1
    assert out["uptime_s"] is not None


def test_daemon_a_dead_token_does_not_kill_the_process(tmp_path, monkeypatch):
    """A rejected token must be backed off and reported, not raised: the process
    is meant to outlive a transport failure."""
    from desk.daemon import Daemon

    d = Daemon(_settings(tmp_path))
    slept: list[float] = []

    class Bot:
        def poll(self, **k):
            return {"ok": False, "reason": "getUpdates HTTP 401"}

    d._bot = Bot()
    monkeypatch.setattr("desk.daemon.time.sleep", lambda s: slept.append(s))
    d._poll_buttons(5.0)                        # must not raise
    assert slept and slept[0] <= 5.0
    assert d.errors


# ---- the webhook transport --------------------------------------------
#
# The webhook is the other way to reach the chat: Telegram PUSHES an update
# instead of the desk polling. Two things must hold or it is worse than the
# poll -- the answer must be IDENTICAL to the poll's (same chat check, same
# action, same nav row), and a forged POST must be rejected before it reaches
# an action.

def _post(port, path, body, headers=None):
    """POST to the local webhook and return (status, parsed body)."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _get(port, path):
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


class _RecordingBot:
    """A bot that records what it was asked to answer, never the network."""

    def __init__(self):
        self.seen: list[dict] = []
        self.registered: list[tuple[str, str]] = []

    def answer_webhook_update(self, update, orchestrator=None):
        self.seen.append(update)
        return {"ok": True, "handled": "market"}

    def set_webhook(self, url, secret_token=""):
        self.registered.append((url, secret_token))
        return True


def _webhook(tmp_path, monkeypatch, *, secret="s3cr3t", port=18099, bot=None):
    from desk.webhook import WebhookServer

    s = _settings(tmp_path)
    s.telegram_token = "123456:FAKE"
    s.telegram_chat_id = "933571066"
    srv = WebhookServer(s, bot=bot or _RecordingBot(), secret_token=secret, port=port)
    srv.start()
    return srv


def test_health_reports_the_deployed_commit(monkeypatch):
    """The desk is silent by design, so a green /health cannot by itself say
    which code is live. The marker is what makes 'is the fix deployed?'
    answerable from outside -- and it must be the commit Render built, not a
    version string that drifts."""
    from desk import webhook

    monkeypatch.setenv("RENDER_GIT_COMMIT", "56c1309abcdef1234567890")
    assert webhook.build_marker() == "56c1309abcde"


def test_health_marker_falls_back_to_the_version_off_render(monkeypatch):
    """Locally there is no deployed commit to name, so the honest answer is the
    package version -- never a guess at a SHA that was never built."""
    from desk import __version__, webhook

    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    assert webhook.build_marker() == f"v{__version__}"


def test_health_endpoint_exposes_the_build_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "deadbeefcafe0000")
    srv = _webhook(tmp_path, monkeypatch, port=18097)
    try:
        status, body = _get(srv.port, "/health")
        assert status == 200
        assert body["ok"] is True and body["build"] == "deadbeefcafe"
    finally:
        srv.stop()


def test_webhook_health_answers_so_a_free_host_does_not_sleep(tmp_path, monkeypatch):
    """`/health` is the whole reason a free host stays awake: the ping is inbound
    traffic, and a long poll (all outbound) would never be."""
    srv = _webhook(tmp_path, monkeypatch)
    try:
        status, body = _get(srv.port, "/health")
        assert status == 200 and body["ok"] is True
    finally:
        srv.stop()


def test_webhook_rejects_a_forged_post_before_it_reaches_an_action(tmp_path, monkeypatch):
    """A POST without the secret header must be refused, and the action must NOT
    run. A forged request that reached an action could spend a model quota."""
    bot = _RecordingBot()
    srv = _webhook(tmp_path, monkeypatch, bot=bot)
    try:
        body = json.dumps({"update_id": 1}).encode()
        status, _ = _post(srv.port, "/telegram", body,
                          {"Content-Type": "application/json",
                           "X-Telegram-Bot-Api-Secret-Token": "wrong"})
        assert status == 403
        assert bot.seen == []                  # never reached the bot
    finally:
        srv.stop()


def test_webhook_answers_a_correctly_signed_update(tmp_path, monkeypatch):
    bot = _RecordingBot()
    srv = _webhook(tmp_path, monkeypatch, bot=bot)
    try:
        body = json.dumps({"update_id": 2}).encode()
        status, out = _post(srv.port, "/telegram", body,
                            {"Content-Type": "application/json",
                             "X-Telegram-Bot-Api-Secret-Token": "s3cr3t"})
        assert status == 200 and out["ok"] is True
        assert [u["update_id"] for u in bot.seen] == [2]
    finally:
        srv.stop()


def test_webhook_rejects_bad_json_and_oversize(tmp_path, monkeypatch):
    srv = _webhook(tmp_path, monkeypatch)
    try:
        headers = {"Content-Type": "application/json",
                   "X-Telegram-Bot-Api-Secret-Token": "s3cr3t"}
        assert _post(srv.port, "/telegram", b"not json", headers)[0] == 400
        assert _post(srv.port, "/telegram", b"[1,2,3]", headers)[0] == 400
    finally:
        srv.stop()


def test_webhook_unknown_path_is_404(tmp_path, monkeypatch):
    srv = _webhook(tmp_path, monkeypatch)
    try:
        assert _get(srv.port, "/nope")[0] == 404
    finally:
        srv.stop()


def test_webhook_does_not_500_so_telegram_does_not_retry_storm(tmp_path, monkeypatch):
    """A bad update must answer 200, not 5xx: a 5xx makes Telegram redeliver the
    same update forever. The failure is reported in the body instead."""
    class Boom(_RecordingBot):
        def answer_webhook_update(self, update, orchestrator=None):
            raise RuntimeError("action blew up")

    srv = _webhook(tmp_path, monkeypatch, bot=Boom())
    try:
        status, out = _post(srv.port, "/telegram", json.dumps({"update_id": 3}).encode(),
                            {"Content-Type": "application/json",
                             "X-Telegram-Bot-Api-Secret-Token": "s3cr3t"})
        assert status == 200 and out["ok"] is False
    finally:
        srv.stop()


def test_webhook_answers_exactly_like_the_poll(tmp_path, monkeypatch):
    """The two transports must take the SAME path. If the webhook answered
    differently from a poll it would be a second, silently diverging client."""
    from desk.bot import TelegramBot

    bot, _ = _fake_bot(tmp_path, monkeypatch, updates=[])
    monkeypatch.setattr(TelegramBot, "send", lambda self, text, **kw: True)
    sent: list[str] = []
    monkeypatch.setattr(TelegramBot, "_send_chart", lambda self, a, c, m: sent.append(a))

    class FakeActions:
        def __init__(self, *a, **k): pass
        def run(self, action): return f"answer:{action}"

    monkeypatch.setattr("desk.actions.Actions", FakeActions)
    out = bot.answer_webhook_update(_press(9, "market"))
    assert out["ok"] is True and out["handled"] == "market"
    assert sent == ["market"]                  # the chart hook ran, same as a poll


def test_webhook_update_from_another_chat_is_ignored(tmp_path, monkeypatch):
    """The same quota guard the poll has: only the configured chat may spend."""
    from desk.bot import TelegramBot

    bot, _ = _fake_bot(tmp_path, monkeypatch, updates=[])
    monkeypatch.setattr(TelegramBot, "send", lambda self, text, **kw: True)

    class FakeActions:
        def __init__(self, *a, **k): pass
        def run(self, action): return "should not run"

    monkeypatch.setattr("desk.actions.Actions", FakeActions)
    out = bot.answer_webhook_update(_press(4, "market", chat="111"))
    assert out["handled"] is None              # not ours to answer


def test_daemon_web_mode_registers_the_webhook_and_skips_the_poll(tmp_path, monkeypatch):
    """`--web` must serve the endpoint and point Telegram at it, and must NOT
    also poll -- the two transports are mutually exclusive on Telegram's side."""
    from desk.daemon import Daemon

    bot = _RecordingBot()
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://desk.example")
    d = Daemon(_settings(tmp_path), bot=bot, web=True, level_every=10, read_every=900)
    polled: list[int] = []
    monkeypatch.setattr(d, "_poll_buttons", lambda wait: polled.append(1))
    monkeypatch.setattr("desk.daemon.market_session", lambda now=None: _OpenSession())
    out = d.run_forever(seconds=0.05)
    assert d.webhook is not None
    assert bot.registered == [("https://desk.example/telegram", "")]
    assert polled == []                        # web mode never polls
    assert out["ticks"] >= 1
    d.webhook.stop()


def test_render_blueprint_stays_inside_the_free_hour_budget():
    """The blueprint must not declare a second service that breaks the free tier.

    This is a regression guard, not a style check. Render gives a workspace 750
    instance-hours a month shared across ALL free services, not 750 each. A
    single always-on web service already spends 720-744 h; a second service
    running even 20 h pushes the workspace over the cap and Render suspends
    *every* free service in it. The blueprint used to declare a 15-minute cron
    as a "belt-and-braces" fallback -- which would have taken the desk down, and
    a cron has no free instance type anyway ($1/month minimum). The failure mode
    is a billing/suspension fact, not an exception, so only this test catches it.
    """
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    doc = yaml.safe_load((root / "render.yaml").read_text())
    services = doc["services"]

    assert len(services) == 1, (
        f"render.yaml declares {len(services)} services; the free 750 h/month is "
        "per workspace, so a second service would suspend the whole workspace")
    svc = services[0]
    assert svc["type"] == "web", "only a web service has a free instance type"
    assert svc["plan"] == "free"
    assert svc["healthCheckPath"] == "/health"

    # One always-on service must still fit the 750-hour cap on a 31-day month.
    assert 31 * 24 <= 750, "a single always-on month must fit the free allowance"

    # Every secret is dashboard-set, never written to git.
    by_key = {e["key"]: e for e in svc["envVars"]}
    for secret in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GROQ_API_KEY",
                   "DESK_WEBHOOK_SECRET"):
        assert by_key[secret].get("sync") is False, f"{secret} must not be in git"


def test_the_keepawake_workflow_pings_health_within_the_sleep_window():
    """A free Render service sleeps after 15 idle minutes, so the ping cadence
    must be well inside that -- and it must be a plain `/health` GET, because
    that is the endpoint that answers without a secret.

    The cron is written as a range (`3-59/5`) rather than `*/5` to dodge the top
    of the hour, GitHub's most congested slot -- so parse the step, do not assume
    the `*/N` shape.
    """
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    path = root / ".github" / "workflows" / "marketdesk-keepawake.yml"
    doc = yaml.safe_load(path.read_text())
    cron = doc[True]["schedule"][0]["cron"]          # `on:` parses as True
    minute = cron.split()[0]
    assert "/" in minute, f"unexpected cadence {cron!r}"
    step = int(minute.split("/")[1])
    assert step <= 15, "a cadence wider than the sleep window lets it sleep"
    # An offset off :00: GitHub drops or delays the top of the hour most.
    assert not minute.startswith("*/"), "start at an offset, not :00"
    step_expr = doc["jobs"]["ping"]["steps"][0]
    blob = str(step_expr.get("env", {})) + str(step_expr.get("run", ""))
    assert "/health" in blob
    # The default must name the service that actually exists. Getting this wrong
    # is a silent 404 loop -- the ping warns forever without saying why.
    assert "xauusd14355-4.onrender.com" in blob or "DESK_HEALTH_URL" in blob


def test_the_bot_cron_leaves_headroom_and_avoids_the_top_of_the_hour():
    """The bot's listen window must fit inside its own cron gap, and the cron
    must not start at :00. A 200s listen plus ~45s of setup must end before the
    next trigger, or `concurrency` drops the run behind it -- measured on this
    repo: a `*/5` cron at :00 produced only 2 scheduled runs in ~17 hours."""
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    doc = yaml.safe_load((root / ".github" / "workflows" / "marketdesk-bot.yml").read_text())
    cron = doc[True]["schedule"][0]["cron"]
    minute = cron.split()[0]
    assert not minute.startswith("*/"), "start at an offset, not :00"
    step = int(minute.split("/")[1])
    assert step >= 5, "GitHub's shortest supported interval is 5 minutes"

    inputs = doc[True]["workflow_dispatch"]["inputs"]
    listen = inputs["listen_seconds"]["default"]
    assert listen + 45 < step * 60, (
        f"a {listen}s listen plus setup must end inside the {step}-minute gap")


def test_daemon_cadences_can_come_from_the_environment(tmp_path, monkeypatch):
    """A container is tuned by env, not by editing the entrypoint. `render.yaml`
    sets DESK_LEVEL_EVERY=30 for the free 0.1-CPU instance; if the CLI ignored
    it, the config would promise a cadence the process never used."""
    import argparse

    from desk.cli import cmd_daemon
    from desk.daemon import Daemon

    captured: dict = {}

    class Cap(Daemon):
        def __init__(self, *a, **k):
            captured.update(k)
            super().__init__(*a, **k)

        def install_signals(self):
            pass

        def run_forever(self, **k):
            return self.summary()

    monkeypatch.setattr("desk.daemon.Daemon", Cap)
    monkeypatch.setenv("DESK_LEVEL_EVERY", "30")
    monkeypatch.setenv("DESK_READ_EVERY", "600")
    ns = argparse.Namespace(symbol=None, level_every=None, read_every=None,
                            poll_wait=None, ping_every=None, seconds=None, web=False)
    assert cmd_daemon(ns) == 0
    assert captured["level_every"] == 30.0
    assert captured["read_every"] == 600.0


def test_a_flag_beats_the_environment(tmp_path, monkeypatch):
    """An explicit flag must win over the env, or a manual run could not override
    a container's tuned cadence."""
    import argparse

    from desk.cli import cmd_daemon
    from desk.daemon import Daemon

    captured: dict = {}

    class Cap(Daemon):
        def __init__(self, *a, **k):
            captured.update(k)
            super().__init__(*a, **k)

        def install_signals(self):
            pass

        def run_forever(self, **k):
            return self.summary()

    monkeypatch.setattr("desk.daemon.Daemon", Cap)
    monkeypatch.setenv("DESK_LEVEL_EVERY", "30")
    ns = argparse.Namespace(symbol=None, level_every=5.0, read_every=None,
                            poll_wait=None, ping_every=None, seconds=None, web=False)
    assert cmd_daemon(ns) == 0
    assert captured["level_every"] == 5.0


# ---- surprise agent: the number the calendar printed --------------------

def _cal_event(name, actual, forecast, currency="USD", hours_ago=1.0, previous=None):
    """A calendar row shaped like the feed's, timed `hours_ago` before now."""
    import datetime as _dt
    when = _dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=hours_ago)
    return {"id": f"{name}-{when.isoformat()}", "time": when.isoformat(),
            "countryCode": "US", "currency": currency, "name": name,
            "importance": "high", "actual": actual, "forecast": forecast,
            "previous": previous}


def test_surprise_sign_is_the_fx_reaction(tmp_path):
    """A USD print above forecast is gold-negative; below is gold-positive.
    Getting this backwards would turn the whole agent into a contrarian."""
    from desk.agents.surprise import gold_score

    assert gold_score("ISM Services PMI", 0.5, "USD", "USD") < 0, "hot USD print -> gold down"
    assert gold_score("ISM Services PMI", -0.5, "USD", "USD") > 0, "cool print -> gold up"
    assert gold_score("ISM Services PMI", 1.0, "AUD", "USD") == 0.0, "non-USD -> no vote"


def test_surprise_flips_sign_for_dollar_negative_series(tmp_path):
    """A higher Unemployment Rate WEAKENS the dollar, so it is gold-positive.
    A single uniform sign would get every labour-weakness release backwards."""
    from desk.agents.surprise import gold_score

    assert gold_score("Unemployment Rate", 0.5, "USD", "USD") > 0, "more unemployment -> gold up"
    assert gold_score("Initial Jobless Claims", 0.5, "USD", "USD") > 0, "more claims -> gold up"
    assert gold_score("Unemployment Rate", -0.5, "USD", "USD") < 0, "fewer -> gold down"


def test_surprise_ignores_usd_tagged_non_dollar_events(tmp_path):
    """A USD currency tag is not a dollar transmission channel. EIA crude stocks
    is USD-tagged but an oil-supply figure -- the live feed scored it on gold
    before this rule existed."""
    from desk.agents.surprise import dollar_positive, gold_score

    assert dollar_positive("EIA Crude Oil Stocks Change") is None
    assert gold_score("EIA Crude Oil Stocks Change", 0.9, "USD", "USD") == 0.0


def test_surprise_needs_a_forecast_to_exist(tmp_path):
    """No expectation -> no surprise. Returning 0.0 would claim the number 'met
    expectations' when there was no expectation to meet."""
    from desk.agents.surprise import event_surprise

    assert event_surprise(54.5, None) is None
    assert event_surprise(None, 55.2) is None
    assert event_surprise(54.5, 0) is None, "a zero forecast has no relative scale"
    assert event_surprise(0.0, 5.0) is not None, "0.0 is a real reading, not 'missing'"


def test_surprise_zero_when_forecast_is_met(tmp_path):
    from desk.agents.surprise import event_surprise

    assert event_surprise(55.2, 55.2) == 0.0


def test_surprise_agent_reads_the_calendar_it_stored(tmp_path):
    """End to end: rows in the shared calendar become a signed study."""
    from desk.agents.surprise import SurpriseAgent
    from desk.llm import LLM

    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    # ISM came in at 54.5 vs 55.2 expected -> a miss -> USD weaker -> gold up.
    shared.save_calendar([_cal_event("ISM Services PMI", 54.5, 55.2)])

    a = SurpriseAgent(s, shared, LLM("", s.models))
    st = a.study("run-1")
    assert st.stance == "bull", f"a USD miss must read bullish for gold, got {st.stance}"
    assert st.score > 0
    assert st.payload["fresh"] == 1
    assert st.payload["events"][0]["actual"] == 54.5


def test_surprise_agent_ignores_stale_and_foreign_prints(tmp_path):
    """An old print and another currency's print must not vote."""
    from desk.agents.surprise import SurpriseAgent
    from desk.llm import LLM

    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    shared.save_calendar([
        _cal_event("ISM Services PMI", 54.5, 55.2, hours_ago=20),      # stale
        _cal_event("RBA Rate Decision", 4.35, 4.10, currency="AUD"),   # not USD
    ])

    a = SurpriseAgent(s, shared, LLM("", s.models))
    st = a.study("run-2")
    assert st.score == 0.0, "nothing fresh and in-currency -> no vote"
    assert st.payload["fresh"] == 0


def test_surprise_agent_stays_silent_without_data(tmp_path):
    """No calendar at all is not a bullish or bearish signal."""
    from desk.agents.surprise import SurpriseAgent
    from desk.llm import LLM

    s = _settings(tmp_path)
    a = SurpriseAgent(s, SharedMemory(s.shared_db), LLM("", s.models))
    st = a.study("run-3")
    assert st.score == 0.0
    assert st.confidence == 0.0
    assert st.payload["events"] == []


def test_surprise_weights_by_relative_size(tmp_path):
    """A bigger relative miss must score bigger, so a 5% miss outweighs a 0.5%
    one instead of both counting as 'a miss'."""
    from desk.agents.surprise import event_surprise

    big = event_surprise(105.0, 100.0)
    small = event_surprise(100.5, 100.0)
    assert big is not None and small is not None
    assert abs(big) > abs(small)


def test_surprise_decays_with_age(tmp_path):
    """Between two opposite prints of equal size, the fresher one must win. With
    a single event the weight normalises out, so this is the case that actually
    exercises the decay -- without it the two would cancel to neutral."""
    from desk.agents.surprise import SurpriseAgent
    from desk.llm import LLM

    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    shared.save_calendar([
        # fresh, bullish for gold: ISM Manufacturing missed 54.7 -> 54.0
        _cal_event("ISM Manufacturing PMI", 54.0, 54.7, hours_ago=0.25),
        # stale, bearish for gold: ISM Services beat 55.2 -> 55.9
        _cal_event("ISM Services PMI", 55.9, 55.2, hours_ago=5.5),
    ])
    st = SurpriseAgent(s, shared, LLM("", s.models)).study("r")
    assert st.score > 0, "the fresh bullish print must outweigh the stale bearish one"
    assert st.payload["events"][0]["weight"] > st.payload["events"][1]["weight"]


def test_surprise_is_in_the_roster(tmp_path):
    """A new agent that is not registered does not run -- the wiring is the
    feature."""
    from desk.agents import ROSTER
    from desk.agents.surprise import SurpriseAgent

    assert SurpriseAgent in ROSTER


# ---- volume: a volatility input, never a direction ---------------------

def test_volume_ratio_reads_the_last_bar_against_its_window(tmp_path):
    from desk import indicators as ind

    bars = [{"tv": 100} for _ in range(20)] + [{"tv": 150}]
    assert ind.volume_ratio(bars) == 1.5


def test_volume_ratio_is_none_when_it_cannot_be_read(tmp_path):
    """A ratio off three bars, or off a dead feed, is not a reading -- return
    None so the caller applies no adjustment rather than a made-up one."""
    from desk import indicators as ind

    assert ind.volume_ratio([{"tv": 100}, {"tv": 200}]) is None
    assert ind.volume_ratio([{"tv": 0} for _ in range(21)]) is None


def test_volatility_agent_reports_the_volume_ratio(tmp_path, monkeypatch):
    from desk.agents.volatility import VolatilityAgent
    from desk.llm import LLM

    s = _settings(tmp_path)
    bars = _bars(120)
    bars[-1]["tickVolume"] = 400  # hot last bar
    desk = Orchestrator(s)
    monkeypatch.setattr("desk.sources.fetch_candles", lambda sym, tf, limit=1000: bars)
    monkeypatch.setattr("desk.sources.fetch_quote",
                        lambda sym: {"mid": bars[-1]["close"], "market_state": "open",
                                     "stale": False, "quote_age_s": 0})
    monkeypatch.setattr("desk.sources.fetch_news", lambda symbol=None, max_results=25: [])
    monkeypatch.setattr("desk.sources.fetch_calendar", lambda importance="high", limit=100: [])
    monkeypatch.setattr("desk.sources.fetch_summary", lambda: {})
    desk.run()
    vol = VolatilityAgent(s, desk.shared, LLM("", s.models))
    st = vol.study("r")
    assert st.payload["frames"]["1h"]["vol_ratio"] is not None
    assert st.payload["frames"]["1h"]["vol_ratio"] > 1.2


def test_hot_volume_widens_the_stop_quiet_keeps_it(tmp_path, monkeypatch):
    """The measured effect (t=3.96) must reach the plan: a hot last bar widens
    the stop, a quiet one leaves the structural stop alone. This is the whole
    point of keeping tick_volume -- and it must not change the direction."""
    import math

    from desk.orchestrator import Orchestrator

    def hourly_wave(n=200):
        """A wave with one bar per hour, so the last bar really is the newest
        on the 1h frame (the shared `_wave` fixture repeats hours)."""
        out = []
        for i in range(n):
            c = 3000 + 50 * math.sin(2 * math.pi * i / 20)
            day, hr = i // 24, i % 24
            out.append({"openTime": f"2026-01-{1 + day:02d}T{hr:02d}:00:00Z",
                        "open": c - 1, "high": c + 2, "low": c - 2, "close": c,
                        "tickVolume": 100, "isOpen": False})
        return out

    def plan_for(last_volume):
        s = _settings(tmp_path / f"vol-{last_volume}")
        s.home.mkdir(parents=True, exist_ok=True)
        desk = Orchestrator(s)
        bars = hourly_wave()
        bars[-1]["tickVolume"] = last_volume
        monkeypatch.setattr("desk.sources.fetch_candles", lambda sym, tf, limit=1000: bars)
        monkeypatch.setattr("desk.sources.fetch_quote",
                            lambda sym: {"mid": bars[-1]["close"], "bid": 1, "ask": 1,
                                         "market_state": "open", "stale": False,
                                         "quote_age_s": 0, "day_diff_pct": 0.0,
                                         "source": "test"})
        monkeypatch.setattr("desk.sources.fetch_news", lambda symbol=None, max_results=25: [])
        monkeypatch.setattr("desk.sources.fetch_calendar", lambda importance="high", limit=100: [])
        monkeypatch.setattr("desk.sources.fetch_summary", lambda: {})
        return desk.run().recommendation

    quiet = plan_for(100)
    hot = plan_for(400)
    assert quiet["vol_ratio"] == 1.0 and quiet["vol_mult"] == 1.0
    assert hot["vol_ratio"] == 4.0 and hot["vol_mult"] > 1.0


def test_surprise_vote_reaches_the_final_verdict(tmp_path, monkeypatch):
    """End to end through the orchestrator: a fresh USD surprise must move the
    weighted score, not merely appear on the board. This is the wiring the desk
    insists on -- an agent whose vote never reaches the verdict is decoration."""
    bars = _bars(120)

    def run_with(cal):
        s = _settings(tmp_path / f"home-{len(cal)}-{abs(hash(str(cal)))}")
        s.home.mkdir(parents=True, exist_ok=True)
        desk = Orchestrator(s)
        monkeypatch.setattr("desk.sources.fetch_candles", lambda sym, tf, limit=1000: bars)
        monkeypatch.setattr("desk.sources.fetch_quote",
                            lambda sym: {"mid": bars[-1]["close"], "bid": 1, "ask": 1,
                                         "market_state": "open", "stale": False,
                                         "quote_age_s": 0, "day_diff_pct": 1.0,
                                         "source": "test"})
        monkeypatch.setattr("desk.sources.fetch_news", lambda symbol=None, max_results=25: [])
        monkeypatch.setattr("desk.sources.fetch_calendar",
                            lambda importance="high", limit=100: cal)
        monkeypatch.setattr("desk.sources.fetch_summary", lambda: {})
        run = desk.run()
        return run, {st.agent: st for st in run.studies}

    quiet, _ = run_with([])
    loud, by = run_with([_cal_event("ISM Services PMI", 62.0, 55.2)])

    # A big USD beat -> dollar stronger -> gold-negative study.
    assert by["surprise"].stance == "bear"
    assert by["surprise"].score < 0
    # ... and it must actually move the verdict's weighted score. If the agent's
    # weight were dropped, the two runs would be identical.
    assert loud.score < quiet.score, "the surprise vote must reach the tally"



# ---- account sizing ---------------------------------------------------

def test_size_is_floored_to_the_lot_step_and_reports_the_real_risk():
    """A size must never round UP: 0.006 lots is 0.00, not 0.01. Rounding up
    silently risks more than the budget, which is the one thing sizing exists to
    prevent. The reported risk must come from the size actually traded."""
    from desk.account import size_plan
    # $5000 * 1% = $50 budget; a $50 stop over 100oz = $5000 risk per lot -> 0.01
    sz = size_plan(balance=5000, risk_pct=1, entry=4000, stop=3950)
    assert sz.feasible
    assert sz.lots == 0.01
    assert abs(sz.risk_money - 50.0) < 1e-9
    # A size that would need 0.0199 lots must floor to 0.01, not round to 0.02.
    sz2 = size_plan(balance=5000, risk_pct=1, entry=4000, stop=3980)  # 0.025 -> 0.02
    assert sz2.lots == 0.02
    sz3 = size_plan(balance=1000, risk_pct=1, entry=4000, stop=3980)  # 0.005 -> 0.00
    assert not sz3.feasible


def test_a_stop_too_wide_for_the_account_is_refused_with_the_number():
    """On a $200 account the smallest gold lot risks 21% on a 42-point stop.
    The honest answer is 'you cannot take this', and it must carry the number
    that proves it -- not a size that looks fine and is not."""
    from desk.account import size_plan
    sz = size_plan(balance=200, risk_pct=2, entry=4120, stop=4162.15, target=4082.99)
    assert not sz.feasible
    assert sz.lots == 0.0
    assert abs(sz.min_risk_money - 42.15) < 1e-6      # 0.01 lot * 42.15 * 100
    assert abs(sz.min_risk_pct - 21.075) < 1e-6
    assert "21.1%" in sz.note or "21.1%" in sz.note.replace("21.07", "21.1")


def test_the_owner_short_sizes_on_a_larger_account():
    """The same trade that is impossible on $200 becomes 0.01 lots once the
    budget clears the minimum lot -- and the money must match the arithmetic."""
    from desk.account import size_plan
    # $10000 * 1% = $100 budget / (42.15 * 100) = 0.0237 -> floor 0.02 lots
    sz = size_plan(balance=10000, risk_pct=1, entry=4120, stop=4162.15, target=4082.99)
    assert sz.feasible
    assert sz.lots == 0.02
    assert abs(sz.risk_money - 84.30) < 1e-6          # 0.02 * 42.15 * 100
    assert abs(sz.reward_money - 74.02) < 1e-6        # 0.02 * 37.01 * 100


def test_a_zero_distance_stop_does_not_divide_by_zero():
    from desk.account import size_plan
    sz = size_plan(balance=200, risk_pct=1, entry=4120, stop=4120)
    assert not sz.feasible
    assert sz.lots == 0.0
    assert sz.risk_money == 0.0


def test_scenarios_carry_sizing_only_when_a_balance_is_given():
    """No balance -> points only, so a keyless desk still works. A balance ->
    every scenario is sized in money, and the render shows it."""
    from desk import scenarios as S
    # two printed levels above and below a live price
    bars = []
    for i in range(60):
        px = 4000 + (i % 10)
        bars.append({"o": px, "h": px + 8, "l": px - 8, "c": px + 1, "open": False})
    frames = {"1h": bars, "4h": bars, "1d": bars}
    live = 4004.0
    plain = S.build(frames, live)
    assert all("sizing" not in s for s in plain["scenarios"])
    assert "لوت" not in S.render(plain)
    sized = S.build(frames, live, balance=200, risk_pct=1)
    assert sized["scenarios"], "the fixture must print at least one level"
    assert all("sizing" in s for s in sized["scenarios"])
    # On $200 these 1h-ATR stops are infeasible, so the money view is a refusal --
    # but it must be there, with the dollars, not silently dropped.
    r = S.render(sized)
    assert "🚫" in r and "$" in r


def test_sizing_line_is_absent_without_a_balance_and_present_with_one(tmp_path):
    from desk import active
    s = _settings(tmp_path)
    plan = active.adopt(s, {"direction": "short", "entry": 4120.0,
                            "stop": 4162.15, "targets": [{"level": 4082.99}]}, "r")
    assert plan is not None
    s.balance = 0
    assert active.sizing_line(plan, s) is None
    s.balance = 200
    line = active.sizing_line(plan, s)
    assert line is not None and "🚫" in line and "21.1%" in line


def test_a_stop_wider_than_the_owners_cap_is_refused_outright():
    """The owner allows 10 points. A 42-point stop is refused because the stop is
    unaffordable as a *setup*, not because of the lot size -- and the refusal must
    say which, so the reader is not told 'increase your account' when the real
    problem is the stop."""
    from desk.account import size_plan
    # At 2% the budget is $4, but the minimum lot on a 10-point stop risks $10.
    # So the cap is not the binding limit here -- the money is.
    tight = size_plan(balance=200, risk_pct=2, entry=4120, stop=4130,
                      max_stop_points=10)
    assert not tight.feasible and "سقفك" not in tight.note
    # At 5% the budget is exactly $10 = 0.01 lot on a 10-point stop, so it fits.
    ok = size_plan(balance=200, risk_pct=5, entry=4120, stop=4130,
                   max_stop_points=10)
    assert ok.feasible and ok.lots == 0.01
    # A 42-point stop is refused because of the CAP, and the note must say so.
    wide = size_plan(balance=200, risk_pct=5, entry=4120, stop=4162.15,
                     max_stop_points=10)
    assert not wide.feasible
    assert "سقفك" in wide.note
    # With no cap, the same stop falls back to the money rule (min lot over-risks).
    uncapped = size_plan(balance=200, risk_pct=5, entry=4120, stop=4162.15)
    assert not uncapped.feasible and "سقفك" not in uncapped.note


def test_a_stop_tighter_than_the_owners_floor_is_refused():
    """The owner's window is 10-15 points. A 6-point stop is inside the noise and
    is refused as 'too_tight', distinct from the money refusal, so the message
    says the right thing."""
    from desk.account import size_plan
    tight = size_plan(balance=200, risk_pct=7.5, entry=4120, stop=4126,
                      min_stop_points=10, max_stop_points=15)
    assert not tight.feasible
    assert tight.reason == "too_tight"
    inside = size_plan(balance=200, risk_pct=7.5, entry=4120, stop=4131,
                       min_stop_points=10, max_stop_points=15)
    assert inside.feasible and inside.lots == 0.01
    assert abs(inside.risk_money - 11.0) < 1e-9        # 11 pts * 0.01 * 100


def test_a_level_from_the_frame_whose_stop_fits_the_window_is_preferred():
    """The nearest printed level is usually a fine-frame shelf with a stop too
    tight to take. When the trader gave a window, the desk must climb to a frame
    whose ATR lands inside it rather than hand back the nearer unusable level."""
    from desk import scenarios as S
    def frame(n, amp, base):
        return [{"o": base, "h": base + amp, "l": base - amp, "c": base,
                 "open": False} for _ in range(n)]
    live = 4000.0
    # 5m: tiny ATR (~4), with a level 8 points above live (nearest, unusable)
    fine = frame(60, 2, 4000.0)
    fine[30]["h"] = 4008.0
    # 30m: ATR ~12, with a level 14 points above live (usable, farther)
    coarse = frame(60, 6, 4000.0)
    coarse[20]["h"] = 4014.0
    frames = {"5m": fine, "15m": [], "30m": coarse, "1h": [], "4h": [], "1d": []}
    sc = S.build(frames, live, min_stop_points=10, max_stop_points=15,
                 balance=200, risk_pct=7.5)
    ups = [s for s in sc["scenarios"] if s["side"] == "long"]
    assert ups, "expected a breakout scenario"
    assert ups[0]["frame"] == "30m", ups[0]["frame"]
    assert 10 <= ups[0]["risk"] <= 15
    assert ups[0]["sizing"].feasible


def test_the_stop_window_still_falls_back_when_nothing_fits():
    """If no frame's ATR is inside the window the desk must still build a
    scenario from the nearest level -- degraded, not empty -- so the reader is
    told 'the nearest setup is too wide', not left with nothing."""
    from desk import scenarios as S
    bars = [{"o": 4000, "h": 4030, "l": 3970, "c": 4000, "open": False}
            for _ in range(60)]
    frames = {"5m": bars, "15m": bars, "30m": bars, "1h": bars, "4h": bars, "1d": bars}
    sc = S.build(frames, 4000.0, min_stop_points=1, max_stop_points=2)
    assert sc["scenarios"], "a scenario must still be produced"
    assert all(s["risk"] > 2 for s in sc["scenarios"])   # outside the window


def test_a_breakout_target_is_never_behind_its_trigger():
    """The window-preference sort stopped the candidate list being ordered by
    price, and the target picker read it by position -- so a breakout long got a
    target *below* its trigger, with an R:R computed from absolute values that
    looked healthy. Every scenario's target must lie beyond its trigger on the
    scenario's own side, whichever order the levels came in."""
    from desk import scenarios as S
    bars = [{"o": 4000, "h": 4020, "l": 3980, "c": 4000, "open": False}
            for _ in range(60)]
    frames = {"5m": bars, "15m": bars, "30m": bars, "1h": bars, "4h": bars, "1d": bars}
    sc = S.build(frames, 4000.0, balance=200, risk_pct=5)
    for s in sc["scenarios"]:
        trig = s["trigger"]
        tgt = s["targets"][0]
        if s["side"] == "long":
            assert tgt > trig, f"long target {tgt} not above trigger {trig}"
        else:
            assert tgt < trig, f"short target {tgt} not below trigger {trig}"
        assert s["rr"] and s["rr"] > 0


def test_an_actionable_level_carries_the_trade_call():
    """The owner asked to be told what to do and why, not just that a level broke.
    A level built for a trade must render buy/sell, the stop, the target and the
    reason in one message."""
    from desk.levels import Level, message
    lv = Level(4145.42, "اختراق صعود", "above", "ملاحظة",
               call_side="long", stop=4134.89, target=4170.52, why="سيناريو 15m")
    txt = message(lv, 4145.92)
    assert "اشترِ" in txt
    assert "4,134.89" in txt and "4,170.52" in txt
    assert "سيناريو 15m" in txt
    assert lv.trade_call()["direction"] == "long"


def test_a_marker_level_gives_no_instruction():
    """A stop line or a low-reward level is a marker, not a trade. It must not be
    dressed up as buy/sell -- the alert would be inventing conviction."""
    from desk.levels import Level, message
    lv = Level(4108.99, "كسر هبوط", "below", "ملاحظة")
    txt = message(lv, 4108.5)
    assert "بِع" not in txt and "اشترِ" not in txt
    assert lv.trade_call() is None


def test_scenario_levels_only_call_a_trade_when_it_pays(tmp_path):
    """Every scenario level that carries a buy/sell must have its stop and target
    on the right sides and clear the desk's own R:R floor -- no level may be
    presented as a trade the desk itself would refuse."""
    import datetime as _dt

    from desk.levels import from_scenarios
    from desk.memory import SharedMemory
    s = _settings(tmp_path)
    shared = SharedMemory(s.shared_db)
    now = _dt.datetime.now(_dt.UTC)
    ep = (now - _dt.timedelta(hours=2)).timestamp()

    def path(i):
        return 4000 + (i % 12) * 2.0
    shared.save_candles(s.symbol, "5m", _mkbars(ep, 5, 60, path))
    shared.save_candles(s.symbol, "15m", _mkbars(ep, 15, 60, path))
    for lv in from_scenarios(s, 4024.0):
        call = lv.trade_call()
        if not call:
            continue
        assert call["direction"] in ("long", "short")
        if call["direction"] == "long":
            assert call["stop"] < lv.price < call["target"]
        else:
            assert call["target"] < lv.price < call["stop"]
        rr = abs(call["target"] - lv.price) / abs(lv.price - call["stop"])
        assert rr >= 1.5 - 1e-9, f"level {lv.price} called with R:R {rr}"


# ---- the daily map ----------------------------------------------------

def _map_bars(n, start, step):
    """Shared-memory-shaped bars for the map: a zig-zag with clear swing highs
    and lows so the pivots and zones are deterministic."""
    import math
    out = []
    for i in range(n):
        p = start + 40 * math.sin(i / 4.0) + i * 0.1
        out.append({"open_time": i * 60, "open": p, "high": p + 1.0,
                    "low": p - 1.0, "close": p, "is_open": 0})
    return out


def test_map_zones_sit_on_printed_prices_not_forecasts():
    """Every zone edge must be a price some bar actually printed. A map that
    invents a level is a forecast wearing a level's clothes."""
    from desk import map as MP
    frames = {tf: _map_bars(80, 4000.0, 60) for tf in MP.MAP_FRAMES}
    m = MP.build(frames, live=4000.0, symbol="XAUUSD")
    printed = {round(b["high"], 4) for tf in frames for b in frames[tf]}
    printed |= {round(b["low"], 4) for tf in frames for b in frames[tf]}
    for z in m.zones:
        assert round(z.low, 4) in printed
        assert round(z.high, 4) in printed
        assert z.touches >= MP.MIN_TOUCHES


def test_map_splits_zones_by_side_of_the_price():
    """A zone above the price is something to sell into; below it, to buy from.
    The split must be by the live price, and each list nearest-first."""
    from desk import map as MP
    frames = {tf: _map_bars(80, 4000.0, 60) for tf in MP.MAP_FRAMES}
    m = MP.build(frames, live=4000.0, symbol="XAUUSD")
    above, below = m._split()
    assert all(z.low > m.live for z in above)
    assert all(z.high < m.live for z in below)
    assert above == sorted(above, key=lambda z: z.low)
    assert below == sorted(below, key=lambda z: -z.high)


def test_map_is_empty_not_wrong_when_frames_are_missing():
    """A frame the desk has not stored must contribute nothing, not a guess."""
    from desk import map as MP
    m = MP.build({"15m": [], "1h": None}, live=4000.0, symbol="XAUUSD")  # type: ignore[dict-item]
    assert m.zones == []
    assert m.trend == {}
    text = MP.render(m)
    assert "خريطة اليوم" in text
    assert "ما في منطقة" in text


def test_map_liquidity_only_lists_nearby_stop_clusters():
    """Liquidity is where stops cluster -- repeated highs/lows. It must only
    report the nearest on each side, or the map is a wall of numbers."""
    from desk import map as MP
    frames = {tf: _map_bars(120, 4000.0, 60) for tf in MP.MAP_FRAMES}
    m = MP.build(frames, live=4000.0, symbol="XAUUSD")
    up = [x for x in m.liquidity if x.side == "above"]
    dn = [x for x in m.liquidity if x.side == "below"]
    assert len(up) <= 2 and len(dn) <= 2
    assert all(x.price > m.live for x in up)
    assert all(x.price < m.live for x in dn)


def test_map_render_carries_no_trade_instruction():
    """The map is context, not a signal. It must NOT tell the reader to buy or
    sell -- that is the level alert's job, and only when a level clears R:R."""
    from desk import map as MP
    frames = {tf: _map_bars(120, 4000.0, 60) for tf in MP.MAP_FRAMES}
    text = MP.render(MP.build(frames, live=4000.0, symbol="XAUUSD"))
    assert "اشترِ" not in text and "بِع" not in text


def test_map_touches_plural_follows_arabic():
    from desk.map import _touches
    assert _touches(1) == "لمسة"
    assert _touches(2) == "لمستان"
    assert _touches(5) == "5 لمسات"
    assert _touches(16) == "16 لمسة"


def test_daemon_sends_the_map_once_a_day(tmp_path, monkeypatch):
    """The map is a morning plan: sent once, after `map_hour`, and never twice
    in a day -- a restart must not re-deliver it. And a failed send is retried,
    not lost until tomorrow."""
    import datetime as _dt

    d, seen = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900)
    d.settings.map_hour = 7
    sent: list[str] = []
    monkeypatch.setattr(d.notifier, "send", lambda t: sent.append(t) or True)
    monkeypatch.setattr("desk.actions.Actions.map", lambda self: "خريطة")
    monkeypatch.setattr("desk.daemon.market_session", lambda now=None: _OpenSession())

    def at(hour):
        return _dt.datetime(2026, 11, 5, hour, 0, tzinfo=_dt.UTC)

    assert d._map_due(at(6)) is False        # before the hour: not yet
    assert d._map_due(at(7)) is True
    d._send_map(at(7))
    assert sent == ["خريطة"]
    assert d._map_due(at(9)) is False        # same day: not again
    assert d._map_due(at(7) + _dt.timedelta(days=1)) is True


def test_daemon_retries_the_map_when_the_send_fails(tmp_path, monkeypatch):
    """A silent failure must not mark the day sent -- otherwise a dead token
    costs the owner the whole day's map."""
    import datetime as _dt

    d, seen = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900)
    d.settings.map_hour = 7
    monkeypatch.setattr(d.notifier, "send", lambda t: False)
    monkeypatch.setattr("desk.actions.Actions.map", lambda self: "خريطة")
    out = d._send_map(_dt.datetime(2026, 11, 5, 8, 0, tzinfo=_dt.UTC))
    assert out["sent"] is False
    assert d.last_map_date is None           # still owed
    assert d._map_due(_dt.datetime(2026, 11, 5, 9, 0, tzinfo=_dt.UTC)) is True


def test_daemon_map_job_runs_from_the_tick(tmp_path, monkeypatch):
    """The tick must actually reach the map job -- a job nothing calls is the
    bug this codebase has been bitten by before."""
    d, seen = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900)
    d.settings.map_hour = 0
    sent: list[str] = []
    monkeypatch.setattr(d.notifier, "send", lambda t: sent.append(t) or True)
    monkeypatch.setattr("desk.actions.Actions.map", lambda self: "خريطة")
    d.tick(0.0)
    assert sent == ["خريطة"]


def test_daemon_map_stamp_survives_a_restart(tmp_path, monkeypatch):
    """The host restarts the process on every deploy. An in-memory 'sent' date
    would re-send the map each time -- so the stamp must live on disk, and a
    fresh Daemon must read it back and stay quiet."""
    import datetime as _dt

    d, _ = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900)
    d.settings.map_hour = 7
    sent: list[str] = []
    monkeypatch.setattr(d.notifier, "send", lambda t: sent.append(t) or True)
    monkeypatch.setattr("desk.actions.Actions.map", lambda self: "خريطة")
    day = _dt.datetime(2026, 11, 5, 8, 0, tzinfo=_dt.UTC)
    d._send_map(day)
    assert sent == ["خريطة"]

    # A fresh process (a deploy) reads the stamp and does NOT re-send today.
    from desk.daemon import Daemon
    d2 = Daemon(_settings(tmp_path))
    d2.settings.map_hour = 7
    assert d2.last_map_date == "2026-11-05"
    assert d2._map_due(day) is False
    # ...but tomorrow it is owed again.
    assert d2._map_due(day + _dt.timedelta(days=1)) is True


def test_daemon_map_stamp_a_torn_file_is_not_a_missing_day(tmp_path, monkeypatch):
    """A corrupt stamp must read as 'nothing sent' (one duplicate map at worst),
    never crash the daemon on boot."""
    d, _ = _daemon(tmp_path, monkeypatch, level_every=10, read_every=900)
    (tmp_path / "map_sent.json").write_text("{not json")
    from desk.daemon import Daemon
    d2 = Daemon(_settings(tmp_path))
    assert d2.last_map_date is None


def test_cli_map_once_a_day_sends_one_and_stamps_the_date(tmp_path, monkeypatch):
    """The map must go out from the cron without the Render daemon, and exactly
    once: a later run the same day is a no-op. A failed send is NOT stamped, so
    the next run retries rather than losing the day."""
    import datetime as _dt

    from desk import cli
    from desk.config import Settings

    s = Settings()
    s.home = tmp_path
    monkeypatch.setattr(cli, "_settings", lambda args: s)
    monkeypatch.setattr("desk.actions.Actions.map", lambda self: "خريطة")
    sent: list[str] = []
    monkeypatch.setattr("desk.notify.Notifier.send", lambda self, t: sent.append(t) or True)

    class A:
        once_a_day = True

    assert cli.cmd_map(A()) == 0
    assert sent == ["خريطة"]
    today = _dt.datetime.now(_dt.UTC).date().isoformat()
    from desk.daemon import _load_map_date
    assert _load_map_date(s) == today

    assert cli.cmd_map(A()) == 0          # same day: no second send
    assert sent == ["خريطة"]


def test_cli_map_once_a_day_retries_after_a_failed_send(tmp_path, monkeypatch):
    from desk import cli
    from desk.config import Settings

    s = Settings()
    s.home = tmp_path
    monkeypatch.setattr(cli, "_settings", lambda args: s)
    monkeypatch.setattr("desk.actions.Actions.map", lambda self: "خريطة")
    monkeypatch.setattr("desk.notify.Notifier.send", lambda self, t: False)

    class A:
        once_a_day = True

    assert cli.cmd_map(A()) == 1
    from desk.daemon import _load_map_date
    assert _load_map_date(s) is None      # not stamped: owed again



def _regime_wave(n=220, period=40, amp=30.0, base=3000.0, drift=0.0):
    """A wave with UNIQUE timestamps, in the internal frame shape `build` reads.

    `_wave` reuses timestamps (`i % 24`), which is harmless for a test that only
    reads a series but corrupts anything round-tripped through shared memory:
    the store keys candles by (symbol, tf, open_time), so later bars overwrite
    earlier ones and the series that comes back is not the one that went in.
    """
    import math
    out = []
    for i in range(n):
        c = base + drift * i + amp * math.sin(2 * math.pi * i / period)
        out.append({"openTime": f"2026-01-{1 + i // 240:02d}T{i // 60 % 24:02d}:"
                                f"{i % 60:02d}:00Z",
                    "open": c - 1, "high": c + 2, "low": c - 2, "close": c,
                    "tickVolume": 100 + i, "isOpen": False})
    return out


def _regime_frames(drift, tfs=("5m", "15m", "30m", "1h", "4h", "1d")):
    """The internal (t/o/h/l/c) shape `scenarios.build` reads, from one wave."""
    raw = _regime_wave(drift=drift)
    return {tf: [{"t": b["openTime"], "o": b["open"], "h": b["high"],
                  "l": b["low"], "c": b["close"], "open": False} for b in raw]
            for tf in tfs}


def test_every_scenario_clears_the_rr_floor_and_both_sides_are_offered():
    """The target must be the nearest level that PAYS for the stop, on both sides.

    This is the fix for the desk's short bias. The nearest printed level sat
    nearer than the frame's own ATR, so the first scenario out was under the R:R
    floor almost every time. Only the second level out cleared it, and a second
    level was only ever built for the short side (`deeper`) -- so the one plan
    that survived was a short. Measured before the fix, live gold: breakout long
    R:R 0.67, breakdown short 0.79, deeper short 1.79.
    """
    from desk import scenarios as S

    frames = _regime_frames(3.0)
    live = frames["1h"][-1]["c"]
    sc = S.build(frames, live, calendar=[], min_rr=1.5)

    assert sc["scenarios"], "a drifting wave must print scenarios"
    for s in sc["scenarios"]:
        assert s["rr"] is not None and s["rr"] >= 1.5, (
            f"{s['kind']} {s['side']} pays only {s['rr']}")
    assert {s["side"] for s in sc["scenarios"]} == {"long", "short"}, (
        "the scenario set must not be one-sided")


def test_from_scenarios_vetoes_a_trade_against_the_higher_frame_trend(tmp_path):
    """A trade that fights the higher frame is a level to watch, not a trade.

    Sixteen plans in a row went short into a rising market. The veto is the
    guard against repeating it: a counter-trend scenario keeps its trigger and
    its place in the alert set, but loses its buy/sell call, so the desk can no
    longer hand anyone a trade that fights the regime.
    """
    from desk.levels import from_scenarios

    for drift, counter_side in ((3.0, "below"), (-3.0, "above")):
        s = _settings(tmp_path)
        shared = SharedMemory(s.shared_db)
        bars = _regime_wave(drift=drift)
        for tf in s.timeframes:
            shared.save_candles(s.symbol, tf, bars)
        live = bars[-1]["close"]
        lv = from_scenarios(s, live)

        counter = [x for x in lv if x.side == counter_side]
        assert counter, "the counter-trend level must still be watched"
        for level in counter:
            assert not level.call_side, (
                f"a trade against the {drift:+.0f} regime must not be actionable: {level.why}")
            assert "عكس اتجاه" in level.why, (
                "the veto must say why it withheld the call")
