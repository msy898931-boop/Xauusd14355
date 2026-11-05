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
import datetime as dt
import time
import uuid
from dataclasses import dataclass, field

from . import sources
from .agents import ROSTER, Study
from .config import Settings
from .llm import LLM
from .memory import SharedMemory
from .session import Session, market_session


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
        return [{"level": lvl["level"], "kind": lvl["kind"]} for lvl in levels[:3]]
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
            if s.kind in ("risk", "volatility"):
                # Neither is a directional vote: the gate vetoes, and volatility
                # only sets the regime. Tallying either as a direction is how a
                # non-directional agent quietly becomes a permanent bias.
                continue
            w = next((a.weight for a in self.agents if a.name == s.agent), 1.0)
            weighted += s.score * w * (0.5 + 0.5 * s.confidence)
            wsum += w
        norm = max(-1.0, min(1.0, weighted / wsum)) if wsum else 0.0

        levels = by_name.get("levels")
        px = (levels.payload.get("price") if levels else None) or (self.shared.load_quote(self.settings.symbol) or {}).get("mid")
        vol = by_name.get("volatility")
        atr1h, atr_frame = self._atr_for_stop(vol)

        if not allowed:
            verdict = "no-trade"
        elif norm > self.settings.neutral_band:
            verdict = "long"
        elif norm < -self.settings.neutral_band:
            verdict = "short"
        else:
            verdict = "no-trade"

        rec = self._plan(verdict, px, atr1h, levels, risk, by_name, norm)
        rec["atr_frame"] = atr_frame
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

    @staticmethod
    def _atr_for_stop(vol) -> tuple[float | None, str | None]:
        """The ATR that sizes the stop, and which frame it came from.

        This used to read `frames["1h"]` and nothing else. When the 1h frame is
        absent -- a daily-only backtest, or an agent that was restricted to a
        coarser frame -- that returned None and the stop fell back to 1% of
        price *silently*. A made-up risk is worse than no trade, so the frame is
        now chosen from what the volatility agent actually reported, and the
        caller is told which one it was.
        """
        frames = ((vol.payload.get("frames") if vol else {}) or {})
        for tf in ("1h", "4h", "1d", "30m", "15m", "5m", "1m"):
            a = (frames.get(tf) or {}).get("atr")
            if isinstance(a, (int, float)) and a > 0:
                return float(a), tf
        return None, None

    @staticmethod
    def _vol_multiplier(vol) -> tuple[float, float | None]:
        """Stop widening from participation, and the ratio it came from.

        Measured on this desk's own 1h history: a bar whose activity is above
        1.2x its 20-bar average is followed by a next-bar range 1.55x wider than
        after a quiet bar (t=3.96; bootstrap 95% CI [1.25, 1.91]), and the effect
        survives controlling for ATR (partial r=+0.39, vs r=+0.13 for ATR alone).
        So a hot bar says "expect a wider next bar" -- information a stop should
        use. It is NOT directional: the same history showed no continuation edge
        (n=8), so this must never become a vote.

        The adjustment is one-sided -- it can only widen. A quiet bar is the
        absence of the signal, not evidence for a tighter stop, and tightening
        below the ATR floor would put the stop inside noise, which the desk
        already refuses to do. An unreadable ratio returns 1.0, no adjustment.
        """
        frames = ((vol.payload.get("frames") if vol else {}) or {})
        r = (frames.get("1h") or {}).get("vol_ratio")
        if not isinstance(r, (int, float)):
            return 1.0, None
        mult = 1.15 if r >= 1.8 else 1.0 if r < 1.2 else 1.1
        return mult, float(r)

    def _plan(self, verdict, px, atr1h, levels, risk, by_name, score) -> dict:
        """Build the trade plan. Every level is tagged pivot / fib / projection."""
        plan = {"direction": verdict, "entry": None, "stop": None,
                "targets": [], "levels_note": None, "warnings": []}
        # Participation is a fact about the last bar, not about the verdict, so
        # it is read before the no-trade return: a stand-down that hides the
        # volume reading would leave the one number the stop sizing used unseen.
        vol_mult, vol_ratio = self._vol_multiplier(by_name.get("volatility"))
        plan["vol_mult"] = vol_mult
        plan["vol_ratio"] = round(vol_ratio, 2) if vol_ratio is not None else None
        if risk:
            plan["warnings"] = list(risk.payload.get("warnings", []))
        if atr1h is None and verdict in ("long", "short"):
            plan["warnings"].append(
                "ما وصل ATR، فالوقف 1% من السعر وليس مدى مقيساً — "
                "اعتبر المخاطرة/المكافأة إرشادية فقط.")
        if verdict == "no-trade" or px is None:
            if risk and not risk.payload.get("allowed", True):
                why = "; ".join(risk.payload.get("blockers", [])) or "بوابة المخاطر"
                plan["levels_note"] = f"لا صفقة: بوابة المخاطر أوقفتها ({why})."
            else:
                plan["levels_note"] = (
                    f"لا صفقة: الميل الصافي {score:+.3f} داخل النطاق المحايد "
                    f"(±{self.settings.neutral_band:g}).")
            return plan
        sup = (levels.payload.get("supports") if levels else []) or []
        res = (levels.payload.get("resistances") if levels else []) or []
        stop_dist = atr1h * 1.5 if atr1h else (px * 0.01)
        # Participation widens the stop when the last bar was hot: a measured
        # (t=3.96) 1.55x wider next-bar range after a high-activity bar. It is a
        # multiplier on the structural stop below, so a quiet market keeps the
        # structural level and a hot one is not stopped out by its own noise.
        stop_dist *= vol_mult
        if vol_mult != 1.0:
            plan["warnings"].append(
                f"نشاط آخر شمعة ساعة {vol_ratio:.2f}× المتوسط — وسّعنا الوقف "
                f"{vol_mult:.2f}× لأن المدى التالي أوسع عادةً.")
        # The nearest structural level is almost always too close to be a real
        # stop -- the same mistake as picking a target by the nearest level. A
        # stop that sits inside one ATR is noise, not protection, and it makes
        # the R:R look better than the trade is. So clear the level by half an
        # ATR and floor the risk at one ATR, whatever the structure says.
        atr = (atr1h or px * 0.01) * vol_mult
        if verdict == "long":
            entry = px
            struct = sup[0]["level"] if sup else px - stop_dist
            stop = round(min(struct - atr * 0.5, px - atr), 2)
            plan["entry"] = round(entry, 2)
            plan["stop"] = stop
            plan["targets"] = _pick_targets(res, entry, stop, self.settings.min_rr, above=True)
            if not plan["targets"] and not res:
                # No structure on this side at all: a projection is the only
                # honest target, and it is labelled as the weakest kind.
                plan["targets"] = [{"level": round(px + stop_dist * 2, 2), "kind": "projection"}]
        else:
            entry = px
            struct = res[0]["level"] if res else px + stop_dist
            stop = round(max(struct + atr * 0.5, px + atr), 2)
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
                f"لا صفقة: ولا مستوى يحقّق الحد الأدنى للمخاطرة/المكافأة "
                f"{self.settings.min_rr:g} — الوقف أوسع من المكافأة.")
            return plan
        if risk_dist:
            best = plan["targets"][0]
            plan["rr"] = round(abs(best["level"] - plan["entry"]) / risk_dist, 2)
        plan["levels_note"] = ("محور = طبعه السوق؛ فيبوناتشي = ضلع مقيس؛ "
                               "تقديري = وقف واحد بعده.")
        return plan

    # ---- the run -------------------------------------------------------
    def session_gate(self, now: dt.datetime | None = None) -> Session | None:
        """The session, or None when the desk is set to ignore it.

        A closed market is decided *before* any network call: the point is to not
        spend the run at all, and gathering first would spend it and then throw
        the result away. `None` means "no opinion, carry on".
        """
        if not self.settings.respect_session:
            return None
        return market_session(now)

    def _stand_down(self, run_id: str, started: float, session: Session) -> Run:
        """A run that never happened, recorded so the schedule stays visible.

        It is written as `no-trade` with the session as the reason, not skipped
        silently: a silent skip is indistinguishable from a broken workflow.
        """
        rec: dict = {"direction": "no-trade", "entry": None, "stop": None,
                     "targets": [], "warnings": [],
                     "levels_note": f"لا قراءة: {session.reason}.",
                     "session": {"open": False, "reason": session.reason,
                                 "opens_at": (session.opens_at.isoformat()
                                              if session.opens_at else None)},
                     "gather": {"symbol": self.settings.symbol, "candles": {},
                                "quote": None, "errors": []},
                     "errors": [], "llm": self.llm.stats(),
                     "allowed": False, "score": 0.0, "weights_sum": 0.0,
                     "bias": {"direction": "flat", "score": 0.0},
                     "per_frame_bias": {}}
        duration = time.time() - started
        run = Run(run_id, self.settings.symbol, started, [], "no-trade", 0.0,
                  rec, [], duration)
        self.shared.save_run(run_id, self.settings.symbol, started, time.time(),
                             "no-trade", 0.0, rec)
        return run

    def run(self, now: dt.datetime | None = None) -> Run:
        run_id = uuid.uuid4().hex[:12]
        started = time.time()
        session = self.session_gate(now)
        if session is not None and not session.is_open:
            return self._stand_down(run_id, started, session)
        gather = self.gather()
        studies, errors = self.dispatch(run_id)
        errors = list(gather["errors"]) + errors
        verdict, score, rec = self.synthesise(studies)
        rec["gather"] = gather
        rec["errors"] = errors
        rec["llm"] = self.llm.stats()
        # A plan in play is part of the picture: a reader must know the desk is
        # already following a call before they read a fresh "no trade".
        from . import active as _active
        _plan = _active.load(self.settings)
        _live = self._live_price_from_rec(rec)
        if _plan is not None and _live is not None:
            rec["active_plan_line"] = _active.one_line(_plan, _live)
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
            sess = (run.recommendation or {}).get("session") or {}
            if sess and not sess.get("open", True):
                return False, sess.get("reason") or "السوق مقفول"
            return False, "no directional call"
        if not run.recommendation.get("allowed", True):
            return False, "بوابة المخاطر أوقفتها"
        fresh, why = self.data_fresh()
        if not fresh:
            return False, why
        if abs(run.score) < self.settings.alert_score:
            return False, f"conviction {run.score:+.2f} below {self.settings.alert_score}"
        # The fingerprint names the TRADE, not the opinion. Keying it on
        # direction+score alone meant a plan that completed could suppress the
        # *next* trade of the same direction and conviction forever. Including
        # the trigger means a genuinely new setup alerts, while the same one
        # re-printed on the next pass does not.
        entry = run.recommendation.get("entry")
        fp = (f"{run.symbol}:{run.verdict}:{round(run.score, 1)}"
              f":{round(float(entry), 2) if entry is not None else 'x'}")
        return True, fp

    def data_fresh(self) -> tuple[bool, str]:
        """Is the newest reading recent enough to act on?

        A directional call computed from a feed that stopped hours ago is a stale
        number wearing a signal's clothes -- the desk would alert on a price the
        market left behind. The feed's own staleness flag catches a slow quote;
        this catches the case where the whole source is down and the run silently
        proceeds on whatever the database already held. Age is measured from
        `fetched_at`, so a market that is merely quiet is not mistaken for a dead
        feed (the fetch still stamps the time).
        """
        if self.settings.max_data_age_s <= 0:
            return True, ""
        now = time.time()
        ages: list[tuple[str, float]] = []
        q = self.shared.load_quote(self.settings.symbol)
        if q and q.get("fetched_at"):
            ages.append(("quote", now - q["fetched_at"]))
        for tf in ("1h", "15m"):
            rows = self.shared.load_candles(self.settings.symbol, tf)
            if rows and rows[-1].get("fetched_at"):
                ages.append((tf, now - rows[-1]["fetched_at"]))
        if not ages:
            # No timestamp to judge: freshness is unprovable, so the gate does not
            # fire. A run with no data at all is already `no-trade` by construction.
            return True, ""
        label, age = min(ages, key=lambda x: x[1])
        if age > self.settings.max_data_age_s:
            return False, f"بيانات قديمة ({label}: {age / 3600:.1f} ساعة)"
        return True, ""

    def watch_once(self, notifier=None) -> dict:
        """One scheduled pass: run the desk, grade what is now gradeable, and
        alert only if the signal is actionable and not already sent.

        Grading runs HERE, on every pass, not only when someone presses the
        hit-rate button. The scoreboard is the desk's only check on itself; if it
        is written only on demand then a busy week leaves the record empty, and an
        empty record cannot tell a good call from a lucky one. It reads candles
        already in shared memory, so it costs no network call.

        The alert answers a different question than "is there a new signal": a
        plan, once issued, is *followed*. The status of the active plan is
        reported on every pass -- but only when it changes, or on the heartbeat,
        so the owner hears "still running" rather than the same direction again.
        """
        from . import active
        from .notify import Notifier, SignalLedger
        from .scoreboard import Grader

        run = self.run()
        try:
            board = Grader(self.shared).grade()
            graded: dict[str, object] = {
                "runs_graded": board.runs_graded,
                "plans_graded": board.plans_graded,
                "expectancy_r": (round(board.expectancy_r, 3)
                                 if board.expectancy_r is not None else None)}
        except Exception as exc:  # noqa: BLE001 - grading must never block an alert
            graded = {"error": exc.__class__.__name__}
        out: dict[str, object] = {"run_id": run.run_id, "verdict": run.verdict,
                                  "score": round(run.score, 3), "alerted": False,
                                  "reason": "", "graded": graded}
        n = notifier or Notifier(self.settings)
        live = self._live_price(run)

        # 1) Follow the plan already in play. This runs before the new-signal
        # check: a running plan is the thing the owner is holding.
        tracked = self._follow_active_plan(live, n, run)
        if tracked is not None:
            out.update(tracked)
            return out

        # 2) No active plan -- consider issuing a new one.
        ok, detail = self.actionable(run)
        out["reason"] = detail
        if not ok:
            return out

        ledger = SignalLedger(self.settings.signal_ledger)
        if ledger.seen(detail):
            out["reason"] = "already alerted"
            return out
        from .report import render
        sent = n.send(render(run, lang="ar"))
        if sent:
            ledger.record(detail, run.run_id)
            plan = active.adopt(self.settings, run.recommendation, run.run_id)
            if plan is not None:
                out["active"] = plan.status
        out["alerted"] = sent
        out["reason"] = "alerted" if sent else "alert not configured/failed"
        return out

    def _live_price(self, run: Run) -> float | None:
        """The price to judge the active plan against: the run's own quote when
        it has one, else the last quote in shared memory."""
        return self._live_price_from_rec(run.recommendation)

    def _live_price_from_rec(self, rec: dict) -> float | None:
        q = (rec.get("gather") or {}).get("quote") or {}
        if isinstance(q.get("mid"), (int, float)):
            return float(q["mid"])
        row = self.shared.load_quote(self.settings.symbol) or {}
        if isinstance(row.get("mid"), (int, float)):
            return float(row["mid"])
        return None

    def _follow_active_plan(self, live: float | None, notifier, run: Run) -> dict | None:
        """Report the active plan's status. Returns None when there is no plan.

        Only a CHANGE is sent, plus a heartbeat so a quiet-but-alive plan is not
        mistaken for a dead desk. A terminal status is sent once and then the
        plan is cleared, so a finished call is not followed forever.

        A second kind of change matters while the plan is still `waiting`: the
        desk's own fresh read may have flipped against it. A breakout that has
        not fired, on a day the desk now leans the other way, is a setup going
        cold -- the owner is told, and the stale plan is dropped.
        """
        from . import active

        plan = active.load(self.settings)
        if plan is None or live is None:
            return None
        now = time.time()
        new = plan.evaluate(live, now=now, expiry_s=self.settings.plan_expiry_s)
        changed = new != plan.status
        due = (now - plan.heartbeat_at) >= self.settings.plan_heartbeat_s
        flipped = (new == "waiting" and self._plan_contradicted(plan, run))
        plan.status = new
        plan.updated_at = now
        if changed or flipped or (new == "on_track" and due):
            if flipped:
                text = active.contradiction_message(plan, live, run.verdict)
            else:
                text = active.message(plan, live)
            sent = notifier.send(text)
            if sent:
                plan.heartbeat_at = now
                if plan.is_terminal() or flipped:
                    active.clear(self.settings)
                else:
                    active.save(self.settings, plan)
            reason = f"plan {new}"
            if flipped:
                reason = f"plan {new} (contradicted by fresh {run.verdict})"
            return {"active": new, "alerted": sent,
                    "reason": reason if sent else reason + " (send failed)"}
        if plan.is_terminal():
            active.clear(self.settings)
        return {"active": new, "alerted": False, "reason": f"plan {new} (no change)"}

    @staticmethod
    def _plan_contradicted(plan, run: Run) -> bool:
        """Whether the desk's fresh verdict is against an unfilled plan.

        Only a decisive opposite call counts: `no-trade` is silence, not a
        reversal, and treating it as one would abandon a good plan on a quiet
        day. The plan's own direction must still be the one the desk backs.
        """
        return run.verdict in ("long", "short") and run.verdict != plan.direction
