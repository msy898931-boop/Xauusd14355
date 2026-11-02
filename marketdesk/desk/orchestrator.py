"""Orchestrator — the central management of the desk.

One run:
  1. gather  — pull every candle biquote serves, the live quote, and the
               market summary into the shared memory (the news agent pulls news
               and the calendar itself, as instructed).
  2. dispatch— wake every specialist concurrently; each reads the shared memory,
               computes its study, and publishes it back.
  3. synthesise — weight the signed scores, respect the risk gate, and write the
               final recommendation with its levels and provenance.

The budget is enforced so a slow model cannot make the desk slow: agents run in
a thread pool and any that overrun are reported as timed-out, never as neutral.
"""

from __future__ import annotations

import concurrent.futures as cf
import time
import uuid
from dataclasses import dataclass, field

from . import sources
from .agents import ROSTER, Study
from .config import Settings
from .llm import LLM
from .memory import SharedMemory


@dataclass
class Run:
    run_id: str
    symbol: str
    started: float
    studies: list[Study] = field(default_factory=list)
    verdict: str = "no-trade"
    score: float = 0.0
    recommendation: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    duration_s: float = 0.0


def _pick_targets(levels: list[dict], entry: float, stop: float, min_rr: float,
                  *, above: bool) -> list[dict]:
    """Targets that clear the reward:risk bar, taken from the whole level list.

    The nearest levels are usually too close to pay for the stop, so choosing
    "the next three levels" and then rejecting them would make the desk refuse
    every setup. This walks outward and keeps the levels that actually pay,
    capped at three so a plan stays readable.
    """
    risk = abs(entry - stop)
    if not risk:
        return [{"level": l["level"], "kind": l["kind"]} for l in levels[:3]]
    out = []
    for lv in levels:
        lvl = lv["level"]
        if above and lvl <= entry:
            continue
        if not above and lvl >= entry:
            continue
        if abs(lvl - entry) / risk >= min_rr:
            out.append({"level": lvl, "kind": lv["kind"]})
        if len(out) == 3:
            break
    return out


