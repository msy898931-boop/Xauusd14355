"""The live two-sided plan: a conditional long and a conditional short, each with
a studied stop and a ladder of targets.

The desk's orchestrator answers "what is the bias?". This answers the other
question a trader actually asks: "where do I act, and where do I get out?" It is
built only from the signal the scan found evidence for -- a break of the prior
20-day range -- and it is graded by the same rules as the backtest: enter at the
close, stop 1.5xATR, scale out in thirds at 1R / 2R / 3R.

The evidence attached to each side is *measured now* (see `evidence.py`), not a
constant. It used to be a hardcoded table that read like a result and never
changed; it now recomputes the side's n, hit rate, expectancy and t against the
constant-direction control, so a side with no edge is labelled as such.

Nothing here is a promise. It is a conditional plan with its evidence attached.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import evidence as ev
from . import indicators as ind

LOOKBACK = ev.LOOKBACK   # the prior-range window the scan found evidence for
STOP_K = ev.STOP_K       # stop at 1.5 x ATR
LADDER = (1.0, 2.0, 3.0)


@dataclass
class Side:
    direction: str
    trigger: float
    entry: float
    stop: float
    risk: float
    targets: list[float] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    # Measured share of trigger bars that reached each rung before the stop,
    # keyed by the rung multiple. Empty when the side has no sample.
    reach: dict = field(default_factory=dict)

    @property
    def risk_pct(self) -> float:
        return self.risk / self.entry * 100


def _closed(bars: list[dict]) -> list[dict]:
    return [b for b in bars if not b.get("isOpen")]


def build_plan(bars: list[dict], live: float) -> dict:
    """bars: daily candles oldest-first (may include the open bar). live: quote."""
    closed = _closed(bars)
    if len(closed) < LOOKBACK + 60:
        raise ValueError(f"need {LOOKBACK + 60}+ closed bars, have {len(closed)}")
    C = [{"o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"]}
         for b in closed]
    atr = ind.atr(C)
    if not atr:
        raise ValueError("no ATR -- not enough closed bars")
    risk = STOP_K * atr

    window = closed[-(LOOKBACK + 1):-1]      # prior bars, excluding the last
    hi = max(b["high"] for b in window)
    lo = min(b["low"] for b in window)

    # Measured, not stored: the same bracket this plan prints, graded on every
    # historical day of the shipped sample, against the constant direction.
    measured = ev.measure(C)
    long_ev = measured.long.as_dict()
    short_ev = measured.short.as_dict()
    # How often each rung was actually reached, per side -- measured on the same
    # bars, replacing the hardcoded reach table that used to be printed as fact.
    reach = ev.rung_reach(C)

    long_side = Side("long", hi, hi, hi - risk, risk,
                     [hi + m * risk for m in LADDER], long_ev,
                     reach["long"]["reach"])
    short_side = Side("short", lo, lo, lo + risk, risk,
                      [lo - m * risk for m in LADDER], short_ev,
                      reach["short"]["reach"])
    closes = [b["c"] for b in C]
    return {
        "live": live,
        "last_closed": closed[-1],
        "evidence_base": measured.base(),
        "closed_count": len(closed),
        "atr": atr,
        "risk": risk,
        "ema20": ind.ema(closes, 20)[-1],
        "ema50": ind.ema(closes, 50)[-1],
        "rsi": ind.rsi(closes, 14),
        "prior_high": hi,
        "prior_low": lo,
        # The window's dates are printed with the range: a 20-day range can be
        # dominated by a move that is weeks old, and a trigger 290 points away
        # reads as a mistake until the reader sees that the range starts on the
        # day gold was 300 points higher.
        "window_start": str(window[0].get("openTime", ""))[:10],
        "window_end": str(window[-1].get("openTime", ""))[:10],
        "window_bars": len(window),
        "range_pct": (live - lo) / (hi - lo) * 100 if hi > lo else 0.0,
        "long": long_side,
        "short": short_side,
    }


def _side_lines(s: Side) -> list[str]:
    out = [f"  {'LONG' if s.direction == 'long' else 'SHORT'} — trigger "
           f"{'break above' if s.direction == 'long' else 'break below'} {s.trigger:,.2f}",
           f"    entry   {s.entry:,.2f}",
           f"    stop    {s.stop:,.2f}   ({s.risk:,.2f} pts = {s.risk_pct:.2f}%)"]
    for i, (m, t) in enumerate(zip(LADDER, s.targets, strict=True), 1):
        reach = s.reach.get(m)
        extra = (f"   reached {reach:.0%} of the time (n={s.evidence['n']})"
                 if reach is not None else "")
        out.append(f"    target{i} {t:,.2f}   ({m:.0f}R, {m*s.risk:,.2f} pts){extra}")
    ev_d = s.evidence
    verdict = "مدعوم" if ev_d.get("supported") else "غير مدعوم — لا تعتمد عليه"
    out.append(f"    الدليل     n={ev_d['n']} إصابة={ev_d['hit']:.1%} "
               f"توقع={ev_d['mean_r']:+.3f}R خطأ={ev_d['base_r']:+.3f}R "
               f"ميزة={ev_d['edge']:+.3f}R t={ev_d['t']:+.2f}  -> {verdict}")
    return out


def format_plan(p: dict, symbol: str = "XAUUSD") -> str:
    last = p["last_closed"]
    L: list[str] = []
    L.append("=" * 74)
    L.append(f"{symbol} — two-sided plan (closed bars only)")
    L.append("=" * 74)
    L.append(f"  live quote        {p['live']:,.2f}")
    L.append(f"  last closed bar   {last['openTime'][:10]}  close {last['close']:,.2f}")
    L.append(f"  bars used         {p['closed_count']} closed")
    L.append(f"  ema20 {p['ema20']:,.2f}   ema50 {p['ema50']:,.2f}   "
             f"ATR(1d) {p['atr']:,.2f}   RSI {p['rsi']:.1f}")
    trend = "LONG" if p["ema20"] > p["ema50"] else "SHORT"
    L.append(f"  trend signal      {trend} (ema20 "
             f"{'>' if p['ema20'] > p['ema50'] else '<'} ema50)")
    L.append(f"  prior 20d range   {p['prior_low']:,.2f} .. {p['prior_high']:,.2f}   "
             f"(live is {p['range_pct']:.0f}% up the range)")
    if p.get("window_start"):
        L.append(f"                    window {p['window_start']} .. {p['window_end']} "
                 f"({p['window_bars']} bars)")
    L.append("")
    L.extend(_side_lines(p["long"]))
    L.append("")
    L.extend(_side_lines(p["short"]))
    L.append("")
    L.append("-" * 74)
    L.append("  management: scale 1/3 at each rung; move the stop to entry after 1R.")
    long_ok = p["long"].evidence.get("supported")
    short_ok = p["short"].evidence.get("supported")
    if long_ok and not short_ok:
        L.append("  the two sides are NOT equally supported: the long side clears the")
        L.append("  evidence bar, the short side does not. trade the supported side.")
    elif short_ok and not long_ok:
        L.append("  the two sides are NOT equally supported: the short side clears the")
        L.append("  evidence bar, the long side does not. trade the supported side.")
    elif not long_ok and not short_ok:
        L.append("  NEITHER side clears the evidence bar on this sample. the plan is")
        L.append("  a map of where the range breaks, not a trade to take blind.")
    else:
        L.append("  both sides clear the evidence bar; size by the weaker one.")
    base = p.get("evidence_base") or {}
    if base:
        L.append(f"  control: always-long {base.get('long_r', 0):+.3f}R, "
                 f"always-short {base.get('short_r', 0):+.3f}R over the same bars.")
    L.append("  one sample, one market, ~10 years. not a promise.")
    return "\n".join(L)
