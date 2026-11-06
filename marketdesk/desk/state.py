"""The market's state, in a handful of coarse buckets.

The desk's verdict is a blend of the directional specialists' opinions. This
module answers a different question, the one a trader actually asks: *when the
market looked like this before, what happened next?* To answer it the market has
to be reduced to a small, countable state -- coarse enough that each state has a
real sample, fine enough that the states differ.

Four axes, chosen because each is a different kind of fact about the market and
none of them is a restatement of another:

* trend   -- price above or below its 50-day mean (the regime)
* rsi     -- the 14-day RSI bucket (the momentum / over-extension)
* range   -- where price sits in its 20-day range (position within the range)
* vol     -- current ATR against its own recent average (quiet vs excited)

2 x 5 x 3 x 3 = 90 states. On ten years of daily gold that is ~25 bars per state
on average, which is thin, so `analog` reports `n` with every answer and refuses
to draw a conclusion from a state with too few samples. A finer grid would find
patterns in noise; this one is deliberately blunt.

`state_at` reads only bars up to and including `i` -- the state is a fact about
the past, never about the bar whose outcome is being measured. That is the one
rule this module must never break, so the lookahead test checks it directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import indicators as ind

RSI_BUCKETS = ((0, 30), (30, 45), (45, 55), (55, 70), (70, 101))
RANGE_BUCKETS = ((0.0, 0.25), (0.25, 0.75), (0.75, 1.01))
VOL_BUCKETS = ((0.0, 0.8), (0.8, 1.25), (1.25, 99.0))   # ATR / its 20-bar mean
RANGE_WINDOW = 20
TREND_MA = 50
VOL_MA = 20


@dataclass(frozen=True)
class State:
    """One coarse description of the market at a bar."""

    trend_up: bool
    rsi_bucket: int
    range_bucket: int
    vol_bucket: int

    def key(self) -> tuple[int, int, int, int]:
        return (1 if self.trend_up else 0, self.rsi_bucket, self.range_bucket,
                self.vol_bucket)

    def label_ar(self) -> str:
        trend = "فوق المتوسط 50" if self.trend_up else "تحت المتوسط 50"
        rsi = ("تشبّع بيعي" if self.rsi_bucket == 0 else
               "ضعيف" if self.rsi_bucket == 1 else
               "محايد" if self.rsi_bucket == 2 else
               "قوي" if self.rsi_bucket == 3 else "تشبّع شرائي")
        rng = ("أسفل المدى" if self.range_bucket == 0 else
               "وسط المدى" if self.range_bucket == 1 else "أعلى المدى")
        vol = ("تقلّب هادئ" if self.vol_bucket == 0 else
               "تقلّب عادي" if self.vol_bucket == 1 else "تقلّب مرتفع")
        return f"{trend} · RSI {rsi} · {rng} · {vol}"


def _bucket(value: float, buckets: tuple[tuple[float, float], ...]) -> int:
    for k, (lo, hi) in enumerate(buckets):
        if lo <= value < hi:
            return k
    return len(buckets) - 1


def _rsi_bucket(r: float) -> int:
    return _bucket(r, RSI_BUCKETS)


def _range_bucket(pos: float) -> int:
    return _bucket(pos, RANGE_BUCKETS)


def _vol_bucket(ratio: float) -> int:
    return _bucket(ratio, VOL_BUCKETS)


def state_at(C: list[dict], i: int) -> State | None:
    """The state at bar `i`, using only bars `0..i`. None when the history is too
    short to compute every axis. Never reads past `i` -- see the module docstring.
    """
    if i < max(TREND_MA, VOL_MA + 14) + 1:
        return None
    closes = [b["c"] for b in C[:i + 1]]
    ma = ind.sma(closes, TREND_MA)
    r = ind.rsi(closes, 14)
    if ma is None or r is None:
        return None
    w = C[i - RANGE_WINDOW + 1:i + 1]
    hi = max(b["h"] for b in w)
    lo = min(b["l"] for b in w)
    pos = (closes[-1] - lo) / (hi - lo) if hi > lo else 0.5
    atr_now = ind.atr(C[:i + 1])
    atr_prev = ind.atr(C[:i - VOL_MA + 1]) if i >= VOL_MA else None
    if not atr_now or not atr_prev:
        ratio = 1.0
    else:
        ratio = atr_now / atr_prev
    return State(closes[-1] > ma, _rsi_bucket(r), _range_bucket(pos),
                 _vol_bucket(ratio))


def all_states(C: list[dict]) -> dict[tuple[int, int, int, int], list[int]]:
    """Map every state to the indices where it occurred, oldest first."""
    out: dict[tuple[int, int, int, int], list[int]] = {}
    for i in range(len(C)):
        st = state_at(C, i)
        if st is None:
            continue
        out.setdefault(st.key(), []).append(i)
    return out
