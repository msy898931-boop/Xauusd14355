"""Backtest — does the desk actually make money, or only look tidy?

THE QUESTION THIS ANSWERS
The desk had a bot, alerts, and green CI but *zero graded runs*: nobody had ever
asked whether the method makes money. This walks the stored daily bars one at a
time, lets the real agents decide at each bar using only what was knowable then,
and grades the plan the way `scoreboard` grades a live one -- first touch wins.

WHAT IS REAL AND WHAT IS NOT
* Real: the agents, the weights, `synthesise`, `_plan`, and the stop/target
  first-touch grading. Nothing is re-implemented, so a change to the desk changes
  the backtest.
* Not real, and reported as such: there is no historical news or cross-market
  feed, so `news` and `correlation` are neutral here (the same way the desk
  treats a failed read: empty, never an all-clear). Only the daily frame has
  enough history, so every agent is restricted to `1d`. The result measures the
  price-only subset on one timeframe -- a floor on the desk, not the whole desk.

NO LOOK-AHEAD
At bar i the agents see bars 0..i and the quote is bar i's close. The walk starts
at i+1. An entry is the close the desk could actually have acted on, and a bar
that touches both the stop and the target counts as the stop -- the same
conservative reading the live scoreboard uses.
"""

from __future__ import annotations

import csv
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import sources
from .config import Settings
from .memory import SharedMemory
from .orchestrator import Orchestrator


@dataclass
class Trade:
    """One decision the desk was willing to stand behind, and how it resolved."""
    when: str
    direction: str
    entry: float
    stop: float
    target: float
    rr: float | None
    result: str          # "target" | "stop" | "open"
    bars: int
    r_multiple: float | None


@dataclass
class Report:
    symbol: str
    bars_total: int
    decisions: int
    directional: int
    trades: list[Trade] = field(default_factory=list)
    bias_hits: dict[str, list[int]] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)

    # ---- summary numbers ------------------------------------------------
    @property
    def closed(self) -> list[Trade]:
        return [t for t in self.trades if t.result in ("target", "stop")]

    @property
    def wins(self) -> int:
        return sum(1 for t in self.closed if t.result == "target")

    @property
    def losses(self) -> int:
        return sum(1 for t in self.closed if t.result == "stop")

    @property
    def hit_rate(self) -> float | None:
        return self.wins / len(self.closed) if self.closed else None

    @property
    def expectancy_r(self) -> float | None:
        """The only number that matters. 60% at 1.2R loses money; 40% at 3R wins.
        A hit rate alone cannot tell them apart."""
        rs = [t.r_multiple for t in self.closed if t.r_multiple is not None]
        return sum(rs) / len(rs) if rs else None

    def bias_rate(self, horizon: str) -> tuple[int, int] | None:
        v = self.bias_hits.get(horizon)
        if not v:
            return None
        return sum(v), len(v)


def load_daily(path: Path) -> list[dict]:
    """The stored daily bars, oldest first, in the shape `save_candles` wants."""
    out = []
    with path.open() as fh:
        for row in csv.DictReader(fh):
            out.append({
                "openTime": row["openTime"],
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "tickVolume": int(float(row.get("tickVolume") or 0)),
                "isOpen": False,
            })
    return sorted(out, key=lambda b: b["openTime"])


def _walk(bars: list[dict], start: int, direction: str, entry: float,
          stop: float, target: float, horizon: int) -> tuple[str, int, float | None]:
    """First touch wins, within `horizon` bars. A bar that spans both is a stop."""
    risk = abs(entry - stop)
    r_mult = (abs(target - entry) / risk) if risk else None
    long = direction == "long"
    window = bars[start:start + horizon]
    for n, b in enumerate(window, 1):
        if (b["low"] <= stop) if long else (b["high"] >= stop):
            return "stop", n, -1.0
        if (b["high"] >= target) if long else (b["low"] <= target):
            return "target", n, r_mult
    return "open", len(window), None


