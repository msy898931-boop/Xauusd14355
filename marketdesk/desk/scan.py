"""Pre-registered signal scan.

The desk's blend showed no edge over a constant direction, and tuning its stops
and targets did not change that. So this asks the prior question: is there any
edge in the data at all? It tests five standard signal families on every daily
bar, with no selection and no tuning on the answer, and prints each one next to
the constant-direction controls.

Two rules make the answer trustworthy:

* Five families is five chances to get lucky, so significance is Bonferroni
  corrected for the number of families tested (0.05/5 -> |t| > 2.58).
* Every family is split into the first 70% and the last 30% of the sample. A
  signal that lives in one half only is reported as not holding.

The result is not a strategy. It is the evidence that decides what is worth
building, and the controls that keep a regime from being read as skill.
"""

from __future__ import annotations

import statistics as st
from pathlib import Path

from . import indicators as ind
from .backtest import load_daily

HORIZON = 20
STOP_K = 1.5          # stop at 1.5 x ATR
TGT_M = 2.0           # target at 2R
MIN_BARS = 60         # enough history for every indicator used
SPLIT = 0.7


def _shape(raw: list[dict]) -> list[dict]:
    """The indicator helpers speak {h,l,c}; the CSV speaks the long names."""
    return [{"openTime": b["openTime"], "o": b["open"], "h": b["high"],
             "l": b["low"], "c": b["close"], "tv": b.get("tickVolume") or 0}
            for b in raw]


def grade(bars: list[dict], i: int, long: bool) -> float | None:
    """Enter at the close of bar i; stop 1.5xATR, target 2R, first touch wins."""
    atr = ind.atr(bars[:i + 1])
    if not atr:
        return None
    risk = STOP_K * atr
    entry = bars[i]["c"]
    stop = entry - risk if long else entry + risk
    tgt = entry + TGT_M * risk if long else entry - TGT_M * risk
    for b in bars[i + 1:i + 1 + HORIZON]:
        if long:
            if b["l"] <= stop:
                return -1.0
            if b["h"] >= tgt:
                return TGT_M
        else:
            if b["h"] >= stop:
                return -1.0
            if b["l"] <= tgt:
                return TGT_M
    end = bars[min(i + HORIZON, len(bars) - 1)]["c"]
    return ((end - entry) if long else (entry - end)) / risk


# ---- the families. Each returns True (long), False (short) or None (no call).

def ema_cross(bars, i):
    c = [b["c"] for b in bars[:i + 1]]
    e20, e50 = ind.ema(c, 20), ind.ema(c, 50)
    if e20[-1] is None or e50[-1] is None:
        return None
    return e20[-1] > e50[-1]


def px_ma50(bars, i):
    c = [b["c"] for b in bars[:i + 1]]
    m = ind.sma(c, 50)
    return None if m is None else c[-1] > m


def breakout20(bars, i):
    w = bars[i - 19:i + 1]
    hi = max(b["h"] for b in w[:-1])
    lo = min(b["l"] for b in w[:-1])
    if bars[i]["c"] >= hi:
        return True
    if bars[i]["c"] <= lo:
        return False
    return None


def rsi_revert(bars, i):
    r = ind.rsi([b["c"] for b in bars[:i + 1]], 14)
    if r is None:
        return None
    if r < 30:
        return True
    if r > 70:
        return False
    return None


def momentum60(bars, i):
    if i < 60:
        return None
    return bars[i]["c"] > bars[i - 60]["c"]


FAMILIES = [
    ("ema20>ema50", ema_cross),
    ("price>ma50", px_ma50),
    ("20d breakout", breakout20),
    ("rsi<30 / >70", rsi_revert),
    ("60d momentum", momentum60),
]


def _stat(rs: list[float]) -> tuple[int, float, float]:
    n = len(rs)
    if n < 2:
        return n, 0.0, 0.0
    m = sum(rs) / n
    se = st.pstdev(rs) / n ** 0.5
    return n, m, (m / se if se else 0.0)


def run_scan(csv_path: Path) -> str:
    bars = _shape(load_daily(csv_path))
    n = len(bars)
    bar = 0.05 / len(FAMILIES)          # Bonferroni for the family count
    z = 2.576                          # |t| at the 0.005 two-sided level
    lines = [f"Signal scan — {csv_path.name}",
             f"  {n} bars · stop {STOP_K}xATR · target {TGT_M}R · {HORIZON}d horizon",
             f"  {len(FAMILIES)} families tested, so the bar is |t| > {z:.2f} "
             f"(p={bar:.3f})", ""]

    for name, fn in FAMILIES:
        rs = []
        for i in range(MIN_BARS, n - 1):
            call = fn(bars, i)
            if call is not None:
                r = grade(bars, i, call)
                if r is not None:
                    rs.append(r)
        cnt, m, t = _stat(rs)
        hit = sum(1 for r in rs if r > 0) / cnt if cnt else 0.0
        mark = "significant" if abs(t) > z else "noise"
        lines.append(f"  {name:>14}  n={cnt:3}  hit={hit:5.1%}  meanR={m:+.3f}  "
                     f"t={t:+5.2f}  {mark}")

    lines.append("")
    lines.append("  controls (same bars, no signal):")
    for lbl, long in (("always long", True), ("always short", False)):
        graded = [grade(bars, i, long) for i in range(MIN_BARS, n - 1)]
        ctrl: list[float] = [r for r in graded if r is not None]
        cnt, m, t = _stat(ctrl)
        lines.append(f"  {lbl:>14}  n={cnt:3}  meanR={m:+.3f}  t={t:+5.2f}")
    drift = (bars[-1]["c"] / bars[MIN_BARS]["c"] - 1) * 100
    lines.append(f"  buy & hold over the window: {drift:+.1f}%")

    lines.append("")
    lines.append("  out of sample — a signal must survive the split:")
    split = MIN_BARS + int((n - MIN_BARS) * SPLIT)
    for name, fn in FAMILIES:
        halves = []
        for lo, hi in ((MIN_BARS, split), (split, n - 1)):
            rs = []
            for i in range(lo, hi):
                call = fn(bars, i)
                if call is not None:
                    r = grade(bars, i, call)
                    if r is not None:
                        rs.append(r)
            halves.append(_stat(rs)[1] if rs else 0.0)
        a, b = halves
        verdict = "holds" if a > 0.05 and b > 0.05 else "does NOT hold"
        lines.append(f"  {name:>14}  in={a:+.3f}R   out={b:+.3f}R   -> {verdict}")

    lines.append("")
    lines.append("  A family that is significant AND holds out of sample is worth")
    lines.append("  building on. Anything else is a regime or a fluke.")
    return "\n".join(lines)
