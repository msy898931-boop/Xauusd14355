"""Momentum agent — RSI and MACD.

Momentum tells the desk *when* a move is stretched. Overbought in an uptrend is
not a sell; it is a warning not to chase. This agent reports the stretch, not a
direction, and lets the orchestrator weigh it.
"""

from __future__ import annotations

from .. import arabic as ar
from .. import indicators as ind
from .base import Agent, Study


def _num(v) -> str:
    """A missing or zero reading prints as a dash, not as the word "None"."""
    if v is None:
        return "—"
    try:
        return f"{float(v):.1f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(v)


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
        facts = ("; ".join(
            f"{tf}: RSI {_num(d.get('rsi'))}، "
            f"هيستوغرام MACD {_num(d.get('macd_hist'))}"
            for tf, d in per.items())
            + f"؛ تشبّع شرائي على {'، '.join(overbought) or 'لا فريم'}؛ "
              f"تشبّع بيعي على {'، '.join(oversold) or 'لا فريم'}")
        summary = self.narrate(facts, "لخّص الزخم وهل السعر ممدود.") or (
            f"الزخم {ar.stance(stance)}. {facts}")
        return Study(self.name, self.kind, stance, min(1.0, abs(norm) + 0.2), norm,
                     summary, {"frames": per, "overbought": overbought, "oversold": oversold})