class Orchestrator:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.settings.ensure_dirs()
        self.shared = SharedMemory(self.settings.shared_db)
        self.llm = LLM(self.settings.groq_key, self.settings.models)
        self.agents = [cls(self.settings, self.shared, self.llm) for cls in ROSTER]

    # ---- phase 1: gather ----------------------------------------------
    def gather(self) -> dict:
        """Pull the market data into shared memory. A source failure is recorded
        in `errors` and the run continues with whatever was gathered."""
        s = self.settings
        report: dict = {"symbol": s.symbol, "candles": {}, "quote": None, "errors": []}
        for tf in s.timeframes:
            try:
                bars = sources.fetch_candles(s.symbol, tf)
                self.shared.save_candles(s.symbol, tf, bars)
                report["candles"][tf] = len(bars)
            except sources.SourceError as exc:
                report["errors"].append(f"candles {tf}: {exc}")
        try:
            q = sources.fetch_quote(s.symbol)
            self.shared.save_quote(s.symbol, q)
            report["quote"] = q
        except sources.SourceError as exc:
            report["errors"].append(f"quote: {exc}")
        return report

    # ---- phase 2: dispatch --------------------------------------------
    def dispatch(self, run_id: str) -> tuple[list[Study], list[str]]:
        """Run the agents. The news agent goes FIRST, alone, because two later
        agents depend on what it publishes: the risk gate reads its
        `event_risk_24h`, and the orchestrator reads its tone. Running it in the
        same pool as the others is a race — the gate reads SQLite instantly and
        the news agent waits on the network, so the gate would see an empty
        board and wave through an event it was built to block."""
        studies: list[Study] = []
        errors: list[str] = []
        deadline = time.time() + self.settings.budget_s

        news = next((a for a in self.agents if a.name == "news"), None)
        rest = [a for a in self.agents if a is not news]
        if news is not None:
            try:
                studies.append(news.run(run_id))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"news: {exc.__class__.__name__}: {exc}")

        # `cf.wait` is what makes the budget real: it returns once the timeout
        # elapses whether or not the work is done. Waiting on `as_completed`
        # instead would block on the first slow agent and the ceiling would be a
        # claim, not a limit. A worker that has not returned by the deadline is
        # reported as timed-out — never silently counted as neutral.
        remaining = max(0.1, deadline - time.time())
        pool = cf.ThreadPoolExecutor(max_workers=min(8, len(rest)) or 1)
        try:
            futures = {pool.submit(a.run, run_id): a for a in rest}
            done, not_done = cf.wait(futures, timeout=remaining)
            for fut in done:
                try:
                    studies.append(fut.result())
                except Exception as exc:  # noqa: BLE001 - one agent must not sink the run
                    errors.append(f"{futures[fut].name}: {exc.__class__.__name__}: {exc}")
            for fut in not_done:
                errors.append(f"{futures[fut].name}: exceeded the "
                              f"{self.settings.budget_s:g}s budget")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return studies, errors

    # ---- phase 3: synthesise ------------------------------------------
    def synthesise(self, studies: list[Study]) -> tuple[str, float, dict]:
        by_name = {s.agent: s for s in studies}
        risk = by_name.get("risk")
        allowed = bool(risk.payload.get("allowed")) if risk else True

        weighted = 0.0
        wsum = 0.0
        for s in studies:
            if s.agent == "risk":
                continue           # the gate is not a directional vote
            w = next((a.weight for a in self.agents if a.name == s.agent), 1.0)
            weighted += s.score * w * (0.5 + 0.5 * s.confidence)
            wsum += w
        norm = max(-1.0, min(1.0, weighted / wsum)) if wsum else 0.0

        levels = by_name.get("levels")
        px = (levels.payload.get("price") if levels else None) or (self.shared.load_quote(self.settings.symbol) or {}).get("mid")
        vol = by_name.get("volatility")
        atr1h = (vol.payload.get("frames", {}).get("1h", {}) or {}).get("atr") if vol else None

        if not allowed:
            verdict = "no-trade"
        elif norm > self.settings.neutral_band:
            verdict = "long"
        elif norm < -self.settings.neutral_band:
            verdict = "short"
        else:
            verdict = "no-trade"

        rec = self._plan(verdict, px, atr1h, levels, risk, by_name, norm)
        # A plan the reward:risk filter rejected is not a trade: the direction may
        # be right, but the entry is not worth taking. Downgrade honestly.
        if verdict in ("long", "short") and rec.get("entry") is None:
            verdict = "no-trade"
            rec["direction"] = verdict
        rec["score"] = round(norm, 3)
        rec["weights_sum"] = round(wsum, 2)
        rec["allowed"] = allowed
        # The desk's *implied* bias, recorded even when it declines to trade.
        # Without this a run that says "no trade" leaves nothing to grade, and
        # the scoreboard could never tell whether the caution was earned.
        band = self.settings.neutral_band
        rec["bias"] = {"direction": "up" if norm > band else ("down" if norm < -band else "flat"),
                       "score": round(norm, 4)}
        rec["per_frame_bias"] = self._frame_bias(studies)
        return verdict, norm, rec

    def _frame_bias(self, studies: list[Study]) -> dict:
        """A signed conviction per timeframe, summed from every agent that
        reported one. It is a readout, not a decision input."""
        acc: dict[str, list[float]] = {}
        for s in studies:
            frames = (s.payload or {}).get("frames") or {}
            for tf, d in frames.items():
                if not isinstance(d, dict):
                    continue
                if "vote" in d:
                    acc.setdefault(tf, []).append(float(d["vote"]))
                elif d.get("structure") in ("up", "down", "range"):
                    acc.setdefault(tf, []).append(
                        1.0 if d["structure"] == "up" else (-1.0 if d["structure"] == "down" else 0.0))
        return {tf: round(sum(v) / len(v), 2) for tf, v in acc.items() if v}

    def _plan(self, verdict, px, atr1h, levels, risk, by_name, score) -> dict:
        """Build the trade plan. Every level is tagged pivot / fib / projection."""
        plan = {"direction": verdict, "entry": None, "stop": None,
                "targets": [], "levels_note": None, "warnings": []}
        if risk:
            plan["warnings"] = risk.payload.get("warnings", [])
        if verdict == "no-trade" or px is None:
            if risk and not risk.payload.get("allowed", True):
                why = "; ".join(risk.payload.get("blockers", [])) or "risk gate"
                plan["levels_note"] = f"No trade: the risk gate blocked it ({why})."
            else:
                plan["levels_note"] = (
                    f"No trade: net bias {score:+.3f} is inside the neutral "
                    f"band (±{self.settings.neutral_band:g}).")
            return plan
        sup = (levels.payload.get("supports") if levels else []) or []
        res = (levels.payload.get("resistances") if levels else []) or []
        stop_dist = atr1h * 1.5 if atr1h else (px * 0.01)
        if verdict == "long":
            entry = px
            stop = round((sup[0]["level"] if sup else px - stop_dist) - (atr1h or 0) * 0.3, 2)
            plan["entry"] = round(entry, 2)
            plan["stop"] = stop
            plan["targets"] = _pick_targets(res, entry, stop, self.settings.min_rr, above=True)
            if not plan["targets"] and not res:
                # No structure on this side at all: a projection is the only
                # honest target, and it is labelled as the weakest kind.
                plan["targets"] = [{"level": round(px + stop_dist * 2, 2), "kind": "projection"}]
        else:
            entry = px
            stop = round((res[0]["level"] if res else px + stop_dist) + (atr1h or 0) * 0.3, 2)
            plan["entry"] = round(entry, 2)
            plan["stop"] = stop
            plan["targets"] = _pick_targets(sup, entry, stop, self.settings.min_rr, above=False)
            if not plan["targets"] and not sup:
                plan["targets"] = [{"level": round(px - stop_dist * 2, 2), "kind": "projection"}]
        risk_dist = abs(plan["entry"] - plan["stop"])
        if not plan["targets"]:
            plan["rr"] = None
            plan["entry"] = plan["stop"] = None
            plan["levels_note"] = (
                f"No trade: no level clears the {self.settings.min_rr:g} "
                f"reward:risk minimum — the stop is wider than the reward.")
            return plan
        if risk_dist:
            best = plan["targets"][0]
            plan["rr"] = round(abs(best["level"] - plan["entry"]) / risk_dist, 2)
        plan["levels_note"] = "pivot = printed by the market; fib = measured leg; projection = one stop beyond."
        return plan

    # ---- the run -------------------------------------------------------
    def run(self) -> Run:
        run_id = uuid.uuid4().hex[:12]
        started = time.time()
        gather = self.gather()
        studies, errors = self.dispatch(run_id)
        errors = list(gather["errors"]) + errors
        verdict, score, rec = self.synthesise(studies)
        rec["gather"] = gather
        rec["errors"] = errors
        rec["llm"] = self.llm.stats()
        duration = time.time() - started
        run = Run(run_id, self.settings.symbol, started, studies, verdict, score,
                  rec, errors, duration)
        self.shared.save_run(run_id, self.settings.symbol, started, time.time(),
                             verdict, score, rec)
        return run

    # ---- the watch loop -------------------------------------------------
    def actionable(self, run: Run) -> tuple[bool, str]:
        """Is this run worth alerting? Only a live, non-neutral call clears the
        bar. A `no-trade` run is the desk working, not news."""
        if run.verdict not in ("long", "short"):
            return False, "no directional call"
        if not run.recommendation.get("allowed", True):
            return False, "risk gate blocked it"
        if abs(run.score) < self.settings.alert_score:
            return False, f"conviction {run.score:+.2f} below {self.settings.alert_score}"
        fp = f"{run.symbol}:{run.verdict}:{round(run.score, 1)}"
        return True, fp

    def watch_once(self, notifier=None) -> dict:
        """One scheduled pass: run the desk, and alert only if the signal is
        actionable and not already sent. Returns a small dict for the caller."""
        from .notify import Notifier, SignalLedger

        run = self.run()
        ok, detail = self.actionable(run)
        out = {"run_id": run.run_id, "verdict": run.verdict,
               "score": round(run.score, 3), "alerted": False, "reason": detail}
        if not ok:
            return out

        ledger = SignalLedger(self.settings.signal_ledger)
        if ledger.seen(detail):
            out["reason"] = "already alerted"
            return out
        n = notifier or Notifier(self.settings)
        from .report import render
        sent = n.send(render(run, lang="ar"))
        if sent:
            ledger.record(detail, run.run_id)
        out["alerted"] = sent
        out["reason"] = "alerted" if sent else "alert not configured/failed"
        return out
