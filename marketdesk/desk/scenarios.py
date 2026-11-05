"""Two-sided scenarios built only from numbers the desk computed.

The orchestrator answers "what is the bias?". `plan` answers "where do I act,
where do I get out?". This answers the question a trader asks next: "if price
does X, what then — and what would prove me wrong?".

Every level here is a price the market actually printed (`indicators.pivots`),
the stop distance is that level's own frame ATR, and an event is a row from the
calendar. Nothing is invented; a scenario with no level to stand on says so
instead of inventing a number. This module renders text only — it makes no
network call of its own, so a caller decides what to gather.
"""

from __future__ import annotations

import datetime as dt

from . import arabic as ar
from . import indicators as ind
from .account import size_plan
from .config import base_currency

# A level's stop is the ATR of the frame that printed it: a 1h shelf plays out
# over hours, so a daily-ATR stop would be several times too wide. Frames are
# tried finest-first so the nearest level usually comes from the finest frame.
#
# The pool reaches down to 5m on purpose. A small account can only afford a
# tight stop, and a tight stop only exists on a fine frame: the 1h ATR here is
# ~20 points and the 4h ~37, both wider than a $200 account can risk. The 15m
# and 30m frames carry the 10-15 point stops the owner asked for, so the desk
# has to look at them or it can only ever say "this setup is too wide for you".
FRAMES = ("5m", "15m", "30m", "1h", "4h", "1d")
# Levels closer than this fraction of the frame's ATR are one shelf, not two.
MERGE_ATR = 0.6
# A level within this fraction of the frame's ATR of the live price is the price
# itself, not a decision point: a daily pivot two points under gold is noise, and
# a stop measured from it (a whole daily ATR) is nonsense.
MIN_DIST_ATR = 0.3
# How far beyond a level counts as a confirmed break rather than a wick.
CONFIRM_ATR = 0.15


def _closed(bars: list[dict]) -> list[dict]:
    """Only bars the feed marked closed. A partial bar's high/low/close move with
    the tick, so a level read off it is not a fact.

    The flag is spelled three ways in this codebase and one of them is a trap:
    the raw feed says `is_open`, the CSV says `isOpen`, and the reshaped frame the
    agents use says `open`. So `open` is read as a flag ONLY when it is actually a
    bool -- in the long-history and backtest shapes `open` is the bar's OPENING
    PRICE, and treating a price as a truthy flag would drop every bar.
    """
    out = []
    for b in bars:
        flag = b.get("is_open")
        if flag is None:
            flag = b.get("isOpen")
        if flag is None:
            o = b.get("open")
            flag = o if isinstance(o, bool) else False
        if not flag:
            out.append(b)
    return out


