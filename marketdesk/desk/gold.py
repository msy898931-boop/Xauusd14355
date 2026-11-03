"""The gold study: when gold looked like this before, what happened next?

The desk's history is ~500 daily bars. This uses the 10-year file instead, and
asks the question a trader actually has at the moment of a decision: the market
is in some state right now -- trend, RSI, where it sits in its range -- and over
the last ten years, how often did that state come up, and what did the next days
look like?

It is descriptive, not predictive, and the report says so. Three coarse buckets
keep the state space small (30 states) so each one has a real sample; a finer
grid would find patterns in noise. Every bucket is reported with its n, and the
base rate is printed next to it, because a 59% down-rate means nothing without
knowing the unconditional rate.

The last section is the practical one: it takes the trade under consideration --
entry, stop, target, in ATR units -- and grades it on every historical day in the
same state, first-touch and stop-first, so the answer is an expectancy in R
rather than a feeling.
"""
from __future__ import annotations

import statistics as st
from pathlib import Path

from . import indicators as ind
from .backtest import load_daily

HORIZONS = (5, 10)
WARMUP = 60
RSI_BUCKETS = ((0, 30), (30, 45), (45, 55), (55, 70), (70, 101))
RANGE_BUCKETS = ((0.0, 0.25), (0.25, 0.75), (0.75, 1.01))


def _shape(bars: list[dict]) -> list[dict]:
    return [{"o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"],
             "openTime": b.get("openTime", "")}
            for b in bars if not b.get("isOpen")]


def _rsi_bucket(r: float) -> int:
    for k, (lo, hi) in enumerate(RSI_BUCKETS):
        if lo <= r < hi:
            return k
    return len(RSI_BUCKETS) - 1


def _range_bucket(pos: float) -> int:
    for k, (lo, hi) in enumerate(RANGE_BUCKETS):
        if lo <= pos < hi:
            return k
    return len(RANGE_BUCKETS) - 1


def state_at(C: list[dict], i: int) -> tuple | None:
    c = [b["c"] for b in C[:i + 1]]
    ma = ind.sma(c, 50)
    r = ind.rsi(c, 14)
    if ma is None or r is None:
        return None
    w = C[max(0, i - 19):i + 1]
    hi = max(b["h"] for b in w)
    lo = min(b["l"] for b in w)
    pos = (c[-1] - lo) / (hi - lo) if hi > lo else 0.5
    return (1 if c[-1] > ma else 0, _rsi_bucket(r), _range_bucket(pos))


def forward_pct(C: list[dict], i: int, h: int) -> float | None:
    j = i + h
    if j >= len(C):
        return None
    return (C[j]["c"] - C[i]["c"]) / C[i]["c"] * 100


def bracket(C: list[dict], i: int, long: bool, stop_atr: float,
            tgt_atr: float, h: int) -> float | None:
    """R multiple for a bracket entered at close i, first touch wins, stop first
    on an ambiguous bar."""
    atr = ind.atr(C[:i + 1])
    if not atr:
        return None
    risk = stop_atr * atr
    reward = tgt_atr * atr
    e = C[i]["c"]
    stop = e - risk if long else e + risk
    tgt = e + reward if long else e - reward
    for b in C[i + 1:i + 1 + h]:
        if long:
            if b["l"] <= stop:
                return -1.0
            if b["h"] >= tgt:
                return tgt_atr / stop_atr
        else:
            if b["h"] >= stop:
                return -1.0
            if b["l"] <= tgt:
                return tgt_atr / stop_atr
    end = C[min(i + h, len(C) - 1)]["c"]
    move = (end - e) if long else (e - end)
    return move / risk


def _bracket_stats(C: list[dict], idx: list[int], long: bool, sa: float,
                   ta: float, h: int) -> tuple[int, float, float]:
    rs = [r for i in idx if (r := bracket(C, i, long, sa, ta, h)) is not None]
    if not rs:
        return 0, 0.0, 0.0
    wins = sum(1 for r in rs if r > 0)
    return len(rs), wins / len(rs), sum(rs) / len(rs)


def _pct_stats(C: list[dict], idx: list[int], h: int):
    vals = [v for i in idx if (v := forward_pct(C, i, h)) is not None]
    if not vals:
        return 0, 0.0, 0.0, 0.0, 0.0
    up = sum(1 for v in vals if v > 0) / len(vals)
    return len(vals), up, st.median(vals), min(vals), max(vals)


