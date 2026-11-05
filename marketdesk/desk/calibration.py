"""Calibration — is "confidence 70%" actually right 70% of the time?

Every agent prints a confidence, and the desk prints a weighted score. Neither
number means anything until it is checked against outcomes. A desk that says
"0.8" and is right 55% of the time is not a confident desk; it is a miscalibrated
one, and a trader who sizes on that number is mis-sizing.

This module builds the curve two ways:

* `from_history(C)` replays the desk's own decision rule over the shipped
  ten-year file and asks, for each band of conviction, how often the market moved
  the way the desk leaned. It needs no live runs and cannot be gamed by a lucky
  week.
* `from_outcomes(rows)` uses the runs that were actually graded, reading the
  bias score the desk recorded even when it declined to trade.

Both reduce to the same object: a list of bands, each with its average predicted
probability, its realised hit rate, and its sample size. The headline number is
the expected calibration error -- the sample-weighted average gap between the
two. Ten points is the desk's own bar: above that, the confidence is a decoration.

Nothing here adjusts the weights. The plan allows tuning only once a sample is
large (MIN_FOR_TUNING); below that, a "fix" would be fitting noise. This module's
job is to tell the truth about the confidence, not to bend it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import history
from . import indicators as ind

# The bands a conviction is sorted into, as predicted probabilities. A desk score
# is a signed lean in [-1, 1]; it is read as a probability by 0.5 + 0.5 * score,
# which is the only mapping that puts "flat" at a coin flip.
EDGES = (0.5, 0.6, 0.7, 0.8, 1.01)
# Below this many graded outcomes, no weight may be touched. The plan's own floor.
MIN_FOR_TUNING = 100
# The desk's acceptance bar, in percentage points of calibration error.
MAX_ECE_PCT = 10.0
DEADBAND_PCT = 0.05


@dataclass
class Band:
    lo: float
    hi: float
    n: int = 0
    correct: int = 0
    pred_sum: float = 0.0

    @property
    def realised(self) -> float | None:
        return self.correct / self.n if self.n else None

    @property
    def predicted(self) -> float | None:
        return self.pred_sum / self.n if self.n else None

    @property
    def label(self) -> str:
        return f"{self.lo:.0%}–{self.hi:.0%}"


@dataclass
class Calibration:
    bands: list[Band] = field(default_factory=list)
    source: str = ""

    @property
    def n(self) -> int:
        return sum(b.n for b in self.bands)

    @property
    def correct(self) -> int:
        return sum(b.correct for b in self.bands)

    @property
    def ece(self) -> float | None:
        """Expected calibration error, as a fraction. None when nothing graded."""
        if not self.n:
            return None
        return sum(b.n * abs((b.realised or 0.0) - (b.predicted or 0.0))
                   for b in self.bands) / self.n

    @property
    def enough_to_tune(self) -> bool:
        return self.n >= MIN_FOR_TUNING

    @property
    def calibrated(self) -> bool:
        e = self.ece
        return e is not None and e * 100 <= MAX_ECE_PCT


def _band_of(p: float, bands: list[Band]) -> Band | None:
    for b in bands:
        if b.lo <= p < b.hi:
            return b
    return None


def _fresh_bands() -> list[Band]:
    return [Band(EDGES[i], EDGES[i + 1]) for i in range(len(EDGES) - 1)]


def from_outcomes(rows: list[dict]) -> Calibration:
    """Calibrate from graded runs. A run with no directional call is skipped, and
    so is a move inside the deadband -- neither is a scorable claim."""
    bands = _fresh_bands()
    cal = Calibration(bands=bands, source="graded runs")
    for r in rows:
        correct = r.get("correct")
        if correct is None:
            continue
        score = r.get("bias_score")
        if score is None:
            continue
        move = r.get("move_pct")
        if move is not None and abs(move) < DEADBAND_PCT:
            continue
        p = 0.5 + 0.5 * max(-1.0, min(1.0, float(score)))
        b = _band_of(p, bands)
        if b is None:
            continue
        b.n += 1
        b.correct += int(correct)
        b.pred_sum += p
    return cal


def _rule_norm(C: list[dict], i: int, band: float) -> float:
    """The desk's own blended lean at bar i, from bars 0..i only."""
    win = C[:i + 1]
    closes = [b["c"] for b in win]
    px = closes[-1]
    e20, e50 = ind.ema(closes, 20)[-1], ind.ema(closes, 50)[-1]
    st = ind.structure(win, wing=5)
    r = ind.rsi(closes, 14)
    trend = 1 if (e20 and e50 and px > e20 > e50) else (
        -1 if (e20 and e50 and px < e20 < e50) else 0)
    structure = 1 if st == "up" else (-1 if st == "down" else 0)
    momentum = 1 if (r and r > 55) else (-1 if (r and r < 45) else 0)
    return (2.0 * structure + 2.0 * trend + 1.2 * momentum) / 5.2


