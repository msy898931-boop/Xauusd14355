"""Levels agent — supports, resistances and Fibonacci.

Every level it prints carries its provenance. `pivot` means the market actually
printed that price (a swing point in the shared candles). `fib` means it is a
retracement of a measured leg. `projection` means it is one ATR beyond the last
real level. A level that is not anchored to the live price is not a level.
"""

from __future__ import annotations

from .. import indicators as ind
from .base import Agent, Study

FIB_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)


class LevelsAgent(Agent):
    name = "levels"
    kind = "levels"
    weight = 1.5
    frames = ("1d", "4h", "1h", "15m")

    def study(self, run_id: str) -> Study:
        data = self.frames_data()
        px = self.live_price()
        if px is None:
            return Study(self.name, self.kind, "neutral", 0.0, 0.0,
                         "لا يوجد سعر متاح — ما في مستويات.", {})

        supports, resistances = [], []
        for tf, bars in data.items():
            if len(bars) < 20:
                continue
            lo, hi = ind.pivots(bars, 3)
            for v in lo:
                if v < px:
                    supports.append({"level": round(v, 2), "kind": "pivot", "tf": tf})
            for v in hi:
                if v > px:
                    resistances.append({"level": round(v, 2), "kind": "pivot", "tf": tf})

        # Fibonacci of the most recent measured leg on the 4h frame.
        fibs = []
        h4 = data.get("4h") or []
        if len(h4) >= 20:
            window = h4[-20:]
            lo_bar = min(window, key=lambda b: b["l"])
            before = [b for b in window if b["t"] <= lo_bar["t"]]
            if before:
                hi_bar = max(before, key=lambda b: b["h"])
                H, L = hi_bar["h"], lo_bar["l"]
                for r in FIB_RATIOS:
                    lvl = ind.fib_retracement(H, L, r)
                    if lvl > px:
                        resistances.append({"level": round(lvl, 2), "kind": "fib", "tf": "4h"})
                    elif lvl < px:
                        supports.append({"level": round(lvl, 2), "kind": "fib", "tf": "4h"})
                    fibs.append({"ratio": r, "level": round(lvl, 2)})

        # Dedupe by level, keep the nearest few each side.
        def dedupe(rows, reverse):
            seen, out = set(), []
            for r in sorted(rows, key=lambda x: x["level"], reverse=reverse):
                key = round(r["level"], 1)
                if key in seen:
                    continue
                seen.add(key)
                out.append(r)
                if len(out) >= 6:
                    break
            return out

        supports = dedupe(supports, reverse=True)
        resistances = dedupe(resistances, reverse=False)

        nearest_sup = supports[0]["level"] if supports else None
        nearest_res = resistances[0]["level"] if resistances else None
        # Closer to support than resistance -> the near-term tilt is up.
        score = 0.0
        if nearest_sup and nearest_res and px:
            up_room = nearest_res - px
            down_room = px - nearest_sup
            if up_room + down_room > 0:
                score = max(-1.0, min(1.0, (up_room - down_room) / (up_room + down_room)))
        stance = self.stance_of(score)
        fib_txt = ", ".join(
            f"{f.get('ratio')} عند {f.get('level')}"
            for f in (fibs or [])[:5]) or "—"
        facts = (f"السعر {px}؛ أقرب دعم {nearest_sup}؛ أقرب مقاومة "
                 f"{nearest_res}؛ فيبوناتشي {fib_txt}")
        summary = self.narrate(facts, "لخّص المستويات المهمة حول السعر.") or (
            f"أقرب دعم {nearest_sup}، أقرب مقاومة {nearest_res}. {facts}")
        return Study(self.name, self.kind, stance, 0.6, score, summary,
                     {"price": px, "supports": supports, "resistances": resistances, "fibs": fibs})
