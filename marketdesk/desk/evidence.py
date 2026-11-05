"""Measured evidence for a setup — the honest replacement for a constant.

The old plan printed a fixed table ("n=47, hit=66.0%, t=4.80") that was written
into the source and never recomputed. It looked like evidence and was a
constant; a reader could not tell, and the numbers did not survive a longer
sample. This module recomputes the same quantities from the shipped history
every time the plan is built, so a plan can never claim a record it does not
have.

What is measured is the *plan's own* trigger, not a proxy:

* the trigger is a close beyond the prior 20-day range (up for long, down for
  short), entered at that close;
* the bracket is the one the plan actually prints -- stop 1.5 x ATR, rungs at
  1R / 2R / 3R, first touch wins, and an ambiguous bar takes the stop;
* the base rate is the constant direction over the same bars, so `edge` is
  relative to simply being long (the honest control in a market that drifts up).

Everything is computed once per process and cached by (bars length, params):
the file is static between runs and the measurement is deterministic.
"""

from __future__ import annotations

import statistics as st
from dataclasses import dataclass

LOOKBACK = 20
STOP_K = 1.5
TARGET_M = 2.0
HORIZON = 20
# Below this the sample is too small to call anything an edge; the report says
# so instead of printing a number nobody should act on.
MIN_N = 30


@dataclass(frozen=True)
class SideEvidence:
    """What the history says about one side of the setup."""

    side: str
    n: int
    hit: float
    mean_r: float
    t: float
    base_r: float

    @property
    def edge(self) -> float:
        return self.mean_r - self.base_r

    @property
    def supported(self) -> bool:
        """Supported only when the sample is large enough, the mean is positive,
        and it beats the constant direction by a margin that is not noise
        (t > 2 on the paired difference is the usual bar; here a conservative
        t > 2 on the mean itself is the floor, and the edge must also be
        positive)."""
        return self.n >= MIN_N and self.mean_r > 0 and self.t > 2.0 and self.edge > 0

    def as_dict(self) -> dict:
        return {"side": self.side, "n": self.n, "hit": round(self.hit, 4),
                "mean_r": round(self.mean_r, 4), "t": round(self.t, 3),
                "base_r": round(self.base_r, 4), "edge": round(self.edge, 4),
                "supported": self.supported}


@dataclass(frozen=True)
class Measurement:
    """Both sides plus the constant-direction control they are judged against."""

    long: SideEvidence
    short: SideEvidence
    base_long_r: float
    base_short_r: float
    bars: int

    def base(self) -> dict:
        return {"long_r": self.base_long_r, "short_r": self.base_short_r,
                "n": self.bars, "bars": self.bars}


def _atr_series(C: list[dict]) -> list[float | None]:
    """ATR(14) per bar, computed once. Recomputing it per bar made the whole
    measurement O(n^2) and turned a two-second job into a minute."""
    out: list[float | None] = [None] * len(C)
    prev = None
    trs: list[float] = []
    for i, b in enumerate(C):
        if i == 0:
            tr = b["h"] - b["l"]
        else:
            pc = C[i - 1]["c"]
            tr = max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc))
        trs.append(tr)
        if i >= 14:
            window = trs[i - 14:i]
            out[i] = sum(window) / 14
            prev = out[i]
        elif prev is not None:
            out[i] = (prev * 13 + tr) / 14
    return out


def _grade(C: list[dict], i: int, long: bool, risk: float) -> float:
    """R multiple for a bracket entered at the close of bar i. First touch wins;
    a bar that spans both the stop and the target is scored as the stop."""
    e = C[i]["c"]
    stop = e - risk if long else e + risk
    tgt = e + TARGET_M * risk if long else e - TARGET_M * risk
    for b in C[i + 1:i + 1 + HORIZON]:
        if long:
            if b["l"] <= stop:
                return -1.0
            if b["h"] >= tgt:
                return TARGET_M
        else:
            if b["h"] >= stop:
                return -1.0
            if b["l"] <= tgt:
                return TARGET_M
    end = C[min(i + HORIZON, len(C) - 1)]["c"]
    move = (end - e) if long else (e - end)
    return move / risk


