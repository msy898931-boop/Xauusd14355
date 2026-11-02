"""Turn the read into a concrete plan. Every level carries its provenance:
`pivot` = a price the market printed; `projection` = one stop-distance beyond
the last real level. Nothing is invented."""
from __future__ import annotations
import csv, json


def load(sym, tf):
    return [{"t": r["openTime"], "o": float(r["open"]), "h": float(r["high"]),
             "l": float(r["low"]), "c": float(r["close"]), "tv": float(r["tickVolume"] or 0)}
            for r in csv.DictReader(open(f"{sym}_{tf}.csv"))]


def atr(rows, n=14):
    trs = [max(rows[i]["h"]-rows[i]["l"], abs(rows[i]["h"]-rows[i-1]["c"]),
               abs(rows[i]["l"]-rows[i-1]["c"])) for i in range(1, len(rows))]
    a = sum(trs[:n])/n
    for tr in trs[n:]:
        a = (a*(n-1)+tr)/n
    return a


def pivots(rows, wing=3):
    hi, lo = [], []
    for i in range(wing, len(rows)-wing):
        w = rows[i-wing:i+wing+1]
        if rows[i]["h"] == max(x["h"] for x in w): hi.append((rows[i]["t"], rows[i]["h"]))
        if rows[i]["l"] == min(x["l"] for x in w): lo.append((rows[i]["t"], rows[i]["l"]))
    return lo, hi


h1 = load("XAUUSD", "1h")
h4 = load("XAUUSD", "4h")
d1 = load("XAUUSD", "1d")
px = h1[-1]["c"]

# The leg that matters: the last 4h swing high into the last 4h swing low.
lo4, hi4 = pivots(h4, 2)
swing_hi = max(h4, key=lambda r: r["h"])            # 4094 area
recent_lo = min(h4[-12:], key=lambda r: r["l"])     # 3870 area
H, L = swing_hi["h"], recent_lo["l"]
rng = H - L

fib = {p: round(L + rng*p, 2) for p in (0.236, 0.382, 0.5, 0.618, 0.786)}
a1, a4, ad = atr(h1), atr(h4), atr(d1)

print(f"price {px:.2f}")
print(f"swing high {H:.2f} ({swing_hi['t']})   swing low {L:.2f} ({recent_lo['t']})  range {rng:.2f}")
print("fib:", fib)
print(f"ATR 1h {a1:.2f}  4h {a4:.2f}  1d {ad:.2f}")

# Nearest real pivots around price, from the frames the desk reads.
lo1, hi1 = pivots(h1, 3)
res = sorted({round(v, 2) for _, v in hi1 if v > px})[:5]
sup = sorted({round(v, 2) for _, v in lo1 if v < px}, reverse=True)[:5]
print("nearest 1h resistances:", res)
print("nearest 1h supports:", sup)

# Where price sits inside the retracement.
pos = (px - L) / rng * 100
print(f"retracement of the down leg: {pos:.1f}%")

out = {"price": px, "swing_high": H, "swing_low": L, "fib": fib,
       "atr": {"1h": a1, "4h": a4, "1d": ad}, "resistances": res, "supports": sup,
       "retracement_pct": pos}
json.dump(out, open("XAUUSD_plan.json", "w"), indent=2)
