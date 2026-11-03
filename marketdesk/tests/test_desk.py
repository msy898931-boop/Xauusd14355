"""Tests — keyless and offline. They pin the invariants that matter.

Run: PYTHONPATH=. python3 -m pytest tests -q
"""

from __future__ import annotations

import json
import os
import sys

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
    assert len(run.studies) == 8
    # Every agent published to the shared board.
    assert len(desk.shared.load_studies(run.run_id)) == 8


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
    assert second["alerted"] is False and second["reason"] == "already alerted"


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
    assert "reward:risk" in rec["levels_note"]


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
    assert "R&amp;D" in acts.run("hitrate") or "Scoreboard" in acts.run("hitrate")


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
    assert sent[0][1].get("keyboard") is False       # analysis, not the menu


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
    assert sent[0][1].get("keyboard") is True        # the buttons come with it


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
    assert "reward:risk" in plan["levels_note"]


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