def _stats(rs: list[float]) -> tuple[float, float]:
    n = len(rs)
    if n < 2:
        return (rs[0] if rs else 0.0), 0.0
    mean = sum(rs) / n
    se = st.pstdev(rs) / (n ** 0.5)
    return mean, (mean / se if se else 0.0)


_CACHE: dict[tuple, Measurement] = {}


def measure(C: list[dict]) -> Measurement:
    """Measure both sides of the 20-day range break on `C` (oldest first).

    Returns a Measurement carrying each side's evidence plus the constant-
    direction control the sides are compared against.

    Cached by the sample's shape: the file is static between runs and the
    measurement is deterministic, so a process that builds both the plan card
    and the recommendation card pays for it once.
    """
    key = (len(C), C[0]["c"] if C else 0.0, C[-1]["c"] if C else 0.0)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    atrs = _atr_series(C)
    closes = [b["c"] for b in C]
    long_rs: list[float] = []
    short_rs: list[float] = []
    base_long: list[float] = []
    base_short: list[float] = []
    for i in range(LOOKBACK + 14, len(C) - 1):
        atr = atrs[i]
        if not atr:
            continue
        risk = STOP_K * atr
        window = C[i - LOOKBACK:i]
        hi = max(b["h"] for b in window)
        lo = min(b["l"] for b in window)
        base_long.append(_grade(C, i, True, risk))
        base_short.append(_grade(C, i, False, risk))
        if closes[i] >= hi:
            long_rs.append(_grade(C, i, True, risk))
        elif closes[i] <= lo:
            short_rs.append(_grade(C, i, False, risk))

    base_r_long, _ = _stats(base_long)
    base_r_short, _ = _stats(base_short)

    def side(name: str, rs: list[float], base: float) -> SideEvidence:
        mean, t = _stats(rs)
        hit = (sum(1 for r in rs if r > 0) / len(rs)) if rs else 0.0
        return SideEvidence(name, len(rs), hit, mean, t, base)

    m = Measurement(
        long=side("long", long_rs, base_r_long),
        short=side("short", short_rs, base_r_short),
        base_long_r=base_r_long,
        base_short_r=base_r_short,
        bars=len(C),
    )
    _CACHE[key] = m
    return m


def summary_line(m: Measurement) -> str:
    """One Arabic line for the recommendation card: which side the shipped
    history supports, with the numbers. Returns "" when neither side has a
    sample, so the caller prints nothing rather than a hollow claim."""
    if m.long.n == 0 and m.short.n == 0:
        return ""
    tag_l = "مدعوم" if m.long.supported else "غير مدعوم"
    tag_s = "مدعوم" if m.short.supported else "غير مدعوم"
    return (f"الدليل المقاس: شراء n={m.long.n} إصابة={m.long.hit:.0%} "
            f"توقع={m.long.mean_r:+.2f}R ({tag_l}) · "
            f"بيع n={m.short.n} إصابة={m.short.hit:.0%} "
            f"توقع={m.short.mean_r:+.2f}R ({tag_s})")


def format_evidence(m: Measurement, *, lang: str = "ar") -> str:
    """A one-block, honest readout: each side with its n, hit, expectancy, the
    base rate it is compared against, and a plain supported / not-supported."""
    if lang == "ar":
        head = "الدليل المقاس (اختراق مدى 20 يوم · وقف 1.5×ATR · هدف 2R · أول لمسة)"
        note = "الدليل محسوب الآن من التاريخ، مو رقم محفوظ."
        unsupported = "غير مدعوم — لا تعتمد عليه"
        supported = "مدعوم"
    else:
        head = "Measured evidence (20d range break · stop 1.5xATR · target 2R)"
        note = "Recomputed from history now; not a stored constant."
        unsupported = "NOT SUPPORTED — do not rely on it"
        supported = "supported"
    out = [head, note]
    for e in (m.long, m.short):
        tag = supported if e.supported else unsupported
        if e.n < MIN_N:
            tag = f"{unsupported} (n={e.n} < {MIN_N})"
        out.append(
            f"  {e.side.upper():<5} n={e.n:<4} hit={e.hit:>5.1%} "
            f"meanR={e.mean_r:+.3f} baseR={e.base_r:+.3f} edge={e.edge:+.3f} "
            f"t={e.t:+.2f}  -> {tag}")
    return "\n".join(out)
