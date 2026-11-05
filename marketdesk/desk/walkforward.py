"""Walk-forward validation — does the desk survive a window it has never seen?

The backtest grades the desk on the whole file at once. That is a start, but a
rule can look good on the whole file and be an artefact of one regime: the ten
years here contain a tripling, so any long-leaning rule scores well on the
average and says nothing about the next window.

This splits the file into consecutive folds and, for each, measures the desk's
rule on a *train* window and then on the *test* window that follows it. A rule
that only works in train is a rule that fit the past. The headline is the gap
between the two, and the comparison against the three things the desk has to beat:

* **buy & hold** — the honest benchmark. If the desk cannot beat holding gold,
  it has no reason to exist as a timing tool.
* **always long / always short** — the constant-direction controls.
* **random** — a seeded coin, so a "win" cannot be luck dressed as skill.

NO LOOK-AHEAD. The rule at bar i reads bars 0..i only (the same `_rule_norm` the
calibration uses), the entry is that bar's close, and the outcome is the bar
`horizon` later. `lookahead_check` asserts the property directly: appending bars
must not change any earlier decision.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import history
from . import indicators as ind
from .calibration import _rule_norm

HORIZON = 20
STOP_K = 1.5
TARGET_M = 2.0
MIN_BARS = 120
DEADBAND_PCT = 0.05


def _grade(C: list[dict], i: int, long: bool) -> float | None:
    """R multiple for a bracket entered at the close of bar i, first touch wins,
    an ambiguous bar takes the stop -- the same ruler as scan and evidence."""
    atr = ind.atr(C[:i + 1])
    if not atr:
        return None
    risk = STOP_K * atr
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
    return ((end - e) if long else (e - end)) / risk


def _stat(rs: list[float]) -> tuple[int, float, float]:
    n = len(rs)
    if n < 2:
        return n, (rs[0] if rs else 0.0), 0.0
    m = sum(rs) / n
    var = sum((x - m) ** 2 for x in rs) / (n - 1)
    se = (var / n) ** 0.5
    return n, m, (m / se if se else 0.0)


def _rule_calls(C: list[dict], lo: int, hi: int, band: float = 0.15) -> list[tuple[int, bool]]:
    """Every bar in [lo, hi) where the desk leans, with the side it leans to."""
    out = []
    for i in range(max(lo, MIN_BARS), hi):
        norm = _rule_norm(C, i, band)
        if abs(norm) <= band:
            continue
        out.append((i, norm > 0))
    return out


def _expectancy(C: list[dict], calls: list[tuple[int, bool]]) -> tuple[int, float, float]:
    rs = [r for i, long in calls if (r := _grade(C, i, long)) is not None]
    return _stat(rs)


def _hold(C: list[dict], lo: int, hi: int) -> float:
    lo, hi = max(lo, MIN_BARS), min(hi, len(C) - 1)
    if hi <= lo:
        return 0.0
    return (C[hi]["c"] / C[lo]["c"] - 1) * 100


@dataclass
class Fold:
    train: tuple[int, int]
    test: tuple[int, int]
    train_n: int
    train_r: float
    train_t: float
    test_n: int
    test_r: float
    test_t: float
    hold_pct: float

    @property
    def holds(self) -> bool:
        return self.train_r > 0 and self.test_r > 0

    @property
    def decay(self) -> float:
        return self.test_r - self.train_r


@dataclass
class WalkForward:
    folds: list[Fold] = field(default_factory=list)
    horizon: int = HORIZON

    @property
    def train_r(self) -> float:
        return sum(f.train_r * f.train_n for f in self.folds) / max(1, sum(f.train_n for f in self.folds))

    @property
    def test_r(self) -> float:
        return sum(f.test_r * f.test_n for f in self.folds) / max(1, sum(f.test_n for f in self.folds))

    @property
    def test_n(self) -> int:
        return sum(f.test_n for f in self.folds)

    @property
    def decay(self) -> float:
        """How much of the train edge survives out of sample."""
        return self.test_r - self.train_r


def walk_forward(C: list[dict], *, folds: int = 4, band: float = 0.15) -> WalkForward:
    """Consecutive folds; each trains on the window before it and tests on the
    window that follows. No shuffle: time order is the point."""
    wf = WalkForward()
    n = len(C)
    span = n // (folds + 1)
    if span < 60:
        return wf
    for k in range(folds):
        tr_lo, tr_hi = k * span, (k + 1) * span
        te_lo, te_hi = tr_hi, min(n - 1, tr_hi + span)
        tr = _rule_calls(C, tr_lo, tr_hi, band)
        te = _rule_calls(C, te_lo, te_hi, band)
        tn, trr, trt = _expectancy(C, tr)
        en, err, ert = _expectancy(C, te)
        wf.folds.append(Fold((tr_lo, tr_hi), (te_lo, te_hi), tn, trr, trt,
                             en, err, ert, _hold(C, te_lo, te_hi)))
    return wf


def controls(C: list[dict], *, windows: list[tuple[int, int]] | None = None,
             seed: int = 7) -> dict[str, tuple[int, float, float]]:
    """The bars the desk must beat, graded on the SAME windows as the desk.

    Comparing the desk's out-of-sample windows against a control measured over
    the whole file is not a fair fight -- the control would get the calm years the
    desk was not allowed to see. So the controls are graded on exactly the test
    windows, bar for bar.
    """
    if not windows:
        windows = [(MIN_BARS, len(C) - 1)]
    rng = random.Random(seed)
    idx: list[int] = []
    for lo, hi in windows:
        idx.extend(range(max(lo, MIN_BARS), min(hi, len(C) - 1)))
    out = {}
    for label, side in (("always long", True), ("always short", False)):
        rs = [r for i in idx if (r := _grade(C, i, side)) is not None]
        out[label] = _stat(rs)
    rs = [r for i in idx if (r := _grade(C, i, rng.random() < 0.5)) is not None]
    out["random"] = _stat(rs)
    return out


def paired_vs_long(C: list[dict], windows: list[tuple[int, int]]) -> tuple[int, float, float]:
    """Paired difference: the desk's call minus always-long, on the same bars.

    Both sides are graded on every bar the desk called, so the difference removes
    the market's drift and asks the only question that matters -- did the desk's
    *timing* add anything over just being long? Returns n, mean difference in R,
    and its t. A t near zero means the desk is riding the drift, not timing it.
    """
    diffs = []
    for lo, hi in windows:
        for i, long in _rule_calls(C, lo, hi):
            a = _grade(C, i, long)
            b = _grade(C, i, True)
            if a is not None and b is not None:
                diffs.append(a - b)
    return _stat(diffs)


def lookahead_check(C: list[dict], *, band: float = 0.15, cut: int | None = None) -> bool:
    """True when no earlier decision changes after later bars are appended.

    This is the property the whole module rests on. If the rule at bar i could
    see bar i+1, every number here would be fiction."""
    cut = cut or int(len(C) * 0.7)
    short = C[:cut]
    a = _rule_calls(short, 0, len(short), band)
    b = [(i, long) for i, long in _rule_calls(C, 0, cut, band)]
    return a == b


def render(wf: WalkForward, ctrl: dict, *, symbol: str = "XAUUSD",
           hold_pct: float | None = None,
           paired: tuple[int, float, float] | None = None) -> str:
    L = [f"التحقق المتدرّج (walk-forward) — {symbol} · أفق {wf.horizon} يوم"]
    if not wf.folds:
        L.append("التاريخ أقصر من أن يتجزّأ.")
        return "\n".join(L)
    L.append("النافذة   تدريب(n/R)      اختبار(n/R)     تدهور   شراء-ومسك")
    for k, f in enumerate(wf.folds, 1):
        L.append(f"  {k}     {f.train_n:>4} / {f.train_r:+.3f}R   "
                 f"{f.test_n:>4} / {f.test_r:+.3f}R   "
                 f"{f.decay:+.3f}   {f.hold_pct:+.1f}%")
    L.append(f"الإجمالي: تدريب {wf.train_r:+.3f}R → اختبار {wf.test_r:+.3f}R "
             f"(تدهور {wf.decay:+.3f}R على {wf.test_n} صفقة)")
    L.append("")
    L.append("المقارنة على نفس نوافذ الاختبار (الحد الذي يجب تجاوزه):")
    for label, (n, m, t) in ctrl.items():
        L.append(f"  {label:<12} n={n:>4}  {m:+.3f}R  t={t:+.2f}")
    if hold_pct is not None:
        L.append(f"  {'buy & hold':<12} {'':>4}  {hold_pct:+.1f}% على المدة")
    L.append("")
    # The honest test is the PAIRED difference: same bars, desk minus always-long.
    # Comparing raw expectancies lets the market's drift flatter any long-leaning
    # rule. Only the paired number says whether the timing added anything.
    if paired is not None:
        pn, pm, pt = paired
        if abs(pt) < 2.0:
            L.append(f"مقابل الشراء الدائم على نفس الشموع: {pm:+.3f}R "
                     f"(t={pt:+.2f}, n={pn}) → ما في تفوّق توقيت معنوي. "
                     "المكتب بيمتطي الميل، مو بيتوقّتو.")
        else:
            L.append(f"مقابل الشراء الدائم على نفس الشموع: {pm:+.3f}R "
                     f"(t={pt:+.2f}, n={pn}) → تفوّق توقيت معنوي.")
    beats = wf.test_r > ctrl.get("always long", (0, 0.0, 0))[1]
    L.append("الحكم: " + ("المكتب يتفوّق على الشراء الدائم خارج العينة."
                          if beats else
                          "المكتب ما بيتفوّق على الشراء الدائم خارج العينة — "
                          "ما عندو edge توقيت، وبيقولها بصراحة."))
    return "\n".join(L)


def run_walkforward(symbol: str = "XAUUSD", *, folds: int = 4) -> str:
    C = history.shaped(symbol)
    wf = walk_forward(C, folds=folds)
    windows = [f.test for f in wf.folds] or [(MIN_BARS, len(C) - 1)]
    ctrl = controls(C, windows=windows)
    paired = paired_vs_long(C, windows)
    return render(wf, ctrl, symbol=symbol, hold_pct=_hold(C, MIN_BARS, len(C) - 1),
                  paired=paired)
