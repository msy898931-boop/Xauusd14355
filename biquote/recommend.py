"""Multi-timeframe tally + a plan whose every level carries provenance."""
from __future__ import annotations
import csv, json, urllib.request

UA = "Mozilla/5.0 (compatible; AnalysisDesk/1.0)"

def load(tf):
    return [{"t": r["openTime"], "o": float(r["open"]), "h": float(r["high"]),
             "l": float(r["low"]), "c": float(r["close"])}
            for r in csv.DictReader(open(f"XAUUSD_{tf}.csv"))]

def ema_last(v, n):
    if len(v) < n: return None
    k = 2/(n+1); s = sum(v[:n])/n
    for x in v[n:]: s = x*k + s*(1-k)
    return s

def pivots(rows, wing=3):
    hi, lo = [], []
    for i in range(wing, len(rows)-wing):
        w = rows[i-wing:i+wing+1]
        if rows[i]["h"] == max(x["h"] for x in w): hi.append(rows[i]["h"])
        if rows[i]["l"] == min(x["l"] for x in w): lo.append(rows[i]["l"])
    return lo, hi

def trend(rows):
    c = [r["c"] for r in rows]
    e20, e50 = ema_last(c, 20), ema_last(c, 50)
    lo, hi = pivots(rows, 5)
    score = 0
    if e20 and e50: score += 1 if c[-1] > e20 > e50 else (-1 if c[-1] < e20 < e50 else 0)
    if len(lo) >= 2 and len(hi) >= 2:
        score += 1 if (hi[-1] > hi[-2] and lo[-1] > lo[-2]) else (-1 if (hi[-1] < hi[-2] and lo[-1] < lo[-2]) else 0)
    return score

WEIGHT = {"1d": 8, "4h": 4, "1h": 2, "30m": 1, "15m": 0.5, "5m": 0.25, "1m": 0.1}
tally = 0.0
print("frame  trend  weight")
for tf in ["1d", "4h", "1h", "30m", "15m", "5m", "1m"]:
    s = trend(load(tf)); w = WEIGHT[tf]
    tally += s*w
    print(f"{tf:>4}   {s:>+d}     {w}")
print(f"weighted tally: {tally:+.2f}")

# Live quote, with the market's own verdict.
try:
    req = urllib.request.Request("https://biquote.io/api/XAUUSD", headers={"User-Agent": UA})
    q = json.loads(urllib.request.urlopen(req, timeout=15).read())
    print(f"live: mid {q['mid']}  state {q['marketState']}  stale {q['stale']}  age {q['quoteAgeSeconds']}s  dayDiff {q['dayDiffPercent']}%")
except Exception as e:
    print("live quote failed:", e)
