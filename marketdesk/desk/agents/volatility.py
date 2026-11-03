"""Volatility agent — ATR and the day's range.

Two jobs. First, size: ATR per frame sets how far a stop has to sit to survive
noise. Second, regime: a very wide day or a very compressed one changes whether
a breakout should be trusted. The agent reports the numbers and the regime.
"""

from __future__ import annotations

from .. import arabic as ar
from .. import indicators as ind
from .base import Agent, Study


def _num(v) -> str:
    """A missing value reads as a dash, never as the string "None"."""
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(v)


class VolatilityAgent(Agent):
    name = "volatility"
    kind = "volatility"
    weight = 0.8
    frames = ("1d", "4h", "1h", "15m")

    def study(self, run_id: str) -> Study:
        data = self.frames_data()
        per: dict[str, dict] = {}
        for tf, bars in data.items():
            if len(bars) < 20:
                per[tf] = {"note": "too short"}
                continue
            a = ind.atr(bars)
            closes = [b["c"] for b in bars]
            recent = bars[-20:]
            avg_range = sum(b["h"] - b["l"] for b in recent) / len(recent)
            a_pct = (a / closes[-1] * 100) if a and closes[-1] else None
            per[tf] = {"atr": round(a, 2) if a else None,
                       "atr_pct": round(a_pct, 2) if a_pct else None,
                       "avg_range_20": round(avg_range, 2)}
        q = self.quote() or {}
        day_pct = q.get("day_diff_pct")
        # Regime from the daily ATR as a share of price: a crude but honest band.
        d = per.get("1d", {})
        regime = "unknown"
        if d.get("atr_pct") is not None:
            regime = ("high" if d["atr_pct"] > 2.0
                      else "normal" if d["atr_pct"] > 0.8 else "low")
        # Volatility is not directional, so it casts NO directional vote. It used
        # to return -0.2 in a high-volatility regime -- a one-sided bearish vote
        # from an agent that says in the same breath that it is not directional.
        # It entered the tally at weight 0.8, and the backtest showed the effect:
        # the agent was bear 46% of the time and bull never. The regime is in the
        # payload, where the risk gate and the stop sizing already read it.
        score = 0.0
        facts = ("; ".join(
            f"{tf}: ATR {_num(d.get('atr'))} ({_num(d.get('atr_pct'))}%)"
            for tf, d in per.items())
            + f"؛ تغيّر اليوم {_num(day_pct)}%؛ النظام {ar.regime(regime)}")
        summary = self.narrate(facts, "لخّص التذبذب والنظام السائد.") or (
            f"نظام التذبذب: {ar.regime(regime)}. {facts}")
        return Study(self.name, self.kind, "neutral", 0.5, score, summary,
                     {"frames": per, "regime": regime, "day_diff_pct": day_pct})