def from_history(C: list[dict], *, horizon: int = 20, band: float = 0.15) -> Calibration:
    """Calibrate the desk's rule against what the next `horizon` days did.

    The claim being checked is the desk's *implied* bias, not its verdict: it
    leans up or down on most bars even when it declines to trade, and those leans
    are what a trader would size on.
    """
    bands = _fresh_bands()
    cal = Calibration(bands=bands, source=f"history ({len(C)} bars, {horizon}d)")
    start = 60
    for i in range(start, len(C) - horizon):
        norm = _rule_norm(C, i, band)
        if abs(norm) <= band:
            continue                      # no lean: nothing to score
        entry = C[i]["c"]
        exit_ = C[i + horizon]["c"]
        move = (exit_ / entry - 1) * 100
        if abs(move) < DEADBAND_PCT:
            continue
        up = move > 0
        correct = int(up == (norm > 0))
        p = 0.5 + 0.5 * abs(norm)
        b = _band_of(p, bands)
        if b is None:
            continue
        b.n += 1
        b.correct += correct
        b.pred_sum += p
    return cal


def from_symbol(symbol: str = "XAUUSD", *, horizon: int = 20,
                band: float = 0.15) -> Calibration:
    """Calibrate the desk's rule on the shipped ten-year file for `symbol`."""
    return from_history(history.shaped(symbol), horizon=horizon, band=band)


_RELIABILITY: dict[tuple, Calibration] = {}


def _reliability_for(symbol: str) -> Calibration:
    C = history.shaped(symbol)
    k = (symbol, len(C), C[-1]["c"] if C else 0.0)
    hit = _RELIABILITY.get(k)
    if hit is None:
        hit = from_history(C)
        _RELIABILITY[k] = hit
    return hit


def reliability(symbol: str, score: float) -> tuple[float, int] | None:
    """The measured hit rate for a lean of `score`, and the sample behind it.

    This is the honest reading of the desk's own conviction. The desk prints a
    blended score whose nominal probability is 0.5 + 0.5 * |score|; measured over
    ten years that number is wildly optimistic (a nominal 92% was right 55.6% of
    the time). This returns what the band actually delivered, so the card can say
    both. None when nothing is graded.
    """
    cal = _reliability_for(symbol)
    p = 0.5 + 0.5 * abs(max(-1.0, min(1.0, score)))
    for b in cal.bands:
        if b.lo <= p < b.hi:
            if b.n and b.realised is not None:
                return b.realised, b.n
            return None
    return None


def render(cal: Calibration, lang: str = "ar") -> str:
    L = [f"منحنى المعايرة — {cal.source}" if lang == "ar" else f"Calibration — {cal.source}"]
    if not cal.n:
        L.append("لا عيّنة مُقيَّمة بعد — ما في منحنى." if lang == "ar"
                 else "no graded sample yet — no curve.")
        return "\n".join(L)
    L.append("النطاق      عيّنة   متوقّع   فعلي    الفرق")
    for b in cal.bands:
        if not b.n:
            continue
        pred, real = b.predicted or 0.0, b.realised or 0.0
        L.append(f"{b.label:>10}  {b.n:>5}   {pred:>6.1%}  {real:>6.1%}  "
                 f"{(real - pred) * 100:>+5.1f}نق")
    e = cal.ece or 0.0
    L.append(f"إجمالي {cal.n} · خطأ المعايرة {e * 100:.1f} نقطة "
             f"(الحد {MAX_ECE_PCT:.0f}) → "
             + ("مُعايَر ✅" if cal.calibrated else "غير مُعايَر ⚠️"))
    L.append("العيّنة تكفي لتعديل الأوزان." if cal.enough_to_tune
             else f"العيّنة {cal.n} < {MIN_FOR_TUNING} — ممنوع تعديل الأوزان "
                  "(منع الإفراط في التوفيق).")
    return "\n".join(L)
