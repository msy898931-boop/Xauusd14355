"""The live two-sided plan: a conditional long and a conditional short, each with
a studied stop and a ladder of targets.

The desk's orchestrator answers "what is the bias?". This answers the other
question a trader actually asks: "where do I act, and where do I get out?" It is
built only from the signal the scan found evidence for -- a break of the prior
20-day range -- and it is graded by the same rules as the backtest: enter at the
close, stop 1.5xATR, scale out in thirds at 1R / 2R / 3R.

Two things keep it honest, and both are printed, not buried:

* Every side carries the sample evidence that supports it. In this data the long
  side is supported and the short side is noise, and the plan says so rather than
  presenting two equally-weighted options.
* The trigger is checked against the control. The bracket's entry added +0.015R
  over always-long (t=0.14), so the plan states plainly that the long side's
  profit is the market's drift, not a proven edge in the trigger.

Nothing here is a promise. It is a conditional plan with its evidence attached.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import indicators as ind

LOOKBACK = 20          # the prior-range window the scan found evidence for
STOP_K = 1.5           # stop at 1.5 x ATR
LADDER = (1.0, 2.0, 3.0)
SIDE_EVIDENCE = {
    "long": {"n": 47, "hit": 0.660, "mean_r": 0.915, "t": 4.80, "supported": True},
    "short": {"n": 16, "hit": 0.375, "mean_r": -0.026, "t": -0.10, "supported": False},
}
RUNG_REACH = {1.0: 0.683, 2.0: 0.540, 3.0: 0.413}   # reached before the stop


@dataclass
class Side:
    direction: str
    trigger: float
    entry: float
    stop: float
    risk: float
    targets: list[float] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

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

    long_side = Side("long", hi, hi, hi - risk, risk,
                     [hi + m * risk for m in LADDER], SIDE_EVIDENCE["long"])
    short_side = Side("short", lo, lo, lo + risk, risk,
                      [lo - m * risk for m in LADDER], SIDE_EVIDENCE["short"])
    closes = [b["c"] for b in C]
    return {
        "live": live,
        "last_closed": closed[-1],
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
        reach = RUNG_REACH.get(m)
        extra = f"   reached {reach:.0%} of the time" if reach else ""
        out.append(f"    target{i} {t:,.2f}   ({m:.0f}R, {m*s.risk:,.2f} pts){extra}")
    ev = s.evidence
    verdict = "SUPPORTED" if ev["supported"] else "NOT SUPPORTED — reduce size"
    out.append(f"    evidence  n={ev['n']} hit={ev['hit']:.1%} "
               f"meanR={ev['mean_r']:+.3f} t={ev['t']:+.2f}  -> {verdict}")
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
    L.append("  the two sides are NOT equally supported. the long side is backed by")
    L.append("  the sample; the short side is noise. trade the long side first.")
    L.append("  the trigger adds +0.015R over always-long (t=0.14): the long side's")
    L.append("  profit is the market's drift, not a proven edge in the trigger.")
    L.append("  one sample, one market, ~20 months. not a promise.")
    return "\n".join(L)
