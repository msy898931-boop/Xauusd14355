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
        # relationship is still reported as context; the *score*, however, is
        # driven by whichever peer actually co-moves (measured below).
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
        # The driver is chosen by MEASURED co-movement, not assumed. It used to
        # be hardcoded to USDJPY, which on this feed correlates with gold at
        # rho ~ 0.00 -- so the agent's only scored branch never fired and it
        # published "neutral" on all 69 runs. The strongest peer above the noise
        # floor leads; its sign decides the direction and its |rho| the size.
        driver_sym = None
        driver_rho = None
        for sym, rho in sorted(rhos.items(), key=lambda kv: -abs(kv[1])):
            if abs(rho) >= _RHO_FLOOR:
                driver_sym, driver_rho = sym, rho
                break
        if driver_sym is not None and driver_rho is not None:
            peer_chg = (peers.get(driver_sym) or {}).get("day_diff_pct")
            if isinstance(peer_chg, (int, float)) and peer_chg != 0:
                # gold moves WITH a positive-rho peer and AGAINST a negative one
                direction = 1.0 if peer_chg > 0 else -1.0
                sign = 1.0 if driver_rho > 0 else -1.0
                score = max(-1.0, min(1.0, direction * sign * abs(driver_rho)))

        adv, dec = breadth.get("advancers"), breadth.get("decliners")
        tone = "neutral"
        if adv is not None and dec is not None:
            tone = "risk-on" if adv > dec * 1.2 else ("risk-off" if dec > adv * 1.2 else "mixed")

        stance = self.stance_of(score)
        conf = 0.5 if driver_rho is None else min(1.0, 0.3 + abs(driver_rho))
        peers_txt = "، ".join(
            f"{k} {_pct(v.get('day_diff_pct'))}" for k, v in peers.items()) or "—"
        driver_txt = (f"{driver_sym} (rho={round(driver_rho, 3)})"
                      if driver_sym is not None and driver_rho is not None
                      else "لا محرّك فوق حد الضجيج")
        facts = (f"الذهب اليوم {_pct(own_chg)}؛ المحرّك المقيس {driver_txt}؛ "
                 f"الأقران {peers_txt}؛ "
                 f"الاتساع صاعد {_n(adv)} / هابط {_n(dec)} ({ar.t(tone, ar.TONE)})")
        note = self.narrate(facts, "لخّص سياق الأسواق المقابلة للذهب.") or (
            f"قراءة الأسواق المقابلة: المحرّك {driver_txt}؛ "
            f"اللوح {ar.t(tone, ar.TONE)}. {facts}")
        return Study(self.name, self.kind, stance, conf, score, note,
                     {"peers": peers, "driver": driver_sym, "driver_rho": driver_rho,
                      "macro": macro, "breadth": breadth, "tone": tone,
                      "correlations": rhos})
