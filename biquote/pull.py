"""Pull all candles biquote serves for a symbol, one CSV per timeframe.

Pure stdlib: the sandbox has no pandas/requests, and biquote needs no key.
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.request

UA = "Mozilla/5.0 (compatible; AnalysisDesk/1.0)"
BASE = "https://biquote.io/api/{symbol}/ohlc"
TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


def get(url: str, tries: int = 4) -> dict:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as exc:  # noqa: BLE001 - report, never invent
            last = exc
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET failed after {tries}: {url} ({last})")


def fetch(symbol: str, interval: str, limit: int = 2000) -> list[dict]:
    url = f"{BASE.format(symbol=symbol)}?interval={interval}&limit={limit}"
    body = get(url)
    bars = body.get("bars") or []
    # Oldest first, closed bars only are flagged; keep the open bar but tag it.
    bars = sorted(bars, key=lambda b: b["openTime"])
    return bars


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "XAUUSD"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary = []
    for tf in TIMEFRAMES:
        bars = fetch(symbol, tf)
        if not bars:
            summary.append((tf, 0, "", ""))
            continue
        path = f"{symbol}_{tf}.csv"
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["openTime", "open", "high", "low", "close", "volume", "tickVolume", "isOpen"])
            for b in bars:
                w.writerow([b["openTime"], b["open"], b["high"], b["low"], b["close"],
                            b.get("volume"), b.get("tickVolume"), b.get("isOpen")])
        summary.append((tf, len(bars), bars[0]["openTime"], bars[-1]["openTime"]))
        print(f"{tf:>4}: {len(bars):>4} bars  {bars[0]['openTime']} -> {bars[-1]['openTime']}  -> {path}")
    with open(f"{symbol}_pull_summary.json", "w") as fh:
        json.dump({"symbol": symbol, "pulled_at": stamp,
                   "timeframes": [{"tf": t, "bars": n, "first": a, "last": b} for t, n, a, b in summary]},
                  fh, indent=2)
    print(f"pulled_at {stamp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
