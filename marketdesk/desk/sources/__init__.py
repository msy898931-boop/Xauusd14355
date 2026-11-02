"""Data sources. One module per upstream; all keyless, all stdlib.

The package re-exports the active source's functions so callers write
`sources.fetch_candles(...)` and never name a vendor directly.
"""

from .biquote import (
    SourceError,
    fetch_calendar,
    fetch_candles,
    fetch_news,
    fetch_quote,
    fetch_summary,
)

__all__ = ["SourceError", "fetch_candles", "fetch_quote", "fetch_news",
           "fetch_calendar", "fetch_summary"]
