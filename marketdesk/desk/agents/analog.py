"""The analog agent — the desk's measured prior, instead of an opinion.

The other agents each hold one view (trend, momentum, structure). This one does
not have a view. It asks a factual question: the market is in some state right
now (see `state.py`); on the shipped ten-year history, every other time it was in
that state, what did the next move actually do?

For each side it reports, over those historical days:

* `n`        how many times the state occurred (the sample size)
* `hit`      how often the bracket reached its target before its stop
* `mean_r`   the expectancy in R, first touch wins, stop first on an ambiguous bar
* `base_r`   the same bracket on EVERY day, the constant-direction control
* `t`        mean_r / standard error -- how far the result is from zero
* `edge`     mean_r - base_r -- what the state adds over just being long/short

Two rules keep it from becoming a horoscope:

1. **It never reads past the bar it is judging.** The state at bar `i` is built
   from bars `0..i` only, so the outcome it is measured against cannot leak in.
2. **It says "no sample" rather than a number.** Below `MIN_N` bars the study is
   published as neutral with the count shown, and the card prints the count, not
   a conclusion. A 3-sample "edge" is not an edge.

The bracket is the desk's own: stop 1.5 x ATR, target 2R, 20-bar horizon. The
same ruler as `evidence.py` and `scan.py`, so no agent's number is measured with
a friendlier one.
"""

from __future__ import annotations

import statistics as st

from .. import history
from .. import state as state_mod
from .base import Agent, Study

STOP_K = 1.5
TARGET_M = 2.0
HORIZON = 20
MIN_N = 30          # below this the state is reported, not concluded from


def _bracket(C: list[dict], i: int, long: bool, risk: float) -> float:
    """R multiple for a bracket entered at the close of bar i, first touch wins,
    an ambiguous bar takes the stop."""
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


def _stats(rs: list[float]) -> tuple[float, float]:
    n = len(rs)
    if n < 2:
        return (rs[0] if rs else 0.0), 0.0
    mean = sum(rs) / n
    se = st.pstdev(rs) / (n ** 0.5)
    return mean, (mean / se if se else 0.0)


def _atr_at(C: list[dict], i: int) -> float | None:
    """ATR(14) at bar i, from bars up to i only."""
    from .. import indicators as ind
    return ind.atr(C[:i + 1])


def measure_state(C: list[dict], key: tuple, *, sides: tuple[str, ...] = ("long", "short")) -> dict:
    """Grade both sides on every historical day in `key`, plus the base rate.

    Returns a dict per side with n / hit / mean_r / t, and "base" with the
    constant-direction rate over the same days.
    """
    idx = state_mod.all_states(C).get(key, [])
    out: dict = {"n_state": len(idx)}
    base: dict[str, list[float]] = {s: [] for s in sides}
    per: dict[str, list[float]] = {s: [] for s in sides}
    for i in idx:
        atr = _atr_at(C, i)
        if not atr:
            continue
        risk = STOP_K * atr
        for s in sides:
            long = s == "long"
            base[s].append(_bracket(C, i, long, risk))
        # the trigger for the state's side: only count a bar that actually broke
        # its prior range on that side, the same trigger the plan uses.
        w = C[i - state_mod.RANGE_WINDOW:i]
        if not w:
            continue
        hi = max(b["h"] for b in w)
        lo = min(b["l"] for b in w)
        if C[i]["c"] >= hi:
            per["long"].append(_bracket(C, i, True, risk))
        elif C[i]["c"] <= lo:
            per["short"].append(_bracket(C, i, False, risk))

    for s in sides:
        mean, t = _stats(per[s])
        hit = (sum(1 for r in per[s] if r > 0) / len(per[s])) if per[s] else 0.0
        bmean, _ = _stats(base[s])
        out[s] = {"n": len(per[s]), "hit": hit, "mean_r": mean, "t": t,
                  "base_r": bmean, "edge": mean - bmean}
    return out


def _cache_key(C: list[dict], key: tuple) -> tuple:
    return (len(C), C[-1]["c"] if C else 0.0, key)


_CACHE: dict[tuple, dict] = {}


def measure_cached(C: list[dict], key: tuple) -> dict:
    k = _cache_key(C, key)
    hit = _CACHE.get(k)
    if hit is None:
        hit = measure_state(C, key)
        _CACHE[k] = hit
    return hit


class AnalogAgent(Agent):
    name = "analog"
    kind = "analog"
    weight = 1.4

    def study(self, run_id: str) -> Study:
        C = history.shaped(self.settings.symbol)
        if len(C) < state_mod.TREND_MA + 40:
            return Study(self.name, self.kind, "neutral", 0.0, 0.0,
                         "لا يوجد تاريخ كافٍ للاستدلال التاريخي.", {"n": 0})
        cur = state_mod.state_at(C, len(C) - 1)
        if cur is None:
            return Study(self.name, self.kind, "neutral", 0.0, 0.0,
                         "تعذّر تحديد حالة السوق الحالية.", {"n": 0})
        m = measure_cached(C, cur.key())
        long_s, short_s = m["long"], m["short"]
        n_state = m["n_state"]

        # The side with the larger positive edge above the sample floor leads.
        # Below the floor there is no call at all -- the state is reported with
        # its count, never dressed as a conclusion.
        score = 0.0
        if n_state >= MIN_N:
            if long_s["n"] >= MIN_N and long_s["edge"] > 0:
                score = min(1.0, max(0.0, long_s["edge"]))
            if short_s["n"] >= MIN_N and short_s["edge"] > 0:
                score = min(score, 0.0) - min(1.0, max(0.0, short_s["edge"]))
        stance = self.stance_of(score)
        conf = min(1.0, 0.2 + (n_state / 200.0)) if n_state >= MIN_N else 0.15

        label = cur.label_ar()
        facts = (f"حالة السوق: {label}؛ تكرّرت {n_state} مرة في عشر سنوات؛ "
                 f"الشراء n={long_s['n']} توقع={long_s['mean_r']:+.3f}R "
                 f"خطأ={long_s['base_r']:+.3f}R ميزة={long_s['edge']:+.3f}R t={long_s['t']:+.2f}؛ "
                 f"البيع n={short_s['n']} توقع={short_s['mean_r']:+.3f}R "
                 f"خطأ={short_s['base_r']:+.3f}R ميزة={short_s['edge']:+.3f}R t={short_s['t']:+.2f}")
        summary = self.narrate(facts, "لخّص ما حدث تاريخياً في هذه الحالة.") or (
            f"الحالة «{label}» تكرّرت {n_state} مرة. "
            f"الشراء توقع {long_s['mean_r']:+.2f}R (ميزة {long_s['edge']:+.2f}R)، "
            f"البيع توقع {short_s['mean_r']:+.2f}R (ميزة {short_s['edge']:+.2f}R). "
            + ("العيّنة كافية." if n_state >= MIN_N else f"العيّنة صغيرة ({n_state})."))
        payload = {"state": label, "state_key": list(cur.key()), "n_state": n_state,
                   "long": long_s, "short": short_s, "min_n": MIN_N,
                   "sufficient": n_state >= MIN_N}
        return Study(self.name, self.kind, stance, conf, score, summary, payload)