def run_gold_study(csv: Path, live: float | None = None, symbol: str = "XAUUSD",
                   stop_atr: float = 0.32, tgt_atr: float = 0.60) -> str:
    C = _shape(load_daily(csv))
    if len(C) < 300:
        raise ValueError(f"need 300+ bars, have {len(C)}")
    last = C[-1]
    px = live if live else last["c"]
    cur = state_at(C, len(C) - 1)
    if cur is None:
        raise ValueError("not enough history for the current state")

    all_idx = [i for i in range(WARMUP, len(C) - 1) if state_at(C, i) is not None]
    ana = [i for i in all_idx if state_at(C, i) == cur]

    c = [b["c"] for b in C]
    atr = ind.atr(C) or 0.0
    rsi = ind.rsi(c, 14) or 0.0
    ma50 = ind.sma(c, 50) or 0.0
    w = C[-20:]
    hi = max(b["h"] for b in w)
    lo = min(b["l"] for b in w)
    pos = (px - lo) / (hi - lo) * 100 if hi > lo else 50.0

    trend_name = "فوق" if cur[0] else "تحت"
    rsi_name = ["<30", "30-45", "45-55", "55-70", ">70"][cur[1]]
    rng_name = ["أسفل", "وسط", "أعلى"][cur[2]]

    L: list[str] = []
    L.append("=" * 60)
    L.append(f"📚 {symbol} — دراسة 10 سنين ({len(C)} شمعة يومية، "
             f"{_d(C[0])} .. {_d(last)})")
    L.append("=" * 60)
    L.append("الحالة الحالية")
    L.append(f"  السعر الحالي    {px:,.2f}")
    L.append(f"  الاتجاه         {trend_name} المتوسط 50 يوم ({ma50:,.2f})")
    L.append(f"  RSI(14)         {rsi:.1f}  ← الفئة {rsi_name}")
    L.append(f"  موقعه بالمدى    {pos:.0f}% من مدى 20 يوم ← {rng_name}")
    L.append(f"  ATR(1d)         {atr:,.2f}  ({atr / px * 100:.2f}% من السعر)")
    L.append("")
    L.append(f"نظائر تاريخية — {len(ana)} يوم مرّ بنفس الحالة")
    L.append(f"  والمعدّل الأساسي مقيس على كل {len(all_idx)} يوم")
    L.append("")
    L.append(f"  {'الأفق':8} {'عدد النظائر':>11} {'صعود%':>7} {'وسيط%':>9} "
             f"{'أسوأ%':>8} {'أفضل%':>8}   {'أساس صعود%':>10} {'أساس وسيط%':>11}")
    for h in HORIZONS:
        n, up, med, worst, best = _pct_stats(C, ana, h)
        bn, bup, bmed, _, _ = _pct_stats(C, all_idx, h)
        L.append(f"  {h:>3}ي      {n:>11} {up:>6.0%} {med:>+9.2f} "
                 f"{worst:>+8.2f} {best:>+8.2f}   {bup:>9.0%} {bmed:>+10.2f}")
    L.append("")
    L.append("  نسبة هبوط 59% ليست إشارة بحد ذاتها — قارنها بالمعدّل الأساسي")
    L.append("  جانبها. الفرق هو معلومة الحالة.")
    L.append("")
    L.append("-" * 60)
    L.append(f"الصفقة قيد الدراسة — وقف {stop_atr} × ATR، "
             f"هدف {tgt_atr} × ATR  (مخاطرة/مكافأة {tgt_atr / stop_atr:.2f})")
    L.append("-" * 60)
    L.append(f"  {'الجهة':6} {'الأفق':8} {'عدد':>6} {'إصابة%':>8} {'التوقّع R':>12}")
    for side, long in (("بيع", False), ("شراء", True)):
        for h in HORIZONS:
            n, hit, exp_r = _bracket_stats(C, ana, long, stop_atr, tgt_atr, h)
            bn, bhit, bexp = _bracket_stats(C, all_idx, long, stop_atr, tgt_atr, h)
            L.append(f"  {side:6} {h:>3}ي      {n:>6} {hit:>7.1%} {exp_r:>+14.3f}"
                     f"   (الأساس {bexp:+.3f})")
    L.append("")
    L.append("  التوقّع بالـR: ‎+0.2R يعني 0.2 ضعف المخاطرة لكل صفقة بالمتوسط.")
    L.append("  عمود الأساس هو نفس الصفقة على كل يوم، فالفرق هو ما تضيفه الحالة.")
    L.append("  عيّنة واحدة، سوق واحد. مو وعد.")
    return "\n".join(L)


def _d(bar: dict) -> str:
    return bar.get("openTime", "")[:10] or "?"
