"""Scoreboard — grade the desk's past calls against what price actually did.

This is the piece that turns the desk from an opinion machine into a system with
a record. Every run stores its entry price and its implied bias; this module
walks the stored runs, reads the price at each horizon from the candles already
in the shared memory, and marks each call right or wrong.

Two honest limits, both enforced here rather than papered over:

* **A horizon is graded only if a candle actually exists past it.** If the run is
  too recent, the horizon is skipped — never filled with the current price, which
  would score a call against itself.
* **"Flat" is not a directional call**, so it is excluded from hit-rate. Counting
  it as a win when price barely moved would inflate the number.

Hit-rate is reported with its sample size everywhere. A rate over 3 graded runs
is labelled as such; it is a running tally, not a validated edge.
"""

from __future__ import annotations

import bisect
import datetime as dt
import json
from dataclasses import dataclass, field

from .config import NEUTRAL_BAND
from .memory import SharedMemory

# Horizon label -> (timeframe to read the close from, seconds to wait).
HORIZONS: dict[str, tuple[str, int]] = {
    "1h": ("5m", 3600),
    "4h": ("15m", 4 * 3600),
    "1d": ("1h", 24 * 3600),
}
# The plan is graded on a finer walk than the bias, so a stop taken early shows.
PLAN_HORIZONS: dict[str, tuple[str, int]] = {
    "4h": ("5m", 4 * 3600),
    "1d": ("15m", 24 * 3600),
}
# A move smaller than this is noise, not a call that can be scored either way.
DEADBAND_PCT = 0.05


