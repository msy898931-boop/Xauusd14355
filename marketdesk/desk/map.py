"""The daily map: where to buy, where to sell, and what the ground looks like.

The owner asked for the battlefield in one page -- buy zones, sell zones,
liquidity, break/retrace levels, supply and demand, structural support and
resistance, and the general trend. Most of that is already computed: the
structural levels are `indicators.pivots`, the trend is `indicators.structure`,
and the break/retrace levels are what `scenarios.py` already builds.

Two honest notes, because this module exists to be read and not to sell:

  * "Supply and demand" and "support and resistance" are the SAME prices. A
    cluster of swing highs is resistance from below and supply from above. This
    module does not compute them twice and present them as two findings; it
    clusters the pivots into zones and labels each zone by where it sits
    relative to the live price.

  * "Manipulation" is not predictable and is not predicted here. What can be
    measured is WHERE STOPS SIT -- repeated highs and lows within a fraction of a
    percent, which is where resting orders cluster. That is `liquidity`, and it is
    a measurement, not a story. Calling it manipulation would be a claim the data
    does not support.

Every number is a price the market actually printed or a level derived from one.
Nothing is forecast. The map is context, not a signal: it shows the ground, it
does not promise the desk's record improves (it is 0/16 -- the map does not
change that).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import arabic as ar
from . import indicators as ind

# Frames the map reads, coarse to fine. The daily and 4h set the zones that
# matter for a swing; the 15m and 1h give the near levels a trader acts on today.
MAP_FRAMES = ("15m", "1h", "4h", "1d")

# Two pivots within this fraction of price are the same shelf, not two levels.
# 0.25% of 4,100 is ~10 points -- about one 15m ATR, so anything closer than that
# cannot be traded apart and belongs in one zone.
CLUSTER_TOL = 0.0025

# Repeated highs/lows within this fraction are "equal" and mark resting stops.
EQUAL_TOL = 0.0012

# Round numbers are magnets; only the ones near the price are worth printing.
ROUND_STEP = 50.0

# A zone needs at least this many touches to be worth showing. One swing high is
# a line; three is a shelf. Below the bar the map is noise, which is the failure
# mode this whole module has to avoid.
MIN_TOUCHES = 2


@dataclass
class Zone:
    low: float
    high: float
    touches: int
    frames: list[str] = field(default_factory=list)

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    def label(self) -> str:
        if self.high - self.low < 0.5:
            return f"{self.mid:,.1f}"
        return f"{self.low:,.1f} – {self.high:,.1f}"


@dataclass
class Liquidity:
    price: float
    side: str          # "above" (buy-side stops) | "below" (sell-side stops)
    frame: str


@dataclass
class Map:
    symbol: str
    live: float
    trend: dict[str, str]
    zones: list[Zone]
    liquidity: list[Liquidity]
    rounds: list[float]
    atr: dict[str, float]

    def _split(self) -> tuple[list[Zone], list[Zone]]:
        """Zones above the price (sell into) and below it (buy from), nearest
        first. The nearest zone on each side is the one a trade would be built
        around today; the rest are context."""
        above = sorted((z for z in self.zones if z.low > self.live),
                       key=lambda z: z.low)
        below = sorted((z for z in self.zones if z.high < self.live),
                       key=lambda z: -z.high)
        return above, below


def _ohlc(bars: list[dict]) -> list[dict]:
    """Reshape shared-memory candles into the shape `indicators` reads.

    Shared memory stores open/high/low/close/is_open; the indicator helpers read
    the one-letter keys. `is_open` bars are already excluded by the caller, so
    every bar here is closed and its high/low is a fact.
    """
    return [{"o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"]}
            for b in bars if not b.get("is_open")]


def _cluster(points: list[tuple[float, str]], tol: float) -> list[Zone]:
    """Group prices that are within `tol` of each other into zones.

    Sorted by price, a zone grows while the next price is within `tol` of the
    LAST one added -- a chained cluster, so a run of prices each just inside the
    band forms one zone rather than many.
    """
    if not points:
        return []
    pts = sorted(points)
    out: list[Zone] = []
    cur: list[tuple[float, str]] = [pts[0]]
    for price, tf in pts[1:]:
        if price - cur[-1][0] <= tol:
            cur.append((price, tf))
        else:
            out.append(_zone(cur))
            cur = [(price, tf)]
    out.append(_zone(cur))
    return [z for z in out if z.touches >= MIN_TOUCHES]


def _zone(pts: list[tuple[float, str]]) -> Zone:
    prices = [p for p, _ in pts]
    frames = sorted({tf for _, tf in pts})
    return Zone(low=min(prices), high=max(prices), touches=len(prices),
                frames=frames)


def _liquidity(bars_by_frame: dict[str, list[dict]], live: float,
               tol: float) -> list[Liquidity]:
    """Repeated highs and lows: where resting stops cluster.

    A high printed twice within `tol` is a level the market defended twice, and
    stops sit just beyond it. Above the price these are buy-side stops (shorts'
    stops and breakout buys); below it, sell-side. Only the nearest few are kept
    -- a list of every equal high is a list nobody reads.
    """
    out: list[Liquidity] = []
    for tf in MAP_FRAMES:
        bars = bars_by_frame.get(tf) or []
        if len(bars) < 10:
            continue
        lo, hi = ind.pivots(bars, wing=3)
        for arr in (hi, lo):
            for i in range(len(arr)):
                for j in range(i + 1, len(arr)):
                    if abs(arr[i] - arr[j]) <= tol:
                        out.append(Liquidity(price=(arr[i] + arr[j]) / 2,
                                             side="", frame=tf))
    # Collapse near-duplicates from overlapping pairs, keeping the highest count
    # by price band, then keep the nearest on each side.
    out.sort(key=lambda x: x.price)
    merged: list[Liquidity] = []
    for liq in out:
        if merged and abs(liq.price - merged[-1].price) <= tol:
            continue
        merged.append(liq)
    above = [x for x in merged if x.price > live][:2]
    below = [x for x in merged if x.price < live][-2:]
    # `side` is where the level sits relative to the PRICE, not whether it was a
    # swing high or low. Reading it off the pivot kind mislabelled every level
    # the price had since moved through: a swing low now above the price was
    # reported as "below", and a high now below it as "above" -- 3 of 4 levels
    # were on the wrong side. The filter below is positional; the label must be
    # too, or the map tells the reader the opposite of what it measured.
    for x in above:
        x.side = "above"
    for x in below:
        x.side = "below"
    return sorted(below + above, key=lambda x: x.price)


def _touches(n: int) -> str:
    """Arabic counts: 1 لمسة, 2 لمستان, 3-10 لمسات, 11+ لمسة."""
    if n == 1:
        return "لمسة"
    if n == 2:
        return "لمستان"
    if 3 <= n <= 10:
        return f"{n} لمسات"
    return f"{n} لمسة"


def _rounds(live: float, span: float) -> list[float]:
    """Round numbers within `span` of the price, nearest first."""
    base = int(live // ROUND_STEP) * ROUND_STEP
    out = []
    for k in range(-2, 4):
        x = base + k * ROUND_STEP
        if x > 0 and abs(x - live) <= span:
            out.append(float(x))
    return sorted(out, key=lambda x: abs(x - live))


def build(bars_by_frame: dict[str, list[dict]], live: float, *,
          symbol: str = "XAUUSD") -> Map:
    """Compute the map from closed frames. Pure: no network, no model, no store.

    `bars_by_frame` maps a frame name to its closed candles (shared-memory shape).
    A missing or short frame simply contributes nothing, so the map degrades
    rather than raising -- the same rule `scenarios.build` follows.
    """
    frames = {tf: _ohlc(bars_by_frame.get(tf) or []) for tf in MAP_FRAMES}

    points: list[tuple[float, str]] = []
    for tf, bars in frames.items():
        if len(bars) < 10:
            continue
        lo, hi = ind.pivots(bars, wing=3)
        points += [(p, tf) for p in lo[-8:]]
        points += [(p, tf) for p in hi[-8:]]

    zones = _cluster(points, live * CLUSTER_TOL)

    trend = {tf: ind.structure(frames[tf], wing=5) for tf in MAP_FRAMES
             if len(frames[tf]) >= 12}
    atr = {}
    for tf in MAP_FRAMES:
        a = ind.atr(frames[tf], 14) if len(frames[tf]) >= 15 else None
        if a:
            atr[tf] = a

    return Map(symbol=symbol, live=live, trend=trend, zones=zones,
               liquidity=_liquidity(frames, live, live * EQUAL_TOL),
               rounds=_rounds(live, span=(atr.get("1d") or 60.0)),
               atr=atr)


def render(m: Map) -> str:
    """Telegram-ready HTML. Every number is one the map computed."""
    above, below = m._split()
    lines = [f"🗺️ <b>خريطة اليوم</b> — {m.symbol}",
             f"السعر <b>{m.live:,.2f}</b>"]

    if m.trend:
        order = ("1d", "4h", "1h", "15m")
        parts = [f"{tf}: {ar.structure(m.trend[tf])}"
                 for tf in order if tf in m.trend]
        lines.append("الاتجاه — " + " · ".join(parts))
    lines.append("")

    lines.append("🔴 <b>مناطق البيع</b> (عرض فوق السعر):")
    if above:
        for z in above[:3]:
            frames = "،".join(z.frames)
            lines.append(f"   <b>{z.label()}</b> — {_touches(z.touches)} ({frames})")
    else:
        lines.append("   ما في منطقة مطبوعة فوق السعر.")
    lines.append("")

    lines.append("🟢 <b>مناطق الشراء</b> (طلب تحت السعر):")
    if below:
        for z in below[:3]:
            frames = "،".join(z.frames)
            lines.append(f"   <b>{z.label()}</b> — {_touches(z.touches)} ({frames})")
    else:
        lines.append("   ما في منطقة مطبوعة تحت السعر.")
    lines.append("")

    if m.liquidity:
        up = [x for x in m.liquidity if x.side == "above"]
        dn = [x for x in m.liquidity if x.side == "below"]
        bits = []
        if dn:
            bits.append("تحت: " + "، ".join(f"{x.price:,.1f}" for x in dn))
        if up:
            bits.append("فوق: " + "، ".join(f"{x.price:,.1f}" for x in up))
        lines.append("💧 <b>سيولة</b> (تجمّع وقفات) — " + " · ".join(bits))

    if m.rounds:
        lines.append("🧲 <b>أرقام مستديرة</b> — "
                     + " · ".join(f"{x:,.0f}" for x in m.rounds))

    if m.atr:
        order = ("1d", "4h", "1h", "15m")
        atr = " · ".join(f"{tf} {m.atr[tf]:,.1f}" for tf in order if tf in m.atr)
        lines.append(f"📏 <b>مدى الحركة (ATR)</b> — {atr}")

    lines.append("")
    lines.append("<i>الخريطة سياق — وين الأرض، مو إشارة ولا وعد. "
                 "ما بتغيّر سجل المكتب.</i>")
    return "\n".join(lines)
