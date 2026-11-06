"""The trade inbox -- how a trade the owner types in the chat survives the runner.

WHY THIS EXISTS
The journal lives in `data/`, which is git-ignored, and a GitHub runner is a
fresh VM with no disk. `marketdesk-watch.yml` carries `data/` in the Actions
cache, but `marketdesk-bot.yml` is restore-only: a trade recorded there is
written to a throwaway VM and gone before the next watch pass can grade it.

Letting the bot save the cache too does NOT fix it. Two workflows writing
`desk-state-<run_id>` keys race, and `restore-keys` picks the newest -- so if
the watch saves after the bot, the bot's snapshot (which holds the new trade) is
discarded, and the trade is lost for good. Silent data loss is the one failure a
journal must not have.

So the trade travels through git instead of the cache. The bot appends one line
to this file and the workflow commits it -- only on a pass that actually
recorded something, so this is a handful of commits a week, not one every five
minutes. Appends never clobber, so concurrent runs cannot lose each other's
lines the way cache snapshots can. The watch reads the file and copies any trade
it does not already hold.

A `uid` makes the copy idempotent: the journal may already have the trade (the
cache happened to survive), and applying it twice must not create a duplicate.
The trade is keyed on the uid the bot minted, not on the row id, because row ids
collide across runners -- every fresh store starts at 1.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

# The file is git-tracked, so it must NOT live under `data/`. It sits beside the
# package so it is found from any working directory, the same trick `_long_csv`
# uses for the history CSV.
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "trade_inbox.jsonl"

# The fields a record carries. `uid` and `opened_at` are the only ones the bot
# adds; the rest are the trade itself, so the watch can rebuild it exactly.
FIELDS = ("uid", "symbol", "direction", "entry", "stop", "target", "size",
          "opened_at", "note", "source")


def path() -> Path:
    """Where the inbox lives, overridable for a test or a non-default checkout."""
    return Path(os.environ.get("DESK_INBOX", DEFAULT_PATH))


def append(record: dict, *, dest: Path | None = None) -> str:
    """Append one trade and return its uid. A uid already present is not re-added,
    so a retried pass cannot double-enter a trade."""
    rec = dict(record)
    uid = rec.get("uid") or uuid.uuid4().hex
    rec["uid"] = uid
    rec = {k: rec.get(k) for k in FIELDS}
    dest = dest or path()
    if any(r.get("uid") == uid for r in read(dest)):
        return uid
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return uid


def read(src: Path | None = None) -> list[dict]:
    """Every record in the inbox, oldest first. A malformed line is skipped
    rather than raised: one bad line must not block every trade behind it."""
    src = src or path()
    if not src.exists():
        return []
    out: list[dict] = []
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("uid"):
            out.append(rec)
    return out


def sync(journal, src: Path | None = None) -> list[int]:
    """Copy every inbox trade the journal does not already hold, and return the
    new row ids.

    Idempotent on `uid`: a trade already in the journal is skipped, so running
    this on every watch pass is safe whether or not the cache survived. The
    journal may legitimately already hold the trade -- the cache is best-effort,
    and a hit means the bot's write did persist.
    """
    added: list[int] = []
    for rec in read(src):
        if journal.has_uid(rec["uid"]):
            continue
        try:
            tid = journal.add(
                rec.get("symbol") or "XAUUSD", rec["direction"],
                float(rec["entry"]), float(rec["stop"]),
                float(rec["target"]) if rec.get("target") is not None else None,
                float(rec["size"]) if rec.get("size") is not None else None,
                opened_at=rec.get("opened_at"), note=rec.get("note"),
                source=rec.get("source") or "inbox", uid=rec["uid"])
        except (KeyError, TypeError, ValueError):
            continue                      # a half-written record is not a trade
        added.append(tid)
    return added