def _ohlc(bars: list[dict]) -> list[dict]:
    return [{"o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]} for b in _closed(bars)]


def _frame_atr(bars: list[dict]) -> float | None:
    c = _ohlc(bars)
    return ind.atr(c) if len(c) >= 20 else None


def _candidates(bars_by_frame: dict[str, list[dict]], live: float,
                min_atr: float = 0.0, max_atr: float = 0.0) -> list[dict]:
    """Printed levels from every available frame, each carrying its own frame and
    that frame's ATR, deduplicated across frames and sorted by distance from the
    live price.

    Pooling frames is the point: the nearest level above gold is usually a fine
    shelf, while a daily-only read puts the first resistance 120 points away and
    makes every scenario unusable.

    When the trader gave a stop window (`min_atr`/`max_atr`), a level whose frame
    ATR sits inside it is preferred over a nearer one whose stop the trader cannot
    take. The stop is the frame's ATR, so this is how a $200 account gets a 10-15
    point setup instead of a 5m level whose 6-point stop it would refuse anyway.
    Ordering is still by distance *within* each group, so the nearest usable level
    wins and the fallback (nothing in the window) is still the nearest level.
    """
    out: list[dict] = []
    for tf in FRAMES:
        bars = bars_by_frame.get(tf)
        if not bars:
            continue
        atr = _frame_atr(bars)
        if not atr:
            continue
        sup, res = ind.pivots(_ohlc(bars), wing=3)
        for price in list(sup) + list(res):
            out.append({"price": float(price), "frame": tf, "atr": atr})
    if not out:
        return []

    def in_window(c: dict) -> bool:
        if min_atr and c["atr"] < min_atr:
            return False
        if max_atr and c["atr"] > max_atr:
            return False
        return True

    windowed = bool(min_atr or max_atr)
    out.sort(key=lambda x: (0 if (windowed and in_window(x)) else 1,
                            abs(x["price"] - live)))
    kept: list[dict] = []
    for c in out:
        if abs(c["price"] - live) < MIN_DIST_ATR * c["atr"]:
            continue
        tol = MERGE_ATR * c["atr"]
        if any(abs(c["price"] - k["price"]) <= tol for k in kept):
            continue
        kept.append(c)
    return kept


def _next_event(cal: list[dict], now: dt.datetime, window_h: float,
                symbol: str = "XAUUSD") -> dict | None:
    """The nearest high-impact event inside `window_h` that can move `symbol`.

    The currency filter is not cosmetic. Without it the nearest high-impact row
    wins whatever it concerns, and the scenario block told a gold trader about
    New Zealand employment -- an event that cannot move gold -- while the USD
    releases that day went unmentioned. The risk gate already filtered this way;
    the scenarios had not been taught the same rule.
    """
    want = base_currency(symbol)
    best = None
    for e in cal or []:
        if e.get("importance") != "high":
            continue
        if e.get("currency") and e["currency"] != want:
            continue
        try:
            when = dt.datetime.fromisoformat(str(e["time"]).replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError):
            continue
        delta_h = (when - now).total_seconds() / 3600
        if 0 <= delta_h <= window_h and (best is None or delta_h < best[0]):
            best = (delta_h, when, e)
    if best is None:
        return None
    return {"hours": round(best[0], 1), "time": best[1].isoformat(), "row": best[2]}


def _probability(side: str, *, symbol: str = "XAUUSD") -> dict | None:
    """The measured frequency of this side's bracket in the CURRENT market state.

    Probability here is not a model's opinion; it is the hit rate of exactly this
    bracket (stop 1.5xATR, target 2R, 20 bars) on every past day the market was in
    the same coarse state. It returns None when the state's sample is too thin,
    and the caller then prints no probability rather than a made-up one -- a
    "60%" from five samples is a lie with a decimal point.
    """
    from . import history
    from . import state as state_mod
    from .agents.analog import MIN_N, measure_cached

    C = history.shaped(symbol)
    if len(C) < state_mod.TREND_MA + 40:
        return None
    cur = state_mod.state_at(C, len(C) - 1)
    if cur is None:
        return None
    m = measure_cached(C, cur.key())
    s = m.get(side) or {}
    if s.get("n", 0) < MIN_N:
        return None
    return {"p": s["hit"], "n": s["n"], "n_state": m["n_state"],
            "mean_r": s["mean_r"], "t": s["t"], "state": cur.label_ar()}


def build(bars_by_frame: dict[str, list[dict]], live: float,
          calendar: list[dict] | None = None, *,
          now: dt.datetime | None = None, window_h: float = 6.0,
          symbol: str = "XAUUSD", balance: float = 0.0, risk_pct: float = 1.0,
          contract_size: float = 100.0, lot_step: float = 0.01,
          spread: float = 0.25, min_stop_points: float = 0.0,
          max_stop_points: float = 0.0, min_rr: float = 1.5) -> dict:
    """The scenario set: a breakout, a rejection, a deeper move, and the event.

    `bars_by_frame` is closed-bar frames (the agent `frames_data` shape). Frames
    that are missing simply contribute no levels, so the set degrades rather than
    raising.
    """
    now = now or dt.datetime.now(dt.UTC)
    frames = {tf: b for tf, b in (bars_by_frame or {}).items() if _closed(b)}

    struct = {}
    for tf in FRAMES:
        if tf in frames:
            struct[tf] = ind.structure(_ohlc(frames[tf]))

    out: dict = {
        "symbol": symbol, "live": live, "structure": struct,
        "scenarios": [], "event": None, "note": None,
    }
    cand = _candidates(frames, live, min_atr=min_stop_points, max_atr=max_stop_points)
    if not cand:
        out["note"] = "لا توجد مستويات مطبوعة كافية لبناء سيناريو."
        return out

    above = [c for c in cand if c["price"] > live]
    below = [c for c in cand if c["price"] < live]

    def scenario(kind, side, level, why) -> dict | None:
        atr = level["atr"]
        confirm = CONFIRM_ATR * atr
        if side == "long":
            stop, confirm_px = level["price"] - atr, level["price"] + confirm
        else:
            stop, confirm_px = level["price"] + atr, level["price"] - confirm
        # The target must be the nearest level strictly BEYOND the trigger on the
        # scenario's own side. Picking it from the pooled list by position was a
        # bug once the list stopped being sorted by price: a breakout long was
        # handed a target *below* its trigger (R:R computed from absolute values,
        # so it looked positive). Derive it from the trigger, not the list order.
        risk = abs(level["price"] - stop)
        if side == "long":
            beyond = [c for c in cand if c["price"] > level["price"]]
        else:
            beyond = [c for c in cand if c["price"] < level["price"]]
        if beyond:
            beyond.sort(key=lambda c: abs(c["price"] - level["price"]))
            # Aim at the nearest level that actually pays for the stop, not
            # simply the nearest level. On a fine frame the printed pivots sit
            # closer together than that frame's ATR, so the nearest target was
            # NEARER than the stop and the first scenario out was under the
            # desk's own R:R floor almost every time. Only the second level out
            # cleared it -- and a second level was only ever built for the short
            # side (`deeper`), so the one plan that survived was a short. That
            # asymmetry, not the market, is what made the desk short-biased.
            # Walking outward is the same rule `plan` already applies, and it
            # is applied to both sides alike.
            paying = [c for c in beyond
                      if abs(c["price"] - level["price"]) >= min_rr * risk]
            target = (paying[0] if paying else beyond[0])["price"]
        else:
            target = level["price"] + (2 * atr if side == "long" else -2 * atr)
        reward = abs(target - level["price"])
        rr = reward / risk if risk else None
        # A breakout whose target sits behind its trigger is not a plan. Guard it
        # here so no caller can emit one, whatever the candidate order was.
        if side == "long" and target <= level["price"]:
            return None
        if side == "short" and target >= level["price"]:
            return None
        sc = {
            "kind": kind, "side": side, "frame": level["frame"],
            "trigger": round(level["price"], 2), "confirm": round(confirm_px, 2),
            "stop": round(stop, 2), "targets": [round(target, 2)],
            "risk": round(risk, 2), "rr": round(rr, 2) if rr else None,
            "why": why,
            # Invalidation: the price at which the scenario's *thesis* is simply
            # wrong -- the break failed back through its own trigger. Distinct
            # from the stop (a money level); this is a thesis level, and it sits
            # nearer than the stop so it fires first. Printed, so a scenario is a
            # falsifiable claim and not a wish.
            "invalidation": round(
                level["price"] - confirm if side == "long"
                else level["price"] + confirm, 2),
        }
        # Size the scenario in money, for the account the caller named. Kept in
        # points when no balance is set, so the desk still works keyless.
        if balance > 0:
            sc["sizing"] = size_plan(
                balance=balance, risk_pct=risk_pct, entry=level["price"],
                stop=stop, target=target, contract_size=contract_size,
                lot_step=lot_step, spread=spread, min_stop_points=min_stop_points,
                max_stop_points=max_stop_points)
        return sc

    built = []
    if above:
        built.append(scenario(
            "breakout", "long", above[0],
            f"أقرب مقاومة مطبوعة ({above[0]['frame']})؛ إغلاق فوقها يؤكد الاختراق"))
    if below:
        built.append(scenario(
            "breakdown", "short", below[0],
            f"أقرب دعم مطبوع ({below[0]['frame']})؛ إغلاق تحته يفعّل الهبوط"))
    if len(below) > 1:
        built.append(scenario(
            "deeper", "short", below[1],
            "كسر الدعم الثاني يفتح الطريق للمستوى الأبعد"))
    out["scenarios"] = [s for s in built if s is not None]

    # Attach the measured probability of each side's bracket in the CURRENT
    # state. Same measurement for both sides, so the two are comparable; a side
    # whose sample is thin gets no number at all.
    probs: dict[str, dict | None] = {}
    for side in ("long", "short"):
        probs[side] = _probability(side, symbol=symbol)
    for sc in out["scenarios"]:
        sc["probability"] = probs.get(sc["side"])
    out["probability_state"] = next(
        (p["state"] for p in probs.values() if p), None)

    ev = _next_event(calendar or [], now, window_h, symbol)
    if ev:
        row = ev["row"]
        out["event"] = {
            "name": ar.event_name(row.get("name")), "currency": row.get("currency"),
            "hours": ev["hours"], "time": ev["time"],
            "forecast": row.get("forecast"), "previous": row.get("previous"),
        }

    for sc in out["scenarios"]:
        sc["frames_with"] = [tf for tf, st in struct.items()
                             if (sc["side"] == "long" and st == "up")
                             or (sc["side"] == "short" and st == "down")]
    return out


def render(sc: dict) -> str:
    """Telegram-ready HTML. Every number is one the desk computed."""
    L: list[str] = []
    L.append(f"🎯 <b>السيناريوهات</b> — {sc['symbol']}")
    L.append(f"السعر <b>{sc['live']:,.2f}</b>")
    if sc.get("structure"):
        parts = [f"{tf}: {ar.structure(st)}" for tf, st in sc["structure"].items()]
        L.append("البنية — " + " · ".join(parts))
    L.append("")

    if sc.get("event"):
        e = sc["event"]
        f, p = e.get("forecast"), e.get("previous")
        line = f"⚠️ <b>حدث</b> خلال {e['hours']:g} ساعة: {e['name']} [{e['currency']}]"
        if f is not None or p is not None:
            line += (f"\n      متوقع {f if f is not None else '—'}"
                     f" · سابق {p if p is not None else '—'}")
        L.append(line)
        L.append("")

    if not sc.get("scenarios"):
        L.append(f"<i>{sc.get('note') or 'لا مستويات كافية.'}</i>")
    heads = {"breakout": "اختراق صعود", "breakdown": "كسر هبوط", "deeper": "هبوط أعمق"}
    for s in sc["scenarios"]:
        arrow = "🟢" if s["side"] == "long" else "🔴"
        L.append(f"{arrow} <b>{heads.get(s['kind'], s['kind'])}</b> — يتفعّل عند "
                 f"<b>{s['trigger']:,.2f}</b> (تأكيد {s['confirm']:,.2f}) [{s['frame']}]")
        rr = f" · R:R {s['rr']}" if s.get("rr") else ""
        L.append(f"      الوقف {s['stop']:,.2f} ({s['risk']:,.0f} نقطة){rr} · الهدف "
                 + "، ".join(f"{t:,.2f}" for t in s["targets"]))
        prob = s.get("probability")
        if prob:
            L.append(f"      🎲 احتمال الوصول (مقاس): <b>{prob['p']:.0%}</b> "
                     f"من {prob['n']} حالة مشابهة · توقع {prob['mean_r']:+.2f}R")
        if s.get("invalidation") is not None:
            L.append(f"      ❌ يُبطَل عند <b>{s['invalidation']:,.2f}</b> "
                     f"(فشل الاختراق ورجوعه)")
        sz = s.get("sizing")
        if sz is not None:
            if sz.feasible:
                money = (f"💵 <b>{sz.lots:g} لوت</b> · تخاطر "
                         f"<b>${sz.risk_money:,.2f}</b> ({sz.risk_pct_actual:.1f}%)")
                if sz.reward_money is not None:
                    money += f" · تربح ${sz.reward_money:,.2f}"
                L.append(f"      {money}")
            else:
                L.append(f"      🚫 <b>ما بتنفع لهالحساب</b> — أصغر حجم "
                         f"({sz.min_lots:g}) يخاطر ${sz.min_risk_money:,.2f} "
                         f"= {sz.min_risk_pct:.1f}%")
        L.append(f"      <i>{s['why']}</i>")
        if s.get("frames_with"):
            L.append(f"      <i>يتوافق مع: {'، '.join(s['frames_with'])}</i>")
        L.append("")
    L.append("<i>السيناريوهات مشروطة ومبنية على مستويات مطبوعة — مو توصية.</i>")
    return "\n".join(L)
