"""Cross-market: does the trend signal generalise, or was it one market's drift?

The scan found `ema20 > ema50` beat always-long on gold by +0.249R (t=+3.83) over
500 daily bars. One market is one sample, and a market that only rises will make
any long-biased rule look good. The test that separates a signal from a drift is
to run the same rule, unchanged, on several markets of different asset classes,
and to compare it against the *best constant* -- not against always-long alone,
because in a falling market always-long is the wrong baseline.

It also answers the window question directly: the same market measured over its
first 500 bars, its last 500, and its full history. A rule that is significant in
one window and noise in the full sample was never a rule.

No tuning anywhere: one lookback, one stop, one horizon for every market.
"""
from __future__ import annotations

import statistics as st
from pathlib import Path

from . import indicators as ind
from .backtest import load_daily

HORIZON = 20
STOP_K = 1.5
TGT_M = 2.0
MIN_BARS = 200
WINDOW = 500           # the size of the desk's original sample


def _shape(bars: list[dict]) -> list[dict]:
    return [{"o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"]}
            for b in bars if not b.get("isOpen")]


def grade(C: list[dict], i: int, long: bool) -> float | None:
    atr = ind.atr(C[:i + 1])
    if not atr:
        return None
    risk = STOP_K * atr
    e = C[i]["c"]
    stop = e - risk if long else e + risk
    tgt = e + TGT_M * risk if long else e - TGT_M * risk
    for b in C[i + 1:i + 1 + HORIZON]:
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
    end = C[min(i + HORIZON, len(C) - 1)]["c"]
    return ((end - e) if long else (e - end)) / risk


def trend_long(C: list[dict], i: int) -> bool | None:
    c = [b["c"] for b in C[:i + 1]]
    e20, e50 = ind.ema(c, 20), ind.ema(c, 50)
    if e20[-1] is None or e50[-1] is None:
        return None
    return e20[-1] > e50[-1]


def _calls(C: list[dict]) -> list[tuple[int, bool]]:
    """(index, direction) for every bar where the rule has an opinion."""
    out: list[tuple[int, bool]] = []
    for i in range(MIN_BARS, len(C) - 1):
        d = trend_long(C, i)
        if d is not None:
            out.append((i, d))
    return out


def _mean(rs: list[float | None]) -> float:
    vals = [r for r in rs if r is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _paired(a: list[float | None], b: list[float | None]) -> tuple[float, float]:
    d = [x - y for x, y in zip(a, b, strict=False) if x is not None and y is not None]
    if len(d) < 2:
        return 0.0, 0.0
    mu = sum(d) / len(d)
    se = st.pstdev(d) / len(d) ** 0.5
    return mu, (mu / se if se else 0.0)


def _accuracy(C: list[dict], lo: int, hi: int) -> tuple[int, float]:
    """Fraction of calls whose direction matched the next HORIZON bars."""
    right = 0
    tot = 0
    for i, d in _calls(C):
        if not (lo <= i < hi):
            continue
        end = C[min(i + HORIZON, len(C) - 1)]["c"]
        moved_up = end > C[i]["c"]
        tot += 1
        if moved_up == d:
            right += 1
    return tot, (right / tot if tot else 0.0)


def run_crossmarket(directory: Path, markets: list[str] | None = None) -> str:
    names = markets or sorted(p.stem.replace("_1d", "") for p in directory.glob("*_1d.csv"))
    L: list[str] = []
    L.append("=" * 96)
    L.append("CROSS-MARKET — does the trend signal generalise, or is it one market's drift?")
    L.append(f"  stop {STOP_K}xATR · target {TGT_M}R · {HORIZON}d · no tuning anywhere")
    L.append("=" * 96)
    L.append(f"  {'market':8} {'bars':>5} {'trend':>8} {'bestConst':>10} "
             f"{'trend-best':>11} {'t':>7}  verdict")

    beat = 0
    tested = 0
    for mk in names:
        p = directory / f"{mk}_1d.csv"
        if not p.exists():
            continue
        C = _shape(load_daily(p))
        if len(C) < MIN_BARS + HORIZON + 5:
            continue
        calls = _calls(C)
        if len(calls) < 30:
            continue
        tr = [grade(C, i, d) for i, d in calls]
        al = [grade(C, i, True) for i, _ in calls]
        sh = [grade(C, i, False) for i, _ in calls]
        # The honest baseline is ONE constant chosen over the whole sample --
        # not max(long, short) per bar, which is an oracle no trader can hold.
        best = al if _mean(al) >= _mean(sh) else sh
        mu, t = _paired(tr, best)
        tested += 1
        wins = mu > 0 and t > 1.96
        if wins:
            beat += 1
        verdict = "BEATS" if wins else ("weak" if mu > 0 else "loses")
        bname = "alwaysL" if best is al else "alwaysS"
        L.append(f"  {mk:8} {len(C):5} {_mean(tr):+8.3f} {_mean(best):+10.3f}"
                 f"  ({bname:7}) {mu:+9.3f} {t:+7.2f}  {verdict}")

    L.append("")
    L.append(f"  markets tested: {tested}   markets where trend beats the best "
             f"constant: {beat}")
    L.append("")
    if beat >= max(3, tested // 2):
        L.append("  -> the signal generalises. it is not one market's drift.")
    else:
        L.append("  -> it does not generalise. on this evidence the result was the")
        L.append("     market's drift, not the rule.")

    L.append("")
    L.append("=" * 96)
    L.append("WINDOW SENSITIVITY — how much a 500-bar sample flatters a rule")
    L.append("=" * 96)
    for mk in names:
        p = directory / f"{mk}_1d.csv"
        if not p.exists():
            continue
        C = _shape(load_daily(p))
        if len(C) < 2 * WINDOW:
            continue
        L.append(f"  {mk}:")
        for lbl, lo, hi in (("first 500", MIN_BARS, MIN_BARS + WINDOW),
                            ("last 500", len(C) - WINDOW, len(C) - 1),
                            ("full", MIN_BARS, len(C) - 1)):
            idx = [(i, d) for i, d in _calls(C) if lo <= i < hi]
            if len(idx) < 30:
                continue
            tr = [grade(C, i, d) for i, d in idx]
            al = [grade(C, i, True) for i, _ in idx]
            mu, t = _paired(tr, al)
            n, acc = _accuracy(C, lo, hi)
            mark = "significant" if abs(t) > 1.96 else "noise"
            L.append(f"    {lbl:10} n={len(idx):4}  trend-alwaysL={mu:+.3f} "
                     f"t={t:+6.2f} {mark:11}  direction hit={acc:.1%}")
    L.append("")
    L.append("  a rule that is significant in one 500-bar window and noise in the")
    L.append("  full sample was never a rule. this is why the sample size decides.")
    return "\n".join(L)