def _parse(ts: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


@dataclass
class Horizon:
    label: str
    graded: int = 0
    correct: int = 0
    skipped: int = 0

    @property
    def hit_rate(self) -> float | None:
        return (self.correct / self.graded) if self.graded else None


@dataclass
class AgentScore:
    agent: str
    horizon: str
    graded: int = 0
    correct: int = 0

    @property
    def hit_rate(self) -> float | None:
        return (self.correct / self.graded) if self.graded else None


@dataclass
class Scoreboard:
    horizons: dict[str, Horizon] = field(default_factory=dict)
    agents: list[AgentScore] = field(default_factory=list)
    runs_seen: int = 0
    runs_graded: int = 0
    plans_graded: int = 0
    plans_skipped: int = 0
    plan_results: dict[str, int] = field(default_factory=dict)
    r_multiples: list[float] = field(default_factory=list)

    @property
    def expectancy_r(self) -> float | None:
        """Mean R per resolved plan. This is the number that matters: a 60% win
        rate at 1.2R loses money, and only the R average shows it."""
        return (sum(self.r_multiples) / len(self.r_multiples)) if self.r_multiples else None

    def report(self) -> str:
        lines = [f"Scoreboard — {self.runs_graded}/{self.runs_seen} runs graded", ""]
        if not self.runs_graded:
            lines.append("No run has a horizon far enough in the past yet.")
            lines.append("A horizon is graded only once a candle exists past it —")
            lines.append("never against the current price.")
            return "\n".join(lines)
        lines.append("Desk (implied bias, flat excluded):")
        for label in HORIZONS:
            h = self.horizons.get(label)
            if not h or not h.graded:
                continue
            lines.append(f"  +{label:<3} {h.correct}/{h.graded} = {h.hit_rate:5.1%}"
                         f"   (skipped {h.skipped})")
        if self.plans_graded:
            tgt = self.plan_results.get("target", 0)
            stp = self.plan_results.get("stop", 0)
            exp = self.expectancy_r
            lines.append("")
            lines.append("Plans (stop vs target, first touch wins):")
            lines.append(f"  {tgt}/{self.plans_graded} reached the target"
                         f"   ({tgt / self.plans_graded:.0%})"
                         + (f"   expectancy {exp:+.2f}R" if exp is not None else ""))
            lines.append(f"  {stp} stopped out first   (skipped {self.plans_skipped})")
        if self.agents:
            lines.append("")
            lines.append("Per agent (the case for re-weighting):")
            for a in sorted(self.agents, key=lambda x: (-(x.hit_rate or 0), -x.graded)):
                if a.graded:
                    lines.append(f"  {a.agent:>12} +{a.horizon:<3} "
                                 f"{a.correct}/{a.graded} = {a.hit_rate:5.1%}")
        return "\n".join(lines)


class Grader:
    def __init__(self, shared: SharedMemory):
        self.shared = shared

    def _price_at(self, symbol: str, tf: str, when: dt.datetime) -> float | None:
        """Close of the last candle at or before `when`, or None if the series
        does not reach that far. Never falls back to the newest bar."""
        bars = self.shared.load_candles(symbol, tf)
        if not bars:
            return None
        times = []
        for b in bars:
            t = _parse(b["open_time"])
            if t is None:
                return None
            times.append(t)
        if times[-1] < when:
            return None                      # the data has not reached the horizon
        i = bisect.bisect_right(times, when) - 1
        return bars[max(0, i)]["close"] if i >= 0 else None

    def _walk_plan(self, symbol: str, tf: str, start: dt.datetime, end: dt.datetime,
                   direction: str, entry: float, stop: float,
                   target: float) -> tuple[str, int, float | None]:
        """Walk the bars between `start` and `end` and report which of the stop or
        the target was touched FIRST. Returns (result, bars, r_multiple).

        This is the honest way to grade a plan. Grading the direction alone would
        count a short that fell to the target *after* first taking out the stop as
        a win — hiding the desk's worst errors. Only the first touch counts.
        """
        bars = self.shared.load_candles(symbol, tf)
        stamped = [(t, b) for b in bars if (t := _parse(b["open_time"])) is not None]
        window = [b for t, b in stamped if start < t <= end]
        risk = abs(entry - stop)
        r_mult = (abs(target - entry) / risk) if risk else None
        long = direction == "up"
        for n, b in enumerate(window, 1):
            hit_stop = (b["low"] <= stop) if long else (b["high"] >= stop)
            hit_tgt = (b["high"] >= target) if long else (b["low"] <= target)
            # When a single bar spans both, the stop is assumed first: the
            # conservative reading, and the one that cannot flatter the desk.
            if hit_stop:
                return "stop", n, -1.0
            if hit_tgt:
                return "target", n, r_mult
        return "open", len(window), None

    def _grade_plans(self, run_id: str, symbol: str, payload: dict,
                     started: dt.datetime, now: dt.datetime,
                     board: Scoreboard) -> None:
        """Grade the trade plan itself: stop-before-target, not just direction."""
        # The run payload *is* the recommendation dict (older rows may nest it).
        rec = payload.get("recommendation") or payload
        entry, stop = rec.get("entry"), rec.get("stop")
        targets = rec.get("targets") or []
        direction = rec.get("direction")
        if direction not in ("long", "short") or not entry or not stop or not targets:
            return
        target = targets[0]["level"]
        for label, (tf, secs) in PLAN_HORIZONS.items():
            if now < started + dt.timedelta(seconds=secs):
                board.plans_skipped += 1
                continue
            end = started + dt.timedelta(seconds=secs)
            if self._price_at(symbol, tf, end) is None:
                board.plans_skipped += 1
                continue
            result, bars, r_mult = self._walk_plan(
                symbol, tf, started, end, "up" if direction == "long" else "down",
                entry, stop, target)
            if result == "open":
                board.plans_skipped += 1
                continue
            board.plans_graded += 1
            board.plan_results[result] = board.plan_results.get(result, 0) + 1
            if r_mult is not None:
                board.r_multiples.append(r_mult)
            self.shared.save_plan_outcome(run_id, label, direction, entry, stop,
                                          target, result, bars, r_mult)

    def grade(self) -> Scoreboard:
        board = Scoreboard()
        runs = self.shared.all_runs()
        board.runs_seen = len(runs)
        now = dt.datetime.now(dt.UTC)
        agent_scores: dict[tuple[str, str], AgentScore] = {}

        for run in runs:
            try:
                payload = json.loads(run["payload"] or "{}")
            except ValueError:
                continue
            bias = payload.get("bias") or {}
            direction = bias.get("direction")
            # Runs written before the bias field existed still carry a score in
            # the run row; read that so the older history stays gradable.
            if not direction:
                s = run.get("score") or 0.0
                direction = ("up" if s > NEUTRAL_BAND
                             else ("down" if s < -NEUTRAL_BAND else "flat"))
            q = (payload.get("gather") or {}).get("quote") or {}
            entry = q.get("mid")
            started = dt.datetime.fromtimestamp(run["started_at"], dt.UTC)
            if direction not in ("up", "down") or not entry:
                continue                     # flat or priceless: nothing to grade

            self._grade_plans(run["run_id"], run["symbol"], payload, started, now, board)

            graded_any = False
            for label, (tf, secs) in HORIZONS.items():
                board.horizons.setdefault(label, Horizon(label))
                h = board.horizons[label]
                if now < started + dt.timedelta(seconds=secs):
                    h.skipped += 1
                    continue
                exit_ = self._price_at(run["symbol"], tf, started + dt.timedelta(seconds=secs))
                if exit_ is None:
                    h.skipped += 1
                    continue
                move = (exit_ / entry - 1) * 100
                if abs(move) < DEADBAND_PCT:
                    h.skipped += 1           # inside the deadband: not a scorable move
                    continue
                correct = int((move > 0) == (direction == "up"))
                h.graded += 1
                h.correct += correct
                graded_any = True
                self.shared.save_outcome(run["run_id"], run["symbol"], label,
                                         entry, exit_, move, direction,
                                         bias.get("score", 0.0), correct)
                self._grade_agents(run["run_id"], label, move, agent_scores)
            if graded_any:
                board.runs_graded += 1

        board.agents = list(agent_scores.values())
        return board

    def _grade_agents(self, run_id: str, horizon: str, move: float,
                      acc: dict[tuple[str, str], AgentScore]) -> None:
        """Score each specialist's own stance on the same move, so the desk can
        eventually re-weight from evidence rather than taste."""
        for st in self.shared.load_studies(run_id):
            if st["agent"] == "risk" or st["stance"] not in ("bull", "bear"):
                continue
            correct = int((move > 0) == (st["stance"] == "bull"))
            self.shared.save_agent_outcome(run_id, st["agent"], horizon,
                                           st["stance"], st["score"], move, correct)
            key = (st["agent"], horizon)
            a = acc.setdefault(key, AgentScore(st["agent"], horizon))
            a.graded += 1
            a.correct += correct
