"""The trade inbox: how a trade typed in the chat survives the runner.

The bug this guards against is silent: `data/` is not saved from the bot
workflow, so a trade recorded there is gone before the grader runs. A test that
only checked "the trade is in the local journal" would pass while the trade was
lost in production -- so these tests follow the record across two stores, the way
two runners would.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desk import inbox
from desk.journal import Journal


def test_append_then_sync_moves_a_trade_onto_another_store(tmp_path: Path) -> None:
    """The whole point: a trade recorded on one runner appears on another."""
    box = tmp_path / "trade_inbox.jsonl"
    inbox.append({"symbol": "XAUUSD", "direction": "short", "entry": 4073.0,
                  "stop": 4077.0, "target": 4064.0, "size": 0.01,
                  "opened_at": "2026-11-06T19:10:00Z"}, dest=box)

    # A DIFFERENT journal -- the watch runner's, which never saw the bot's disk.
    other = Journal(tmp_path / "other" / "journal.db")
    added = inbox.sync(other, src=box)
    assert len(added) == 1
    t = other.all_trades()[0]
    assert (t.direction, t.entry, t.stop, t.target, t.size) == \
        ("short", 4073.0, 4077.0, 4064.0, 0.01)
    assert t.opened_at == "2026-11-06T19:10:00Z"


def test_sync_is_idempotent_so_a_pass_can_run_twice(tmp_path: Path) -> None:
    """The cache may or may not have survived, so the same inbox line can be
    synced many times. A duplicate trade would double the record."""
    box = tmp_path / "trade_inbox.jsonl"
    inbox.append({"symbol": "XAUUSD", "direction": "long", "entry": 4063.0,
                  "stop": 4055.0}, dest=box)
    j = Journal(tmp_path / "journal.db")
    assert len(inbox.sync(j, src=box)) == 1
    assert inbox.sync(j, src=box) == []
    assert len(j.all_trades()) == 1


def test_append_is_idempotent_on_a_reused_uid(tmp_path: Path) -> None:
    """A retried pass mints the same uid, so the line must not be written twice."""
    box = tmp_path / "trade_inbox.jsonl"
    rec = {"uid": "abc123", "symbol": "XAUUSD", "direction": "long",
           "entry": 4063.0, "stop": 4055.0}
    inbox.append(rec, dest=box)
    inbox.append(rec, dest=box)
    assert len(inbox.read(box)) == 1


def test_a_malformed_line_does_not_block_the_trades_behind_it(tmp_path: Path) -> None:
    """One bad line must not cost every trade after it -- the file is written by
    a runner that can be killed mid-write."""
    box = tmp_path / "trade_inbox.jsonl"
    box.write_text(
        '{"uid": "u1", "symbol": "XAUUSD", "direction": "long", '
        '"entry": 4063.0, "stop": 4055.0}\n'
        'this is not json\n'
        '{"uid": "u2", "symbol": "XAUUSD", "direction": "short", '
        '"entry": 4073.0, "stop": 4077.0}\n', encoding="utf-8")
    recs = inbox.read(box)
    assert [r["uid"] for r in recs] == ["u1", "u2"]
    j = Journal(tmp_path / "journal.db")
    assert len(inbox.sync(j, src=box)) == 2


def test_an_incomplete_record_is_skipped_not_invented(tmp_path: Path) -> None:
    """A record with no stop cannot be graded, so it is dropped rather than
    guessed -- the same rule the journal applies to a trade."""
    box = tmp_path / "trade_inbox.jsonl"
    box.write_text('{"uid": "u1", "symbol": "XAUUSD", "direction": "long", '
                   '"entry": 4063.0}\n', encoding="utf-8")
    j = Journal(tmp_path / "journal.db")
    assert inbox.sync(j, src=box) == []
    assert j.all_trades() == []


def test_a_trade_recorded_in_the_chat_reaches_a_second_journal(tmp_path, monkeypatch) -> None:
    """The end-to-end shape: the bot's Actions writes the journal AND the inbox;
    a separate store (the watch) picks it up from the inbox alone."""
    from desk.actions import Actions
    from desk.config import Settings

    box = tmp_path / "trade_inbox.jsonl"
    monkeypatch.setenv("DESK_INBOX", str(box))
    monkeypatch.setenv("DESK_HOME", str(tmp_path / "bot"))
    s = Settings()
    Actions(s).record_trade("سجل بيع 4073 وقف 4077 هدف 4064 حجم 0.01")

    watch = Journal(tmp_path / "watch" / "journal.db")
    assert inbox.sync(watch, src=box) == [1]
    assert watch.all_trades()[0].direction == "short"


@pytest.mark.parametrize("field", ["entry", "stop"])
def test_a_record_with_a_non_numeric_price_is_skipped(tmp_path: Path, field: str) -> None:
    box = tmp_path / "trade_inbox.jsonl"
    rec = {"uid": "u1", "symbol": "XAUUSD", "direction": "long",
           "entry": 4063.0, "stop": 4055.0}
    rec[field] = "not a number"
    inbox.append(rec, dest=box)
    j = Journal(tmp_path / "journal.db")
    assert inbox.sync(j, src=box) == []


def test_the_graders_frame_is_fetched_even_when_the_agents_do_not_use_it(
        tmp_path, monkeypatch) -> None:
    """The journal grades on 5m; the agents may be configured to read only 1h.
    If `gather` fetched only the agents' frames, the grader would see no bars and
    settle nothing -- and "nothing resolved" is indistinguishable from "no trade
    hit its level", which is the silent failure this guards."""
    from desk.config import Settings
    from desk.orchestrator import Orchestrator

    s = Settings()
    s.home = tmp_path
    s.symbol = "XAUUSD"
    s.timeframes = ("1h",)             # the agents' view
    s.trades_timeframes = ("5m",)      # the grader's view
    s.peers = ()
    fetched: list[str] = []

    def fake(sym, tf, limit=1000):
        fetched.append(tf)
        return []

    monkeypatch.setattr("desk.sources.fetch_candles", fake)
    monkeypatch.setattr("desk.sources.fetch_quote", lambda sym: {"mid": 1.0})
    Orchestrator(s).gather()
    assert "5m" in fetched, "the grader's frame must be fetched, not assumed"
    assert "1h" in fetched
