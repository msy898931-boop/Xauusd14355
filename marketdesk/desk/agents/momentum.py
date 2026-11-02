"""Momentum agent — RSI and MACD.

Momentum tells the desk *when* a move is stretched. Overbought in an uptrend is
not a sell; it is a warning not to chase. This agent reports the stretch, not a
direction, and lets the orchestrator weigh it.
"""

from __future__ import annotations

from .. import indicators as ind
from .base import Agent, Study


class MomentumAgent(Agent):
    name = "momentum"
    kind = "momentum"
    weight = 1.2
    frames = ("1d", "4h", "1h", "30m", "15m")

    def study(self, run_id: str) -> Study:
        data = self.frames_data()
        per: dict[str, dict] = {}
        votes: list[int] = []
        for tf, bars in data.items():
            closes = [b["c"] for b in bars]
            if len(closes) < 35:
                per[tf] = {"note": "too short"}
                continue
            r = ind.rsi(closes)
            line, sig, hist = ind.macd(closes)
            vote = 0
            if r is not None:
                if r > 70:
                    vote -= 1     # overbought: momentum stretched, do not chase
                elif r < 30:
                    vote += 1     # oversold: stretched the other way
                elif r > 50:
                    vote += 1
                else:
                    vote -= 1
            if hist is not None:
                vote += 1 if hist > 0 else -1
            per[tf] = {"rsi": r, "macd_hist": hist, "macd_line": line, "vote": vote}
            votes.append(vote)
        avg = sum(votes) / len(votes) if votes else 0.0
        norm = max(-1.0, min(1.0, avg / 2))
        stance = self.stance_of(norm)
        overbought = [tf for tf, d in per.items() if (d.get("rsi") or 0) > 70]
        oversold = [tf for tf, d in per.items() if 0 < (d.get("rsi") or 100) < 30]
        facts = ("; ".join(f"{tf}: RSI {d.get('rsi') and round(d['rsi'],1)}, "
                           f"MACDhist {d.get('macd_hist') and round(d['macd_hist'],2)}"
                           for tf, d in per.items())
                 + f"; overbought on {overbought or 'none'}; oversold on {oversold or 'none'}")
        summary = self.narrate(facts, "Summarise momentum and any stretch.") or (
            f"Momentum is {stance}. {facts}")
        return Study(self.name, self.kind, stance, min(1.0, abs(norm) + 0.2), norm,
                     summary, {"frames": per, "overbought": overbought, "oversold": oversold})
