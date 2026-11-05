"""The active plan: one recommendation, followed until it ends.

The desk issues a call and then goes quiet, because a *new* signal is news and
the same signal is not. But a call is a live thing: it waits for its trigger, it
fills, it moves, it reaches its target or its stop. Someone holding that call
does not want to be told the direction again every ten minutes -- they want to
be told the moment the situation changes, and to be reassured in between that it
is still running.

So this module remembers the ONE plan currently in play and reports only the
transitions:

    waiting      the trigger has not fired yet
    on_track     filled, and price is between the entry and the target
    target       the first target was reached
    stopped      the stop was hit
    invalidated  the setup died before filling, or the plan got too old

A status change is always sent. An unchanged `on_track` is re-sent only after a
heartbeat interval, so "still running" is a periodic reassurance rather than a
flood. The plan is cleared once it reaches a terminal state -- a finished plan
must not be followed forever.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

TERMINAL = ("target", "stopped", "invalidated")


@dataclass
class ActivePlan:
    direction: str          # "long" | "short"
    entry: float            # the trigger that fills the plan
    stop: float
    target: float           # the first target -- what the plan is "heading to"
    run_id: str
    issued_at: float
    status: str = "waiting"
    updated_at: float = 0.0
    heartbeat_at: float = 0.0

    # ---- lifecycle -----------------------------------------------------
    def evaluate(self, live: float, *, now: float | None = None,
                 expiry_s: float | None = None) -> str:
        """The status the live price implies. Pure -- it does not mutate.

        A breakout plan fills when price crosses the trigger. Before that the
        plan is only `waiting`, so price reaching the stop beforehand is not a
        stop-out -- nothing was ever filled. That is a dead setup, reported as
        `invalidated`, not as a loss the trader never took.

        A plan that sits `waiting` past `expiry_s` is abandoned: a trigger that
        never fires is a level the market has moved away from, and following it
        forever would be watching a trade nobody can take.
        """
        now = time.time() if now is None else now
        long = self.direction == "long"
        if self.status == "waiting" and expiry_s and (now - self.issued_at) > expiry_s:
            return "invalidated"
        if long:
            if self.status == "waiting":
                if live >= self.entry:
                    return "on_track"
                if live <= self.stop:
                    return "invalidated"
                return "waiting"
            if live >= self.target:
                return "target"
            if live <= self.stop:
                return "stopped"
            return "on_track"
        # short
        if self.status == "waiting":
            if live <= self.entry:
                return "on_track"
            if live >= self.stop:
                return "invalidated"
            return "waiting"
        if live <= self.target:
            return "target"
        if live >= self.stop:
            return "stopped"
        return "on_track"

    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    def progress(self, live: float) -> float:
        """How far from the entry to the target the price has travelled, 0..1.

        A readout, never a decision input: it says "half way there", not "hold".
        """
        span = self.target - self.entry
        if not span:
            return 0.0
        return max(0.0, min(1.0, (live - self.entry) / span))


# ---- persistence -------------------------------------------------------

def _path(settings) -> Path:
    return Path(settings.home) / "active_plan.json"


def load(settings) -> ActivePlan | None:
    p = _path(settings)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    try:
        return ActivePlan(**d)
    except TypeError:
        return None


def save(settings, plan: ActivePlan) -> None:
    p = _path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(plan), indent=2))
    tmp.replace(p)


def clear(settings) -> None:
    p = _path(settings)
    try:
        p.unlink()
    except FileNotFoundError:
        pass


def adopt(settings, rec: dict, run_id: str, *, now: float | None = None) -> ActivePlan | None:
    """Start following a directional recommendation, replacing any old plan.

    Returns None when the recommendation is not a tradeable plan -- no
    direction, no entry, no stop, no target. The desk says "no trade" far more
    often than not, and that is not a plan to follow.
    """
    now = time.time() if now is None else now
    direction = rec.get("direction")
    entry, stop = rec.get("entry"), rec.get("stop")
    targets = rec.get("targets") or []
    target = None
    if targets:
        first = targets[0]
        target = first.get("level") if isinstance(first, dict) else first
    if direction not in ("long", "short") or not entry or not stop or target is None:
        return None
    plan = ActivePlan(direction=direction, entry=float(entry), stop=float(stop),
                      target=float(target), run_id=run_id, issued_at=now,
                      status="waiting", updated_at=now, heartbeat_at=now)
    save(settings, plan)
    return plan


# ---- messages ----------------------------------------------------------

_DIR = {"long": "شراء", "short": "بيع"}


def message(plan: ActivePlan, live: float) -> str:
    """The status card, in the owner's language, for the status it now holds."""
    d = _DIR.get(plan.direction, plan.direction)
    head = f"📌 <b>متابعة التوصية</b> — {d}\n" \
           f"دخول {plan.entry:,.2f} · وقف {plan.stop:,.2f} · هدف {plan.target:,.2f}\n" \
           f"السعر الآن <b>{live:,.2f}</b>"
    if plan.status == "waiting":
        side = "فوق" if plan.direction == "long" else "تحت"
        return (f"{head}\n⏳ <b>لسا ما تفعّلت</b> — بتنتظر إغلاق {side} "
                f"{plan.entry:,.2f}.")
    if plan.status == "on_track":
        pct = plan.progress(live) * 100
        return (f"{head}\n🟢 <b>الصفقة مكملة — رايحة عالهدف.</b> "
                f"قطعنا {pct:.0f}% من الطريق. لسا ما في شي بيغيّر الخطة.")
    if plan.status == "target":
        return (f"{head}\n🎯 <b>الهدف الأول تحقق</b> ({plan.target:,.2f}). "
                f"الخطة انتهت بنجاح — سجّل الربح.")
    if plan.status == "stopped":
        return (f"{head}\n🛑 <b>الوقف ضُرب</b> ({plan.stop:,.2f}). "
                f"الخطة انتهت — الخسارة محسوبة ومحدودة.")
    return (f"{head}\n⚪ <b>الخطة انتهت</b> — ما تفعّلت قبل ما يموت السيتاب. "
            f"ما في صفقة مفتوحة.")


