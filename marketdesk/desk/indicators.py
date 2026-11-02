"""Indicators — pure functions over a list of bars.

A bar is `{"t","o","h","l","c","tv"}`. Every function returns None when the
sample is too short to compute an honest value; none returns 0 as a stand-in.
That rule is the desk's whole reason to exist: a number that was not earned is
not printed.
"""

from __future__ import annotations


def ema(values: list[float], n: int) -> list[float | None]:
    if len(values) < n:
        return [None] * len(values)
    k = 2 / (n + 1)
    out: list[float | None] = [None] * (n - 1)
    seed = sum(values[:n]) / n
    out.append(seed)
    for v in values[n:]:
        prev = out[-1] if out[-1] is not None else seed
        out.append(v * k + prev * (1 - k))
    return out


def sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def true_ranges(bars: list[dict]) -> list[float]:
    out = []
    for i in range(1, len(bars)):
        h, low, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        out.append(max(h - low, abs(h - pc), abs(low - pc)))
    return out


def atr(bars: list[dict], n: int = 14) -> float | None:
    trs = true_ranges(bars)
    if len(trs) < n:
        return None
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def macd(closes: list[float]) -> tuple[float | None, float | None, float | None]:
    """Returns (line, signal, histogram). None where the sample is too short."""
    e12, e26 = ema(closes, 12), ema(closes, 26)
    if e12[-1] is None or e26[-1] is None:
        return None, None, None
    line = e12[-1] - e26[-1]
    hist_series = [a - b for a, b in zip(e12[26:], e26[26:], strict=False)
                   if a is not None and b is not None]
    if not hist_series:
        return line, None, None
    sig = ema(hist_series, 9)[-1]
    return line, sig, (line - sig if sig is not None else None)


def pivots(bars: list[dict], wing: int = 3) -> tuple[list[float], list[float]]:
    """Swing lows and highs: a bar whose extreme is the extreme of its
    `2*wing+1` neighbourhood. These are prices the market actually printed."""
    hi, lo = [], []
    for i in range(wing, len(bars) - wing):
        w = bars[i - wing:i + wing + 1]
        if bars[i]["h"] == max(x["h"] for x in w):
            hi.append(bars[i]["h"])
        if bars[i]["l"] == min(x["l"] for x in w):
            lo.append(bars[i]["l"])
    return lo, hi


def structure(bars: list[dict], wing: int = 5) -> str:
    """Higher-highs/higher-lows -> up, lower-highs/lower-lows -> down, else range."""
    lo, hi = pivots(bars, wing)
    if len(lo) < 2 or len(hi) < 2:
        return "unknown"
    if hi[-1] > hi[-2] and lo[-1] > lo[-2]:
        return "up"
    if hi[-1] < hi[-2] and lo[-1] < lo[-2]:
        return "down"
    return "range"


def fib_retracement(high: float, low: float, ratio: float) -> float:
    """A retracement level of a leg from `low` to `high`."""
    return low + (high - low) * ratio


def pct_change(a: float, b: float) -> float | None:
    return None if a == 0 else (b / a - 1) * 100


def slope(values: list[float | None], n: int = 5) -> float | None:
    """Simple slope of the last n non-None points, per step."""
    pts = [v for v in values if v is not None][-n:]
    if len(pts) < 2:
        return None
    return (pts[-1] - pts[0]) / (len(pts) - 1)
