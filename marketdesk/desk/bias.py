"""Bias audit — does the desk lean one way for structural reasons?

The desk produced 19 shorts and 1 long across 69 runs, which looks like a
structural bearish lean. It was not: gold fell from 4683 to 4065 over that
window, and the intraday frames were genuinely down. This module exists so that
claim can be *checked* rather than argued about.

It replays the desk's own decision rule -- the structure, trend and momentum
votes and their weights, combined with the same neutral band -- over the shipped
ten-year daily file, and reports the distribution of long / short / no-trade.
On a market that rose from 1302 to 3970, a structurally fair engine must lean
LONG. An engine that leans short here has a code asymmetry, not an opinion.

`balance()` is the check the test uses: it returns the long share, the short
share, and a verdict. The thresholds are loose on purpose -- the goal is to catch
a broken rule (0 bull vs 53 bear), not to force the desk to be bullish.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import history
from . import indicators as ind

WEIGHTS = {"structure": 2.0, "trend": 2.0, "momentum": 1.2}
NEUTRAL_BAND = 0.15
# A fair engine on a market that tripled must not lean short. If shorts beat
# longs here, a rule is broken; the exact split is not prescribed.
MIN_LONG_SHARE = 0.30
MAX_SHORT_SHARE = 0.45


@dataclass(frozen=True)
class Balance:
    long: int
    short: int
    no_trade: int
    bars: int
    votes: dict

    @property
    def long_share(self) -> float:
        return self.long / self.bars if self.bars else 0.0

    @property
    def short_share(self) -> float:
        return self.short / self.bars if self.bars else 0.0

    @property
    def no_trade_share(self) -> float:
        return self.no_trade / self.bars if self.bars else 0.0

    @property
    def balanced(self) -> bool:
        return (self.long_share >= MIN_LONG_SHARE
                and self.short_share <= MAX_SHORT_SHARE)

    def summary_ar(self) -> str:
        return (f"على {self.bars} شمعة يومية: شراء {self.long_share:.0%} · "
                f"بيع {self.short_share:.0%} · لا صفقة {self.no_trade_share:.0%}"
                + ("  ✅ متوازن" if self.balanced else "  ⚠️ انحياز بنيوي"))


def _votes(C: list[dict], i: int) -> dict:
    """The structure / trend / momentum votes at bar i, from bars 0..i only."""
    win = C[:i + 1]
    closes = [b["c"] for b in win]
    px = closes[-1]
    e20, e50 = ind.ema(closes, 20)[-1], ind.ema(closes, 50)[-1]
    st = ind.structure(win, wing=5)
    r = ind.rsi(closes, 14)
    trend = 1 if (e20 and e50 and px > e20 > e50) else (
        -1 if (e20 and e50 and px < e20 < e50) else 0)
    structure = 1 if st == "up" else (-1 if st == "down" else 0)
    momentum = 1 if (r and r > 55) else (-1 if (r and r < 45) else 0)
    return {"structure": structure, "trend": trend, "momentum": momentum}


def audit(C: list[dict], *, band: float = NEUTRAL_BAND) -> Balance:
    """Replay the decision rule over `C` and count the outcomes."""
    long = short = no_trade = 0
    tally: dict = {"structure": {1: 0, 0: 0, -1: 0}, "trend": {1: 0, 0: 0, -1: 0},
                   "momentum": {1: 0, 0: 0, -1: 0}}
    start = 60
    for i in range(start, len(C)):
        v = _votes(C, i)
        for k, val in v.items():
            tally[k][val] += 1
        num = sum(v[k] * WEIGHTS[k] for k in WEIGHTS)
        norm = num / sum(WEIGHTS.values())
        if norm > band:
            long += 1
        elif norm < -band:
            short += 1
        else:
            no_trade += 1
    bars = max(1, len(C) - start)
    return Balance(long, short, no_trade, bars, tally)


def audit_symbol(symbol: str = "XAUUSD", *, band: float = NEUTRAL_BAND) -> Balance:
    """Audit the shipped ten-year file for `symbol`."""
    return audit(history.shaped(symbol), band=band)


def render(b: Balance, symbol: str = "XAUUSD") -> str:
    L = [f"تدقيق الانحياز — {symbol}"]
    L.append(b.summary_ar())
    L.append("توزيع أصوات الوكلاء (صعودي/محايد/هبوطي):")
    for agent, t in b.votes.items():
        L.append(f"  {agent:<10} {t[1]:>5} / {t[0]:>5} / {t[-1]:>5}")
    return "\n".join(L)
