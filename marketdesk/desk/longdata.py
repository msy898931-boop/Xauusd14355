"""Fetch long daily history from Yahoo's chart API (keyless) into the desk's CSV
shape, so the same scan/backtest can run on 10+ years and on several markets.

Yahoo returns timestamps + ohlc arrays; this flattens them into the same columns
biquote's CSVs use, so nothing downstream changes. Only fully-formed bars are
kept -- the last bar of a live chart is still moving, and a moving bar is not a
fact.
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

UA = "Mozilla/5.0 (compatible; AnalysisDesk/1.0)"
BASE = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"

# name -> Yahoo symbol. Chosen for a spread of asset classes, so a signal that
# works is tested against more than one market's drift.
MARKETS = {
    "XAUUSD": "GC=F",      # gold futures
    "XAGUSD": "SI=F",      # silver
    "EURUSD": "EURUSD=X",  # euro
    "USDJPY": "JPY=X",     # yen
    "USOIL": "CL=F",       # crude
    "SPX": "^GSPC",        # S&P 500
    "DXY": "DX-Y.NYB",     # dollar index
}


def get(url: str, tries: int = 4):
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


def fetch(symbol: str, rng: str = "10y") -> list[dict]:
    """Daily bars from Yahoo, oldest first.

    The last bar of a live chart is the day still forming -- its close moves with
    the tick. It is marked `isOpen` rather than dropped, because the date is real
    and a caller may want to know the market is mid-session; the study and the
    backtest exclude it. Marking it closed would be the same look-at-the-partial-
    bar mistake the desk fixed twice already.
    """
    body = get(BASE.format(sym=symbol, rng=rng))
    res = (body.get("chart") or {}).get("result") or []
    if not res:
        return []
    r = res[0]
    ts = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    o, h, lo, c, v = (q.get("open") or [], q.get("high") or [],
                      q.get("low") or [], q.get("close") or [],
                      q.get("volume") or [])
    out: list[dict] = []
    for i, t in enumerate(ts):
        try:
            if None in (o[i], h[i], lo[i], c[i]):
                continue
            out.append({
                "openTime": datetime.fromtimestamp(t, UTC)
                            .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "open": float(o[i]), "high": float(h[i]),
                "low": float(lo[i]), "close": float(c[i]),
                "volume": 0, "tickVolume": int(v[i] or 0) if i < len(v) else 0,
                "isOpen": False,
            })
        except (IndexError, TypeError, ValueError):
            continue
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if out and out[-1]["openTime"][:10] == today:
        out[-1]["isOpen"] = True
    return out


def write_csv(bars: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["openTime", "open", "high", "low", "close", "volume",
                    "tickVolume", "isOpen"])
        for b in bars:
            w.writerow([b["openTime"], b["open"], b["high"], b["low"], b["close"],
                        b["volume"], b["tickVolume"], b["isOpen"]])


def main() -> int:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("longdata")
    rng = sys.argv[2] if len(sys.argv) > 2 else "10y"
    print(f"fetching {len(MARKETS)} markets, range={rng} -> {outdir}/")
    for name, sym in MARKETS.items():
        try:
            bars = fetch(sym, rng)
            if not bars:
                print(f"  {name:7} {sym:10} -> no bars")
                continue
            write_csv(bars, outdir / f"{name}_1d.csv")
            print(f"  {name:7} {sym:10} -> {len(bars):5} bars  "
                  f"{bars[0]['openTime'][:10]} .. {bars[-1]['openTime'][:10]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:7} {sym:10} -> FAILED {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
