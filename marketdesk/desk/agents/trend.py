"""Trend agent — multi-timeframe alignment.

The plain question: do the frames agree? Each frame is scored by price vs its
EMA20/EMA50 stack, then the frames are combined with a halving weight per step
down, so a clean daily cannot be outvoted by three choppy intraday frames.
"""

from __future__ import annotations

from .. import arabic as ar
from .. import indicators as ind
from .base import Agent, Study

# Halve the weight per step down from the daily.
WEIGHT = {"1d": 8, "4h": 4, "1h": 2, "30m": 1, "15m": 0.5, "5m": 0.25, "1m": 0.1}


class TrendAgent(Agent):
    name = "trend"
    kind = "trend"
    weight = 2.0

    def study(self, run_id: str) -> Study:
        data = self.frames_data()
        per, tally, wtot = {}, 0.0, 0.0
        for tf in ("1d", "4h", "1h", "30m", "15m", "5m", "1m"):
            bars = data.get(tf) or []
            w = WEIGHT[tf]
            if len(bars) < 55:
                per[tf] = {"vote": 0, "note": "too short for EMA50"}
                wtot += w
                continue
            closes = [b["c"] for b in bars]
            e20, e50 = ind.ema(closes, 20)[-1], ind.ema(closes, 50)[-1]
            e20_slope = ind.slope(ind.ema(closes, 20), 6)
            px = closes[-1]
            vote = 0
            if e20 and e50:
                if px > e20 > e50:
                    vote = 1
                elif px < e20 < e50:
                    vote = -1
            # A rising EMA20 under price strengthens an up vote, and vice versa.
            if vote == 1 and e20_slope is not None and e20_slope > 0:
                vote = 1
            per[tf] = {"vote": vote, "px": px, "ema20": e20, "ema50": e50,
                       "ema20_slope": e20_slope}
            tally += vote * w
            wtot += w
        norm = max(-1.0, min(1.0, tally / wtot)) if wtot else 0.0
        stance = self.stance_of(norm)
        agree = sum(1 for d in per.values() if d.get("vote") == (1 if norm > 0 else -1))
        conf = min(1.0, abs(norm) + 0.1)
        facts = (f"المجموع المرجّح {tally:+.2f}/{wtot:.2f} (المعيار {norm:+.2f})؛ "
                 f"{agree} فريمات متوافقة؛ أصوات الفريمات "
                 + ", ".join(f"{tf}:{d.get('vote')}" for tf, d in per.items()))
        summary = self.narrate(facts, "لخّص الاتجاه على الفريمات.") or (
            f"الاتجاه {ar.stance(stance)}. {facts}")
        return Study(self.name, self.kind, stance, conf, norm, summary,
                     {"frames": per, "tally": tally, "normalised": norm})
