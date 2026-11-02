"""Risk agent — the gate. It does not pick a direction; it can veto one.

Checks the conditions under which the desk must not trade at all: the market is
closed or the quote is stale, an ATR says the tape is too wild for the stated
risk, or a high-impact event is imminent. This is the fail-closed specialist:
when it says no, the orchestrator may not issue a live entry.
"""

from __future__ import annotations

from .. import indicators as ind
from .base import Agent, Study

# The currency whose high-impact events move each instrument. Gold and silver
# are quoted in dollars; the FX majors are read off their first leg.
_CURRENCY = {
    "XAUUSD": "USD", "XAGUSD": "USD", "BTCUSD": "USD", "USOIL": "USD",
    "EURUSD": "EUR", "GBPUSD": "GBP", "USDJPY": "USD", "AUDUSD": "AUD",
}


def _base_currency(symbol: str) -> str:
    """The currency an event must concern to close the gate for `symbol`."""
    return _CURRENCY.get(symbol.upper(), symbol.upper()[:3])


class RiskAgent(Agent):
    name = "risk"
    kind = "risk"
    weight = 1.0
    frames = ("1d", "4h", "1h")

    def study(self, run_id: str) -> Study:
        q = self.quote() or {}
        data = self.frames_data()
        blockers = []
        warnings = []

        if not q:
            blockers.append("no live quote")
        else:
            if q.get("market_state") and q["market_state"] != "open":
                blockers.append(f"market {q['market_state']}")
            if q.get("stale"):
                blockers.append("quote is stale")
            age = q.get("quote_age_s")
            if isinstance(age, int) and age > 300:
                blockers.append(f"quote {age}s old")

        # Imminent high-impact event, read from what the news agent published.
        # Two refinements over a flat "any high-impact event" rule:
        #   * the event must be inside the configured window, and
        #   * it must concern the traded instrument's currency. A JOLTS release
        #     matters to gold (USD); an RBA decision does not.
        base_ccy = _base_currency(self.settings.symbol)
        news_study = next((st for st in self.shared.load_studies(run_id)
                           if st["agent"] == "news"), None)
        if news_study:
            payload = news_study["payload"]
            imminent = payload.get("imminent_events")
            if imminent is None:                      # older payload shape
                imminent = ([{"name": "high-impact event"}]
                            if payload.get("event_risk_24h") else [])
            relevant = [e for e in imminent
                        if not e.get("currency") or e.get("currency") == base_ccy]
            if relevant:
                names = ", ".join(e.get("name", "?") for e in relevant[:3])
                blockers.append(f"high-impact {base_ccy} event within "
                                f"{self.settings.event_window_h:g}h ({names})")
            elif imminent:
                warnings.append("a high-impact event is near but not for this "
                                "instrument's currency")

        h1 = data.get("1h") or []
        a = ind.atr(h1) if len(h1) > 20 else None
        px = self.live_price()
        atr_pct = (a / px * 100) if a and px else None
        if atr_pct is not None and atr_pct > 3.0:
            warnings.append(f"1h ATR {atr_pct:.1f}% is very high — widen stops or stand aside")

        allowed = not blockers
        # Risk is a gate: when it blocks, the score is neutral and confidence is
        # in the *decision*, not a direction.
        summary = ("Trading allowed. " + ("; ".join(warnings) if warnings else "No blocking conditions.")
                   ) if allowed else ("Trading blocked: " + "; ".join(blockers))
        return Study(self.name, self.kind, "neutral", 1.0, 0.0, summary,
                     {"allowed": allowed, "blockers": blockers, "warnings": warnings,
                      "atr_pct_1h": atr_pct})
