"""Assemble the read + the plan. Every level is tagged: pivot (printed by the
market) or projection (one ATR beyond the last real level)."""
from __future__ import annotations
import csv, json, urllib.request

UA = "Mozilla/5.0 (compatible; AnalysisDesk/1.0)"

def load(tf):
    return [{"t": r["openTime"], "o": float(r["open"]), "h": float(r["high"]),
             "l": float(r["low"]), "c": float(r["close"]), "tv": float(r["tickVolume"] or 0)}
            for r in csv.DictReader(open(f"XAUUSD_{tf}.csv"))]

def atr(rows, n=14):
    trs = [max(rows[i]["h"]-rows[i]["l"], abs(rows[i]["h"]-rows[i-1]["c"]),
               abs(rows[i]["l"]-rows[i-1]["c"])) for i in range(1, len(rows))]
    a = sum(trs[:n])/n
    for tr in trs[n:]: a = (a*(n-1)+tr)/n
    return a

h1, h4, d1 = load("1h"), load("4h"), load("1d")
px = h1[-1]["c"]
a1, a4, ad = atr(h1), atr(h4), atr(d1)

# Live broker quote.
try:
    req = urllib.request.Request("https://biquote.io/api/XAUUSD", headers={"User-Agent": UA})
    q = json.loads(urllib.request.urlopen(req, timeout=15).read())
except Exception:
    q = {}

# Key levels with provenance.
sup = [
    (3964.44, "pivot", "61.8% retracement of the 2025-04→2026-01 bull run (2956.3→5595.4)"),
    (3955.87, "pivot", "today's 4h low (02 Nov 16:00)"),
    (3934.83, "pivot", "today's session low (02 Nov 14:00 4h)"),
    (3891.03, "pivot", "today's early low (02 Nov 08:00 1h)"),
    (3870.05, "pivot", "the swing low (30 Oct 16:00 4h)"),
]
res = [
    (3970.46, "pivot", "today's high (02 Nov 16:00)"),
    (3988.36, "pivot", "23.6% of the 4082.99→3870.05 leg"),
    (4004.20, "pivot", "25 Oct high / 1h pivot"),
    (4022.48, "pivot", "26 Oct 1h pivot"),
    (4034.24, "pivot", "23 Oct high / 1h pivot"),
    (4082.99, "pivot", "28 Oct high — the leg's origin"),
]

report = {
    "symbol": "XAUUSD", "source": "biquote (MT5 broker feed)",
    "price": px, "live": {"mid": q.get("mid"), "marketState": q.get("marketState"),
                          "stale": q.get("stale"), "quoteAgeSeconds": q.get("quoteAgeSeconds"),
                          "dayDiffPercent": q.get("dayDiffPercent")},
    "atr": {"1h": round(a1,2), "4h": round(a4,2), "1d": round(ad,2)},
    "supports": [{"level": l, "kind": k, "why": w} for l, k, w in sup],
    "resistances": [{"level": l, "kind": k, "why": w} for l, k, w in res],
}
json.dump(report, open("XAUUSD_report.json","w"), indent=2, ensure_ascii=False)
print(json.dumps(report, indent=2, ensure_ascii=False))
