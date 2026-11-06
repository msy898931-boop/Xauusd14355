"""Regime agent — whether there is a trend, and whether the tape is tradeable.

Every other specialist answers "which way". None answers "is there anything here
to trade". That gap is measurable: on the desk's own ten-year daily file, three
standard methods (EMA20/50, 60-day momentum, 20-day breakout) each beat "always
long" on the same bars in the LOWEST and HIGHEST volatility thirds, and each
LOST to it in the middle third (EMA20/50 -0.176R, t=-2.88; 60-day momentum
-0.234R, t=-3.69). The middle third is where a chop eats a breakout's edge.

So this agent reports two numbers that no directional agent reports:

* ADX -- trend STRENGTH. A 20-day breakout returned +0.318R when ADX was below
  25 and +0.042R (t=0.35, indistinguishable from noise) when it was above. The
  desk's breakout logic is at its weakest exactly when a trend is strongest.
* the ATR percentile -- where today's volatility sits in the instrument's own
  250-day range. A fixed dollar threshold cannot answer this: gold's daily ATR
  has tripled over the sample, so "40 dollars" means high now and low then.

It casts NO directional vote (weight 0.0, and `regime` is excluded from the
tally alongside risk and volatility). This is deliberate. The finding above is
a pattern in one instrument over one decade, and the desk's own rule is that a
weight is earned by a measured edge, not awarded by a hypothesis. The agent
publishes the measurement; the scoreboard decides whether it ever votes.
"""

from __future__ import annotations

from .. import arabic as ar
from .. import indicators as ind
from .base import Agent, Study

# The two readings are taken on the frame that sets the day's tone. ADX needs
# ~30 bars to be honest, so the daily is the natural home; the 4h is the
# fallback when the daily history is short.
FRAMES = ("1d", "4h")

# Wilder's own bands. Kept as constants so the stance and the prose can never
# disagree about what "trending" means.
TREND_AT = 25.0
RANGE_AT = 20.0


def trend_label(adx: float | None) -> str:
    """trending / ranging / unknown from the ADX reading."""
    if adx is None:
        return "unknown"
    if adx >= TREND_AT:
        return "trending"
    if adx <= RANGE_AT:
        return "ranging"
    return "normal"


def vol_label(percentile: float | None) -> str:
    """low / normal / high from the ATR percentile. The thirds are the bands the
    measurement above used, so the label and the evidence share a definition."""
    if percentile is None:
        return "unknown"
    if percentile < 0.33:
        return "low"
    if percentile >= 0.66:
        return "high"
    return "normal"


def _num(v, nd: int = 1) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


class RegimeAgent(Agent):
    name = "regime"
    kind = "regime"
    # No vote until the pattern is confirmed on live outcomes. See the module
    # docstring: a hypothesis earns a weight, it is not given one.
    weight = 0.0
    frames = FRAMES

    def study(self, run_id: str) -> Study:
        data = self.frames_data()
        per: dict[str, dict] = {}
        for tf in FRAMES:
            bars = data.get(tf) or []
            if len(bars) < 30:
                per[tf] = {"note": "too short"}
                continue
            per[tf] = {
                "adx": ind.adx(bars),
                "vol_percentile": ind.atr_percentile(bars),
                "bars": len(bars),
            }

        # The daily leads; the 4h answers only if the daily is unreadable.
        lead_tf = next((tf for tf in FRAMES
                        if (per.get(tf) or {}).get("adx") is not None), None)
        adx = (per.get(lead_tf) or {}).get("adx") if lead_tf else None
        pct = (per.get(lead_tf) or {}).get("vol_percentile") if lead_tf else None
        trend = trend_label(adx)
        vol = vol_label(pct)

        # Not directional: the score is 0 and the stance is neutral by contract.
        # The orchestrator excludes this kind from the tally anyway; both guards
        # are kept so a future refactor cannot quietly turn it into a bias.
        score = 0.0
        stance = "neutral"
        conf = 0.5

        frame_txt = "، ".join(
            f"{tf}: ADX {_num((per.get(tf) or {}).get('adx'))}"
            for tf in FRAMES if (per.get(tf) or {}).get("adx") is not None)
        facts = (f"{frame_txt}؛ الترتيب المئوي للتذبذب "
                 f"{_num((pct or 0) * 100 if pct is not None else None, 0)}%؛ "
                 f"الاتجاه {ar.regime(trend)}، التذبذب {ar.regime(vol)}")
        summary = self.narrate(facts, "لخّص نظام السوق: هل في اتجاه، وكيف التذبذب.") or (
            f"نظام السوق: {ar.regime(trend)} باتجاه "
            f"{'قوي' if trend == 'trending' else 'ضعيف'}، وتذبذب {ar.regime(vol)}. {facts}")

        return Study(self.name, self.kind, stance, conf, score, summary,
                     {"frames": per, "lead_frame": lead_tf, "adx": adx,
                      "vol_percentile": pct, "trend": trend, "vol": vol})
