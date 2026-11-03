"""Structure agent — price action / SMC.

Reads market structure (higher-highs & higher-lows vs lower-highs & lower-lows)
on the coarse frames, finds the last break of structure, and locates the price
inside the current dealing range (premium vs discount). This is the agent that
decides *which side* the desk is allowed to trade, not the entry.
"""

from __future__ import annotations

from .. import arabic as ar
from .. import indicators as ind
from .base import Agent, Study


class StructureAgent(Agent):
    name = "structure"
    kind = "price_action"
    weight = 2.0
    frames = ("1d", "4h", "1h", "15m")

    def study(self, run_id: str) -> Study:
        data = self.frames_data()
        per: dict[str, dict] = {}
        votes = []
        for tf, bars in data.items():
            if len(bars) < 20:
                per[tf] = {"structure": "unknown", "note": "sample too short"}
                continue
            st = ind.structure(bars, wing=5)
            lo, hi = ind.pivots(bars, 3)
            closes = [b["c"] for b in bars]
            px = closes[-1]
            rng_hi, rng_lo = max(b["h"] for b in bars), min(b["l"] for b in bars)
            pos = (px - rng_lo) / (rng_hi - rng_lo) if rng_hi > rng_lo else 0.5
            zone = "premium" if pos > 0.6 else ("discount" if pos < 0.4 else "equilibrium")
            # A break of structure: the last close beyond the prior swing extreme.
            bos = "none"
            if len(hi) >= 2 and px > hi[-2]:
                bos = "bullish BOS"
            elif len(lo) >= 2 and px < lo[-2]:
                bos = "bearish BOS"
            per[tf] = {"structure": st, "zone": zone, "position": round(pos, 2),
                       "bos": bos, "swing_high": rng_hi, "swing_low": rng_lo}
            votes.append(1 if st == "up" else (-1 if st == "down" else 0))

        # Coarse frames carry the bias: weight the daily and 4h more.
        wmap = {"1d": 4, "4h": 3, "1h": 2, "15m": 1}
        score = sum(wmap.get(tf, 1) * v for tf, v in zip(per, votes, strict=False)) or 0
        total = sum(wmap.get(tf, 1) for tf in per) or 1
        norm = max(-1.0, min(1.0, score / total))
        stance = self.stance_of(norm)
        conf = min(1.0, abs(norm) + 0.2)

        facts = "; ".join(
            f"{tf}: {ar.structure(d.get('structure'))} / "
            f"{ar.structure(d.get('zone'))} / {ar.bos(d.get('bos'))}"
            for tf, d in per.items())
        summary = self.narrate(facts, "لخّص بنية السوق والميل.") or (
            f"البنية {ar.stance(stance)}. " + facts)
        return Study(self.name, self.kind, stance, conf, norm, summary, {"frames": per})
