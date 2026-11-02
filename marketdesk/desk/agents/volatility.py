"""Volatility agent — ATR and the day's range.

Two jobs. First, size: ATR per frame sets how far a stop has to sit to survive
noise. Second, regime: a very wide day or a very compressed one changes whether
a breakout should be trusted. The agent reports the numbers and the regime.
"""

from __future__ import annotations

from .. import indicators as ind
from .base import Agent, Study


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
        # Volatility is not directional; it moderates confidence elsewhere.
        score = 0.0
        if regime == "high":
            score = -0.2     # a wide tape punishes chasing
        facts = ("; ".join(f"{tf}: ATR {d.get('atr')} ({d.get('atr_pct')}%)"
                           for tf, d in per.items())
                 + f"; day change {day_pct}%; regime {regime}")
        summary = self.narrate(facts, "Summarise volatility and the regime.") or (
            f"Volatility regime: {regime}. {facts}")
        return Study(self.name, self.kind, "neutral", 0.5, score, summary,
                     {"frames": per, "regime": regime, "day_diff_pct": day_pct})
