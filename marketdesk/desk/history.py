"""Locating and loading the shipped long daily history.

The desk ships ~500 daily bars from the live feed, which is far too short to ask
"when did the market look like this before?". A ten-year file sits beside the
package (fetched by `longdata.py`), and this is the one place that finds it.

Two callers used to each carry their own search path (the plan card and the CLI),
which is how they drifted. They share this now.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


def locate_daily(symbol: str = "XAUUSD") -> Path | None:
    """The shipped daily CSV for `symbol`, or None. Searched from the package,
    then from the repo root, so it is found whether the runner's cwd is
    `marketdesk/` or the repo root."""
    here = Path(__file__).resolve()
    for cand in (here.parent.parent.parent / "longdata" / f"{symbol}_1d.csv",
                 Path("longdata") / f"{symbol}_1d.csv",
                 Path("../longdata") / f"{symbol}_1d.csv"):
        if cand.exists():
            return cand
    return None


@lru_cache(maxsize=8)
def _load_cached(path_str: str, mtime: float) -> tuple[dict, ...]:
    from .backtest import load_daily  # deferred: backtest -> orchestrator -> agents
    return tuple(load_daily(Path(path_str)))


def daily_bars(symbol: str = "XAUUSD") -> list[dict]:
    """Closed daily bars, oldest first, from the shipped file. Empty when the
    file is missing -- a missing record is silence, never a guess."""
    path = locate_daily(symbol)
    if path is None:
        return []
    try:
        bars = _load_cached(str(path), path.stat().st_mtime)
    except OSError:
        return []
    return [b for b in bars if not b.get("isOpen")]


def shaped(symbol: str = "XAUUSD") -> list[dict]:
    """The same bars in the {o,h,l,c} shape the indicators speak."""
    return [{"o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"]}
            for b in daily_bars(symbol)]