def run_backtest(csv_path: Path, *, symbol: str = "XAUUSD", warmup: int = 120,
                 horizons: tuple[int, ...] = (1, 5, 10)) -> Report:
    """Walk the daily bars and grade every plan the desk was willing to state.

    News and cross-market are neutral here by design: with no historical feed
    they must not invent a direction, so their weight sits out. That is the same
    fail-closed behaviour the live desk shows when a source read fails.
    """
    bars = load_daily(csv_path)
    if len(bars) <= warmup + max(horizons):
        raise ValueError(f"not enough history: {len(bars)} bars, need > {warmup + max(horizons)}")

    # ISOLATION IS NOT OPTIONAL. `save_candles` upserts and never deletes, so a
    # reused store keeps every bar a previous pass wrote -- including bars from
    # *after* the one being decided. Reading those is look-ahead, and it is
    # silent: the first version of this module reused the package's store and the
    # trend agent saw all 500 bars at every step, so it said "bear" 379/379 times
    # on a market that rose 18%. A fresh store per run is what makes the walk
    # honest, and the guard below fails loudly if one ever leaks.
    home = Path(tempfile.mkdtemp(prefix="desk-backtest-"))
    try:
        return _walk_history(csv_path, bars, symbol, warmup, horizons, home)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _walk_history(csv_path: Path, bars: list[dict], symbol: str, warmup: int,
                  horizons: tuple[int, ...], home: Path) -> Report:
    settings = Settings()
    settings.home = home
    settings.timeframes = ("1d",)
    settings.groq_key = ""                       # no model: computed studies only
    settings.peers = ()                          # no peer feed in history
    settings.ensure_dirs()

    orch = Orchestrator(settings)
    for agent in orch.agents:
        agent.frames = ("1d",)

    # No network, ever. A read fails instantly instead of retrying, and the desk
    # already treats a failed read as empty rather than as an all-clear.
    def _no_source(*_a, **_k):
        raise sources.SourceError("backtest: no historical feed")

    for name in ("fetch_candles", "fetch_quote", "fetch_news",
                 "fetch_calendar", "fetch_summary"):
        setattr(sources, name, _no_source)

    shared: SharedMemory = orch.shared
    for b in bars[:warmup + 1]:
        shared.save_candles(symbol, "1d", [b])

    rep = Report(symbol=symbol, bars_total=len(bars), decisions=0, directional=0)
    rep.skipped = {"neutral": 0, "no_plan": 0, "risk_blocked": 0}

    for i in range(warmup, len(bars) - 1):
        bar = bars[i]
        shared.save_candles(symbol, "1d", [bar])
        # Fail loudly rather than trade on a bar that did not exist yet.
        newest = shared.load_candles(symbol, "1d")[-1]["open_time"]
        if newest != bar["openTime"]:
            raise AssertionError(
                f"look-ahead: the store's newest bar is {newest} but the decision "
                f"bar is {bar['openTime']}")
        # The quote is the price the desk could act on right now: this bar's
        # close. Marked open and fresh so the risk gate judges the setup, not
        # the simulation.
        shared.save_quote(symbol, {
            "mid": bar["close"], "bid": bar["close"], "ask": bar["close"],
            "market_state": "open", "stale": False, "quote_age_s": 0,
            "day_diff_pct": round((bar["close"] / bars[i - 1]["close"] - 1) * 100, 4),
            "source": "backtest",
        })

        studies, _errors = orch.dispatch(f"bt{i}")
        verdict, _score, rec = orch.synthesise(studies)
        rep.decisions += 1

        # The desk's implied bias, graded even when it declines to trade -- the
        # same honesty the live scoreboard applies.
        direction = (rec.get("bias") or {}).get("direction")
        if direction in ("up", "down"):
            rep.directional += 1
            for h in horizons:
                j = i + h
                if j >= len(bars):
                    continue
                moved = bars[j]["close"] - bar["close"]
                if abs(moved) < 1e-9:
                    continue
                correct = (moved > 0) if direction == "up" else (moved < 0)
                rep.bias_hits.setdefault(f"{h}d", []).append(1 if correct else 0)

        if verdict not in ("long", "short"):
            rep.skipped["neutral" if rec.get("entry") is None and rec.get("allowed", True)
                        else "risk_blocked"] += 1
            continue
        entry, stop = rec.get("entry"), rec.get("stop")
        targets = rec.get("targets") or []
        if not entry or not stop or not targets:
            rep.skipped["no_plan"] += 1
            continue

        result, n, r = _walk(bars, i + 1, verdict, entry, stop,
                             targets[0]["level"], max(horizons))
        rep.trades.append(Trade(
            when=bar["openTime"], direction=verdict, entry=entry, stop=stop,
            target=targets[0]["level"], rr=rec.get("rr"), result=result,
            bars=n, r_multiple=r))
    return rep


def buy_and_hold(bars: list[dict], warmup: int) -> float:
    """The bar the desk has to clear. A long held from the first decision to the
    last, in percent -- if the desk cannot beat holding, it adds nothing."""
    a, b = bars[warmup]["close"], bars[-1]["close"]
    return (b / a - 1) * 100


def format_report(rep: Report, *, buy_hold_pct: float | None = None,
                  min_rr: float = 1.5) -> str:
    lines = [f"Backtest — {rep.symbol} (daily bars, price-only agents)",
             f"  {rep.bars_total} bars · {rep.decisions} decisions · "
             f"{rep.directional} had a directional bias"]
    closed = rep.closed
    if not closed:
        lines.append("  No plan was stated, so there is nothing to grade.")
    else:
        hit = rep.hit_rate
        exp = rep.expectancy_r
        lines += [
            "",
            "  Plans (stop vs target, first touch wins):",
            f"    {rep.wins} reached the target, {rep.losses} stopped out"
            f"   ({hit:.0%} hit rate)" if hit is not None else "",
            f"    expectancy {exp:+.2f}R" if exp is not None else "",
        ]
        # Expectancy is the verdict, not the hit rate: a high hit rate at a poor
        # R can still lose money, and the reverse.
        if exp is not None:
            verdict = ("edge" if exp > 0.05 else
                       "no edge" if exp > -0.05 else "losing")
            lines.append(f"    -> {verdict} on this sample")
    lines.append("")
    lines.append("  Desk's implied bias (direction only, flat excluded):")
    for h in sorted(rep.bias_hits):
        r = rep.bias_rate(h)
        if r:
            correct, n = r
            lines.append(f"    +{h:<4} {correct}/{n} = {correct / n:.1%}")
    if buy_hold_pct is not None:
        lines += ["", f"  Buy and hold over the same window: {buy_hold_pct:+.1f}%",
                  "  (a direction hit rate is not a return; this is the bar the",
                  "   desk has to clear to be worth running at all)"]
    lines += ["", "  Skipped: " + ", ".join(f"{k} {v}" for k, v in rep.skipped.items())]
    return "\n".join(x for x in lines if x != "")
