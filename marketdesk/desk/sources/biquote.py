"""biquote.io — the desk's single data source. Keyless, pure stdlib.

Endpoint families, documented at https://biquote.io/docs/:
  * candles   GET /api/{symbol}/ohlc?interval=<tf>&limit<=1000
  * quote     GET /api/{symbol}            (mid, marketState, stale, quoteAgeSeconds)
  * news      GET /api/news/market?symbol=...&maxResults=...
  * calendar  GET /api/calendar?importance=high
  * summary   GET /api/market/summary

Two traps are handled here so no caller has to know them:
  * `volume` is ALWAYS 0 on this broker feed — the activity field is `tickVolume`.
  * the feed's own verdict (`marketState`, `stale`, `quoteAgeSeconds`) is carried
    through, so a closed market reads as closed, never as a live price.

Licence note: biquote's terms forbid re-exposing OHLC / bulk data through a
public API. The desk reads the candles locally for its own analysis and does not
serve them onward; keep it that way.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from ..config import BIQUOTE_BASE, BIQUOTE_UA


class SourceError(RuntimeError):
    """An upstream read failed. Reported, never replaced with a made-up value."""


def _get(path: str, params: dict | None = None, tries: int = 3, timeout: int = 25):
    url = f"{BIQUOTE_BASE}{path}"
    if params:
        qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items() if v is not None)
        if qs:
            url = f"{url}?{qs}"
    last: Exception | None = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": BIQUOTE_UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as exc:
            last = exc
            time.sleep(0.8 * (i + 1))
    raise SourceError(f"GET {url} failed: {last}")


def fetch_candles(symbol: str, tf: str, limit: int = 1000) -> list[dict]:
    """All bars biquote keeps for one timeframe (its retention is the ceiling,
    not `limit`). Returned oldest-first."""
    body = _get(f"/api/{symbol}/ohlc", {"interval": tf, "limit": limit})
    bars = body.get("bars") or []
    return sorted(bars, key=lambda b: b["openTime"])


def fetch_quote(symbol: str) -> dict:
    """The live broker tick, reshaped into the desk's quote shape."""
    q = _get(f"/api/{symbol}")
    if not isinstance(q, dict) or not isinstance(q.get("mid"), (int, float)):
        raise SourceError(f"no usable quote for {symbol}")
    return {
        "mid": round(float(q["mid"]), 4),
        "bid": q.get("bid"), "ask": q.get("ask"),
        "market_state": q.get("marketState"),
        "stale": bool(q.get("stale")),
        "quote_age_s": q.get("quoteAgeSeconds"),
        "day_diff_pct": q.get("dayDiffPercent"),
        "source": q.get("source", "biquote (MT5)"),
    }


def fetch_news(symbol: str | None = None, max_results: int = 25) -> list[dict]:
    """Market news, optionally filtered to a symbol. biquote aggregates Google
    News + some Hacker News; the publisher field tells them apart."""
    return _get("/api/news/market", {"symbol": symbol, "maxResults": max_results}) or []


def fetch_calendar(importance: str = "high", limit: int = 100) -> list[dict]:
    """Economic releases. `actual` is null until published — never read it as 0."""
    return _get("/api/calendar", {"importance": importance, "limit": limit}) or []


def fetch_summary() -> dict:
    """Whole-market breadth: advancers/decliners across biquote's universe."""
    return _get("/api/market/summary") or {}
