"""Watch specific price levels and alert once when each is crossed.

The desk alerts on its own verdict. This alerts on the levels a plan is built
around, which is a different question: the orchestrator can say "no trade" for
weeks while the price sits between a trigger and a stop, and the trader still
wants to know the moment one side is decided.

A level fires once. Crossing back and forth is noise, not two signals, so the
ledger keys on the level and the side it broke -- the same discipline the desk
uses for its own alerts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Level:
    price: float
    label: str
    side: str          # "above" or "below": the side that counts as a break
    note: str
    # The trade this break calls for, when there is one. A level that only says
    # "price broke 4,145" leaves the reader to invent the stop, the target and
    # the reason; the owner asked to be told what to do and why, so the call
    # travels with the level. Empty for a pure marker (e.g. a stop line).
    call_side: str = ""       # "long" | "short" | ""
    stop: float | None = None
    target: float | None = None
    why: str = ""

    def trade_call(self) -> dict | None:
        """The actionable trade, or None when the level is only a marker."""
        if self.call_side not in ("long", "short") or self.stop is None:
            return None
        return {"direction": self.call_side, "entry": self.price,
                "stop": self.stop, "target": self.target, "why": self.why}


# The levels the live plan is built around. Edit this list as the plan changes;
# nothing else needs to move.
#
# Refreshed 2026-11-03 (price 3,961). The previous set was written when gold sat
# near 3,900 and had been stale for days: every entry was *below* the market, so
# `triggered` fired on all of them at once and the alerts were noise. Levels are
# a statement about the current market, so they must be re-stated when it moves.
#
# This list is the FALLBACK. A run that has already printed its own triggers
# watches those instead -- see `from_recommendation`. A hand-typed list goes
# stale the moment the desk changes its mind, and an alert that watches yesterday
# plan is an alert for a trade nobody is in.
DEFAULT_LEVELS = (
    Level(3970.5, "الخط الفاصل", "above",
          "إغلاق فوقه = الصعود تأكد — الطريق لـ4,030 ثم 4,083"),
    Level(3964.6, "مقاومة الساعة", "above",
          "أول مقاومة — فوقها يفتح الباب للخط الفاصل"),
    Level(3937.5, "دعم الساعة", "below",
          "كسره = الهبوط يتفعّل — الطريق لـ3,895"),
    Level(3895.0, "قاع الساعة", "below",
          "تحته الطريق مفتوح لـ3,870 (القاع الكبير)"),
    Level(3870.0, "القاع الكبير", "below",
          "كسر القاع الكبير — الطريق لـ3,850 ثم 3,820"),
)


def triggered(live: float, levels=DEFAULT_LEVELS) -> list[Level]:
    """The levels the live price is currently on the breaking side of."""
    out = []
    for lv in levels:
        if lv.side == "above" and live >= lv.price:
            out.append(lv)
        elif lv.side == "below" and live <= lv.price:
            out.append(lv)
    return out


def approaching(live: float, levels=DEFAULT_LEVELS, *,
                within: float = 12.0) -> list[tuple[Level, float]]:
    """Levels the price is near but has NOT broken yet, nearest first.

    `triggered` answers "which side is decided". This answers the earlier
    question -- "which one is about to be decided" -- which is the one a trader
    actually wants a warning about. A level already crossed is excluded: that is
    `triggered`'s job, and reporting it here would double up.

    `within` is in price units, so it must be set to the instrument's noise. On
    gold a 12-point band is roughly a fifth of a daily ATR: close enough to be
    worth a look, far enough not to fire on every tick.
    """
    out: list[tuple[Level, float]] = []
    for lv in levels:
        dist = abs(live - lv.price)
        if dist > within:
            continue
        crossed = (lv.side == "above" and live >= lv.price) or \
                  (lv.side == "below" and live <= lv.price)
        if not crossed:
            out.append((lv, dist))
    return sorted(out, key=lambda t: t[1])


def fingerprint(lv: Level) -> str:
    return f"level:{lv.price:g}:{lv.side}"


def near_fingerprint(lv: Level) -> str:
    """A separate ledger key, so "near" and "broken" are two signals, not one.

    The level fires twice in a real move: once when price comes close and once
    when it breaks. Keying them apart is what lets both be sent once, and stops
    a re-approach from re-firing.
    """
    return f"near:{lv.price:g}:{lv.side}"


def message(lv: Level, live: float) -> str:
    arrow = "▲" if lv.side == "above" else "▼"
    verb = "اخترق" if lv.side == "above" else "كسر"
    head = (f"{arrow} <b>تنبيه مستوى</b>\n"
            f"السعر {live:,.2f} {verb} <b>{lv.price:,.2f}</b> ({lv.label})\n"
            f"{lv.note}")
    call = lv.trade_call()
    if not call:
        return head
    return head + "\n\n" + call_text(call, live)


def call_text(call: dict, live: float) -> str:
    """The instruction: do what, at what, with which stop and target, and why.

    The alert used to say only that a level broke, leaving the reader to invent
    the stop, the target and the reason. This says it. Risk is stated in money
    for the account the desk is sized for, so "10 points" is never mistaken for
    "small".
    """
    long = call["direction"] == "long"
    stop, target = call.get("stop"), call.get("target")
    action = "🟢 <b>اشترِ</b>" if long else "🔴 <b>بِع</b>"
    lines = [f"{action} — السعر {live:,.2f} تجاوز {call['entry']:,.2f}"]
    if stop is not None:
        pts = abs(live - stop)
        lines.append(f"      الوقف <b>{stop:,.2f}</b> ({pts:.0f} نقطة)")
    if target is not None:
        pts = abs(target - live)
        rr = (abs(target - live) / abs(live - stop)) if stop and live != stop else None
        tail = f" · R:R {rr:.2f}" if rr else ""
        lines.append(f"      الهدف <b>{target:,.2f}</b> ({pts:.0f} نقطة{tail})")
    if call.get("why"):
        lines.append(f"      <i>{call['why']}</i>")
    return "\n".join(lines)


def near_message(lv: Level, live: float, dist: float) -> str:
    """The warning, phrased as a warning -- it is not a break yet."""
    arrow = "▲" if lv.side == "above" else "▼"
    side = "فوقه" if lv.side == "above" else "تحته"
    return (f"🔔 <b>اقتراب من مستوى</b>\n"
            f"السعر {live:,.2f} يقترب من {arrow} <b>{lv.price:,.2f}</b> ({lv.label})\n"
            f"باقي {dist:,.1f} نقطة، والكسر يكون {side}.\n"
            f"<i>لسا ما انكسر — هذا تحذير مبكر.</i>")


def from_recommendation(rec: dict, live: float) -> tuple[Level, ...]:
    """The levels the desk's own last plan is built around, as watchable levels.

    This is the fix for the gap that made the alerts useless: the alert watched a
    hand-typed list while the recommendation named its own entry, stop and
    targets. The two drifted -- a run would say "break below 3,919.49" while the
    alert watched 3,937.50, so the level the plan actually hinged on had no alert
    at all. The trade the desk described could not be entered by anyone reading
    its own alerts.

    So the triggers come from the plan itself:
      * the entry is a level, on whichever side the plan wants to be filled;
      * the first target is a level, on the side that pays;
      * the stop is a level, on the side that ends it -- the one line the trader
        must not miss.
    Returns () when there is nothing usable, so the caller can fall back rather
    than alert on nothing.
    """
    entry = rec.get("entry")
    stop = rec.get("stop")
    targets = rec.get("targets") or []
    direction = rec.get("direction")
    if direction not in ("long", "short") or not entry or not stop:
        return ()
    target = targets[0].get("level") if targets and isinstance(targets[0], dict) \
        else (targets[0] if targets else None)
    long = direction == "long"
    why = f"توصية المكتب · R:R {rec.get('rr')}" if rec.get("rr") else "توصية المكتب"
    out: list[Level] = [
        Level(float(entry), "دخول التوصية", "above" if long else "below",
              f"{'شراء' if long else 'بيع'} من هنا — هاد مستوى الدخول",
              call_side=direction, stop=float(stop),
              target=float(target) if target is not None else None, why=why),
        Level(float(stop), "وقف التوصية", "below" if long else "above",
              "ضُرب الوقف هنا — الخطة انتهت"),
    ]
    if target is not None:
        out.append(Level(float(target), "هدف التوصية", "above" if long else "below",
                         "الهدف الأول — أول مستوى بيسحب الربح"))
    return tuple(out)


def from_scenarios(settings, live: float, *, symbol: str | None = None) -> tuple[Level, ...]:
    """The triggers the desk's own scenario set is built around, as watchable
    levels.

    `from_recommendation` watches the PLAN -- entry, stop, first target. This
    watches the SCENARIOS, which answer the next question: "if price does X,
    what then?". They are different prices. A desk that says "no trade" still
    prints scenarios, so a plan-only watcher goes silent exactly when the market
    is deciding something -- the moment the trader most wants to hear.

    The levels are read from what a run already stored, and the build is
    read-only over shared memory (no agent, no model, no network beyond the
    quote the caller already fetched). Measured at ~5 ms, so a check every few
    seconds is free.

    Returns () when there is nothing to build from, so the caller falls back
    rather than watching nothing.
    """
    from . import scenarios as S
    from .memory import SharedMemory

    symbol = symbol or settings.symbol
    shared = SharedMemory(settings.shared_db)
    frames: dict[str, list[dict]] = {}
    for tf in settings.timeframes:
        rows = shared.load_candles(symbol, tf)
        frames[tf] = [{"t": r["open_time"], "o": r["open"], "h": r["high"],
                       "l": r["low"], "c": r["close"], "open": bool(r["is_open"])}
                      for r in rows]
    sc = S.build(frames, live, shared.load_calendar(),
                 window_h=settings.event_window_h, symbol=symbol,
                 balance=settings.balance, risk_pct=settings.risk_pct,
                 contract_size=settings.contract_size, lot_step=settings.lot_step,
                 spread=settings.spread,
                 min_stop_points=settings.min_stop_points,
                 max_stop_points=settings.max_stop_points)
    labels = {"breakout": "اختراق صعود", "breakdown": "كسر هبوط", "deeper": "هبوط أعمق"}
    out: list[Level] = []
    for item in sc.get("scenarios") or []:
        trigger = item.get("trigger")
        if trigger is None:
            continue
        # A long breakout is confirmed ABOVE its level; a short breakdown BELOW.
        side = "above" if item.get("side") == "long" else "below"
        kind = labels.get(item.get("kind", ""), str(item.get("kind")))
        stop = item.get("stop")
        tgt = (item.get("targets") or [None])[0]
        rr = item.get("rr")
        # Only a scenario that pays its way gets a buy/sell instruction: a break
        # with R:R under the desk's own floor is a level to watch, not a trade to
        # take, and calling it a trade would be the alert inventing conviction.
        actionable = rr is not None and rr >= 1.5
        why = (f"سيناريو {item.get('frame')} · تأكيد بإغلاق "
               f"{item.get('confirm')} · R:R {rr}")
        out.append(Level(
            float(trigger), kind, side,
            f"سيناريو {item.get('frame')} · تأكيد {item.get('confirm')} · R:R {rr}",
            call_side=item.get("side") if actionable else "",
            stop=float(stop) if (actionable and stop is not None) else None,
            target=float(tgt) if (actionable and tgt is not None) else None,
            why=why if actionable else ""))
    return tuple(out)


def _dedupe(levels: list[Level]) -> list[Level]:
    """Drop a level that repeats a price already in the list, keeping the first.

    The plan and the scenario set can name the same shelf from two angles; two
    alerts for one price is the noise this module exists to avoid. First wins, so
    the plan's own label (the more specific one) is kept.
    """
    out: list[Level] = []
    seen: set[float] = set()
    for lv in levels:
        key = round(lv.price, 2)
        if key in seen:
            continue
        seen.add(key)
        out.append(lv)
    return out


def _auto_levels(settings, live: float, *, symbol: str | None = None) -> tuple[Level, ...]:
    """The levels to watch when the caller names none.

    The desk's own last plan, plus -- unless switched off -- its own scenario
    triggers. The hand-typed DEFAULT_LEVELS is the last resort, not the default:
    a number typed days ago watches a trade nobody is in.
    """
    from .memory import SharedMemory

    levels: list[Level] = []
    try:
        rows = SharedMemory(settings.shared_db).recent_runs(1)
        if rows:
            raw = rows[0]["payload"] or {}
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
            rec = payload.get("recommendation") or payload
            if isinstance(rec, dict):
                levels.extend(from_recommendation(rec, live))
    except Exception:  # noqa: BLE001 - a bad payload must not stop the watch
        pass
    if getattr(settings, "watch_scenarios", True):
        try:
            levels.extend(from_scenarios(settings, live, symbol=symbol))
        except Exception:  # noqa: BLE001 - a missing frame must not stop the watch
            pass
    return tuple(_dedupe(levels)) or DEFAULT_LEVELS


def watch_once(settings, *, symbol: str | None = None, within: float = 12.0,
               notifier=None, levels=None) -> dict:
    """One pass over the watched levels: alert once per break and once per
    approach, then say what happened.

    This is the whole of the level watcher, in one place, so the scheduled
    `levels` command and a live `--listen` session cannot drift apart -- a live
    session that checked a different list, or skipped the ledger, would either
    spam or miss. The ledger keys on level+side, so a pass that runs every few
    seconds during a session still sends each signal exactly once.

    With no explicit `levels`, the desk's OWN last plan and its own SCENARIO
    triggers are watched -- the entry, the stop, the first target, and the
    breakout/breakdown levels the scenarios are built around. That is the
    difference between an alert that fires on the trade the desk described and
    one that fires on a number typed days ago.
    """
    from . import sources
    from .notify import Notifier, SignalLedger

    symbol = symbol or settings.symbol
    live = float(sources.fetch_quote(symbol)["mid"])
    if levels is None:
        levels = _auto_levels(settings, live, symbol=symbol)
    hits = triggered(live, levels)
    near = approaching(live, levels, within=within)
    ledger = SignalLedger(settings.signal_ledger)
    n = notifier if notifier is not None else Notifier(settings)
    sent, warned = [], []
    for lv in hits:
        fp = fingerprint(lv)
        if ledger.seen(fp):
            continue
        if n.send(message(lv, live)):
            ledger.record(fp, f"{lv.price:g}")
            sent.append(lv.label)
    for lv, dist in near:
        fp = near_fingerprint(lv)
        if ledger.seen(fp):
            continue
        if n.send(near_message(lv, live, dist)):
            ledger.record(fp, f"near {lv.price:g}")
            warned.append(lv.label)
    return {"live": live, "total": len(levels), "crossed": [lv.label for lv in hits],
            "near": [(lv.label, dist) for lv, dist in near],
            "alerted": sent, "warned": warned}
