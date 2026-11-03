"""Correlation agent — is gold moving on its own, or is the whole board moving?

Reads peer symbols' daily change and the market breadth summary, then measures
how gold's *daily returns* actually co-move with each peer over a shared window
(Pearson correlation of close-to-close returns). The score is scaled by that
measured co-movement, so a peer that has never tracked gold cannot move the desk
by an arbitrary fixed amount. The daily-change comparison survives as the
plain-language driver note, not as the number.
"""

from __future__ import annotations

from .. import arabic as ar
from .. import sources
from .base import Agent, Study

# Correlation below this is noise, not a relationship worth weighting.
_RHO_FLOOR = 0.2


def _returns(bars: list[dict]) -> list[float]:
    """Close-to-close returns. Accepts either a shared-store row (`close`) or a
    reshaped bar (`c`)."""
    raw = [b.get("c", b.get("close")) for b in bars]
    closes = [float(x) for x in raw if x is not None]
    return [(closes[i] / closes[i - 1] - 1.0)
            for i in range(1, len(closes)) if closes[i - 1]]


def pearson(a: list[float], b: list[float]) -> float | None:
    """Pearson correlation of two equal-length series, or None if it cannot be
    measured (too short, or one side is flat). None is not 0."""
    n = min(len(a), len(b))
    if n < 10:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / (va ** 0.5 * vb ** 0.5)


def _n(v) -> str:
    """A count, or a dash -- never the word "None"."""
    return "—" if v is None else str(v)


def _pct(v) -> str:
    """A percent, or a dash -- never the word "None"."""
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return str(v)


class CorrelationAgent(Agent):
    name = "correlation"
    kind = "cross_market"
    weight = 0.7

    def _peer_returns(self, sym: str) -> list[float]:
        try:
            bars = sources.fetch_candles(sym, "1h", limit=200)
        except sources.SourceError:
            return []
        return _returns(sorted(bars, key=lambda b: b["openTime"]))

    def study(self, run_id: str) -> Study:
        peers = {}
        for sym in self.settings.peers:
            try:
                q = sources.fetch_quote(sym)
                peers[sym] = {"mid": q["mid"], "day_diff_pct": q["day_diff_pct"],
                              "market_state": q["market_state"]}
            except sources.SourceError as exc:
                peers[sym] = {"error": str(exc)[:120]}
        try:
            summary = sources.fetch_summary()
            breadth = summary.get("overview", {})
        except sources.SourceError:
            breadth = {}

        own = self.quote() or {}
        own_chg = own.get("day_diff_pct")

        # Measured co-movement, not a fixed award. The desk's own hourly closes
        # against each peer's, over the same window.
        own_ret = _returns(sorted(
            self.shared.load_candles(self.settings.symbol, "1h"),
            key=lambda b: b["open_time"]))
        rhos: dict[str, float] = {}
        for sym in self.settings.peers:
            pr = self._peer_returns(sym)
            rho = pearson(own_ret, pr) if own_ret and pr else None
            if rho is not None:
                peers[sym]["rho_1h"] = round(rho, 3)
                rhos[sym] = rho

        # The dollar is the usual driver of gold, so the sign of the USDJPY
        # relationship sets the direction; the size is the measured correlation.
        usdjpy_rho = rhos.get("USDJPY")
        usdjpy_chg = (peers.get("USDJPY") or {}).get("day_diff_pct")
        macro = "unclear"
        if own_chg is not None and usdjpy_chg is not None:
            if own_chg > 0 and usdjpy_chg < 0:
                macro = "gold-led (weak dollar)"
            elif own_chg < 0 and usdjpy_chg > 0:
                macro = "dollar-led (gold pressured)"
            else:
                macro = "mixed"

        score = 0.0
        # driver_rho is a measured float or None; the score is scaled by it, so a
        # peer that has never tracked gold contributes nothing rather than a fixed
        # award for "the directions differed".
        driver_rho = abs(usdjpy_rho) if usdjpy_rho is not None else None
        if usdjpy_rho is not None and driver_rho is not None and driver_rho >= _RHO_FLOOR:
            if macro == "gold-led (weak dollar)":
                score = min(1.0, max(0.0, -usdjpy_rho))
            elif macro == "dollar-led (gold pressured)":
                score = -min(1.0, max(0.0, -usdjpy_rho))

        adv, dec = breadth.get("advancers"), breadth.get("decliners")
        tone = "neutral"
        if adv is not None and dec is not None:
            tone = "risk-on" if adv > dec * 1.2 else ("risk-off" if dec > adv * 1.2 else "mixed")

        stance = self.stance_of(score)
        conf = 0.5 if driver_rho is None else min(1.0, 0.3 + driver_rho)
        peers_txt = "، ".join(
            f"{k} {_pct(v.get('day_diff_pct'))}" for k, v in peers.items()) or "—"
        facts = (f"الذهب اليوم {_pct(own_chg)}؛ ارتباط الساعة المقيس مع USDJPY "
                 f"{round(usdjpy_rho, 3) if usdjpy_rho is not None else 'غير قابل للقياس'}؛ "
                 f"الأقران {peers_txt}؛ "
                 f"المحرّك {ar.t(macro, ar.DRIVER)}؛ الاتساع صاعد {_n(adv)} / "
                 f"هابط {_n(dec)} ({ar.t(tone, ar.TONE)})")
        note = self.narrate(facts, "لخّص سياق الأسواق المقابلة للذهب.") or (
            f"قراءة الأسواق المقابلة: {ar.t(macro, ar.DRIVER)}؛ "
            f"اللوح {ar.t(tone, ar.TONE)}. {facts}")
        return Study(self.name, self.kind, stance, conf, score, note,
                     {"peers": peers, "driver": macro, "breadth": breadth, "tone": tone,
                      "correlations": rhos})
