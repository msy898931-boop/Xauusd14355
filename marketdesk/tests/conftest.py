"""Shared test setup.

THE WALL-CLOCK BUG THIS FIXES
Several tests call `desk.run()` and `watch_once()` with no explicit time, so they
read the real clock through `market_session()`. The desk stands down when the
market is shut -- from Friday 21:00 UTC until Sunday 22:00 UTC -- and a
stand-down skips every agent, so those tests fail on a weekend even though
nothing is wrong with the code. CI never caught it because every push so far
landed on a weekday.

The fix pins the *default* to an open market, and only the default. A call with
an explicit `now=` still reaches the real `market_session`, so the session tests
(which pass a time) keep testing the real calendar. A test that injects its own
session via `monkeypatch` runs after this fixture and overrides it, so the
closed-market tests still see their closed session.

`desk.orchestrator` and `desk.daemon` each do `from .session import
market_session`, binding the name into their own module -- so both are patched,
not `desk.session`.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _open_market_by_default(monkeypatch):
    import desk.daemon as daemon
    import desk.orchestrator as orch
    from desk.session import Session
    from desk.session import market_session as real

    def pinned(now=None):
        if now is None:
            return Session(True, "سوق مفتوح (اختبار)", None)
        return real(now)

    monkeypatch.setattr(orch, "market_session", pinned)
    monkeypatch.setattr(daemon, "market_session", pinned)


@pytest.fixture(autouse=True)
def _inbox_never_touches_the_tracked_file(tmp_path, monkeypatch):
    """Point the trade inbox at a temp file for EVERY test.

    The real inbox is git-tracked and must be empty at rest. A test that records
    a trade without setting `DESK_INBOX` -- as the bot integration test does --
    would otherwise append to the repository's own file, so the suite would dirty
    the working tree. Isolating it here means no test can, including a test
    written later.
    """
    monkeypatch.setenv("DESK_INBOX", str(tmp_path / "trade_inbox.jsonl"))
