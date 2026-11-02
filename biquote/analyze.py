"""Read the pulled biquote candles and produce a market read + a plan.

Pure stdlib. Every number is computed from the CSVs pull.py wrote; nothing is
invented. An indicator that cannot be computed from the sample is reported as
None, never as 0.
"""
from __future__ import annotations

import csv
import json
import math
import sys

TFS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


def load(symbol: str, tf: str) -> list[dict]:
    rows = []
    with open(f"{symbol}_{tf}.csv") as fh:
        for r in csv.DictReader(fh):
            rows.append({
                "t": r["openTime"],
                "o": float(r["open"]), "h": float(r["high"]),
                "l": float(r["low"]), "c": float(r["close"]),
                "tv": float(r["tickVolume"] or 0),
                "open": r["isOpen"] == "True",
            })
    return rows


def ema(vals: list[float], n: int) -> list[float | None]:
    if len(vals) < n:
        return [None] * len(vals)
    k = 2 / (n + 1)
    out: list[float | None] = [None] * (n - 1)
    seed = sum(vals[:n]) / n
    out.append(seed)
    for v in vals[n:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def atr(rows: list[dict], n: int = 14) -> float | None:
    if len(rows) <= n:
        return None
    trs = []
    for i in range(1, len(rows)):
        h, l, pc = rows[i]["h"], rows[i]["l"], rows[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def macd(closes: list[float]) -> tuple[float | None, float | None]:
    e12, e26 = ema(closes, 12), ema(closes, 26)
    if e12[-1] is None or e26[-1] is None:
        return None, None
    line = e12[-1] - e26[-1]
    hist = []
    for a, b in zip(e12[26:], e26[26:]):
        if a is not None and b is not None:
            hist.append(a - b)
    if not hist:
        return line, None
    sig = ema(hist, 9)[-1]
    return line, (line - sig if sig is not None else None)


def pivots(rows: list[dict], wing: int = 3) -> tuple[list[float], list[float]]:
    hi, lo = [], []
    for i in range(wing, len(rows) - wing):
        win = rows[i - wing:i + wing + 1]
        if rows[i]["h"] == max(x["h"] for x in win):
            hi.append(rows[i]["h"])
        if rows[i]["l"] == min(x["l"] for x in win):
            lo.append(rows[i]["l"])
    return lo, hi


def swings(rows: list[dict], wing: int = 5) -> str:
    lo, hi = pivots(rows, wing)
    if len(lo) < 2 or len(hi) < 2:
        return "غير محدد"
    hh, hl = hi[-1] > hi[-2], lo[-1] > lo[-2]
    lh, ll = hi[-1] < hi[-2], lo[-1] < lo[-2]
    if hh and hl:
        return "صاعد (HH/HL)"
    if lh and ll:
        return "هابط (LH/LL)"
    return "عرضي"


def analyse(symbol: str, tf: str) -> dict:
    rows = load(symbol, tf)
    closes = [r["c"] for r in rows]
    bad = [r for r in rows if not (r["l"] <= min(r["o"], r["c"]) and max(r["o"], r["c"]) <= r["h"])]
    e20, e50, e200 = ema(closes, 20), ema(closes, 50), ema(closes, 200)
    lo, hi = pivots(rows, 3)
    return {
        "tf": tf, "n": len(rows), "first": rows[0]["t"], "last": rows[-1]["t"],
        "last_price": closes[-1], "open_first": closes[0],
        "chg_pct": (closes[-1] / closes[0] - 1) * 100,
        "high": max(r["h"] for r in rows), "low": min(r["l"] for r in rows),
        "ema20": e20[-1], "ema50": e50[-1], "ema200": e200[-1],
        "rsi": rsi(closes), "atr": atr(rows),
        "macd": macd(closes), "structure": swings(rows),
        # Levels relative to the last price: supports are pivots BELOW it
        # (nearest first), resistances pivots ABOVE it (nearest first).
        "supports": sorted({round(x, 2) for x in lo if x < closes[-1]}, reverse=True)[:4],
        "resistances": sorted({round(x, 2) for x in hi if x > closes[-1]})[:4],
        "ohlc_ok": not bad,
    }


def fmt(v, nd=2):
    return "—" if v is None else f"{v:,.{nd}f}"


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "XAUUSD"
    out = []
    print(f"== {symbol} — biquote (MT5) ==")
    for tf in TFS:
        a = analyse(symbol, tf)
        out.append(a)
        print(f"\n[{tf}] {a['n']} شمعة  {a['first']} → {a['last']}   OHLC صالح: {a['ohlc_ok']}")
        print(f"     آخر إغلاق {fmt(a['last_price'],3)} | تغير {a['chg_pct']:+.2f}% | مدى {fmt(a['low'],2)}–{fmt(a['high'],2)}")
        print(f"     EMA20 {fmt(a['ema20'])} EMA50 {fmt(a['ema50'])} EMA200 {fmt(a['ema200'])} | RSI {fmt(a['rsi'],1)} | ATR {fmt(a['atr'],2)}")
        ml, mh = a["macd"]
        print(f"     MACD {fmt(ml,3)} / hist {fmt(mh,3)} | بنية: {a['structure']}")
        print(f"     دعم {a['supports']} | مقاومة {a['resistances']}")
    with open(f"{symbol}_analysis.json", "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