def contradiction_message(plan: ActivePlan, live: float, fresh_verdict: str) -> str:
    """The plan has not filled and the desk's own fresh read now points the other
    way. Say so plainly, and stop following a call the desk no longer backs."""
    d = _DIR.get(plan.direction, plan.direction)
    fresh = _DIR.get(fresh_verdict, fresh_verdict)
    return (f"📌 <b>تحديث على التوصية</b> — {d}\n"
            f"دخول {plan.entry:,.2f} · وقف {plan.stop:,.2f} · هدف {plan.target:,.2f}\n"
            f"السعر الآن <b>{live:,.2f}</b>\n"
            f"⚠️ <b>تغيّر الوضع:</b> المكتب صار يميل لـ<b>{fresh}</b> قبل ما تتفعّل "
            f"خطة {d}. ألغينا متابعتها — رح ننطر إشارة جديدة واضحة.")


def one_line(plan: ActivePlan, live: float) -> str:
    """A single line for the desk card, so a reader knows a plan is in play."""
    d = _DIR.get(plan.direction, plan.direction)
    tag = {"waiting": "بانتظار التفعيل", "on_track": "ماشية عالهدف",
           "target": "وصلت الهدف", "stopped": "ضُرب الوقف",
           "invalidated": "انتهت بلا تفعيل"}.get(plan.status, plan.status)
    return (f"  خطة نشطة: {d} · دخول {plan.entry:,.2f} · وقف {plan.stop:,.2f} · "
            f"هدف {plan.target:,.2f} · {tag}")


def sizing_line(plan: ActivePlan, settings) -> str | None:
    """The money view of the plan in play, for the account the desk was given.

    Returns None when no balance is configured, so a keyless desk still prints
    levels and nothing more. The size is computed from the plan's own entry and
    stop, so what the reader sees is what this plan costs them.
    """
    if not getattr(settings, "balance", 0):
        return None
    from .account import size_plan

    sz = size_plan(balance=settings.balance, risk_pct=settings.risk_pct,
                   entry=plan.entry, stop=plan.stop, target=plan.target,
                   contract_size=settings.contract_size,
                   lot_step=settings.lot_step, spread=settings.spread,
                   min_stop_points=getattr(settings, "min_stop_points", 0.0),
                   max_stop_points=getattr(settings, "max_stop_points", 0.0))
    if not sz.feasible:
        return (f"  💵 الحساب ${settings.balance:,.0f}: 🚫 الوقف واسع — أصغر حجم "
                f"({sz.min_lots:g}) يخاطر ${sz.min_risk_money:,.2f} "
                f"({sz.min_risk_pct:.1f}%)")
    return (f"  💵 الحساب ${settings.balance:,.0f} · {sz.lots:g} لوت · "
            f"خسارة الوقف ${sz.risk_money:,.2f} ({sz.risk_pct_actual:.1f}%) · "
            f"ربح الهدف ${sz.reward_money:,.2f}")
