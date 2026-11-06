"""Memory — two tiers, exactly as the desk is organised.

Shared memory (`SharedMemory`, SQLite) is the blackboard every agent reads and
writes: the candle tables, the news table, the economic calendar, and the
`studies` table where each agent posts its result. Private memory
(`AgentMemory`, one JSON file per agent under `agents/<name>/`) holds what only
that agent keeps: its own running notes, its last verdict, its counters.

The split matters: a specialist's scratch reasoning must not be mistaken for a
fact on the blackboard. Only a *study* is published; the private store is the
agent's own.

Provenance is a first-class column everywhere. A row without a source is not
written, and a metric that cannot be computed is stored as NULL, never 0.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol TEXT NOT NULL, tf TEXT NOT NULL, open_time TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, tick_volume INTEGER,
    is_open INTEGER, fetched_at REAL,
    PRIMARY KEY (symbol, tf, open_time)
);
CREATE TABLE IF NOT EXISTS quotes (
    symbol TEXT PRIMARY KEY, mid REAL, bid REAL, ask REAL,
    market_state TEXT, stale INTEGER, quote_age_s INTEGER,
    day_diff_pct REAL, source TEXT, fetched_at REAL
);
CREATE TABLE IF NOT EXISTS news (
    url TEXT PRIMARY KEY, title TEXT, description TEXT, publisher TEXT,
    published TEXT, fetched_at REAL
);
CREATE TABLE IF NOT EXISTS calendar (
    id TEXT PRIMARY KEY, time TEXT, country TEXT, currency TEXT, name TEXT,
    importance TEXT, actual REAL, forecast REAL, previous REAL,
    fetched_at REAL
);
CREATE TABLE IF NOT EXISTS studies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL, agent TEXT NOT NULL, kind TEXT NOT NULL,
    stance TEXT, confidence REAL, score REAL, summary TEXT,
    payload TEXT, created_at REAL,
    UNIQUE (run_id, agent)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, symbol TEXT, started_at REAL, finished_at REAL,
    verdict TEXT, score REAL, payload TEXT
);
CREATE TABLE IF NOT EXISTS outcomes (
    run_id TEXT NOT NULL, symbol TEXT NOT NULL, horizon TEXT NOT NULL,
    entry REAL, exit REAL, move_pct REAL,
    bias_dir TEXT, bias_score REAL, correct INTEGER,
    graded_at REAL,
    PRIMARY KEY (run_id, horizon)
);
CREATE TABLE IF NOT EXISTS agent_outcomes (
    run_id TEXT NOT NULL, agent TEXT NOT NULL, horizon TEXT NOT NULL,
    stance TEXT, score REAL, move_pct REAL, correct INTEGER,
    graded_at REAL,
    PRIMARY KEY (run_id, agent, horizon)
);
CREATE TABLE IF NOT EXISTS plan_outcomes (
    run_id TEXT NOT NULL, horizon TEXT NOT NULL, direction TEXT,
    entry REAL, stop REAL, target REAL,
    result TEXT, bars INTEGER, r_multiple REAL, graded_at REAL,
    PRIMARY KEY (run_id, horizon)
);
"""


def _now() -> float:
    return time.time()


def json_default(o):
    """Serialise a desk object that `json` does not know.

    A stored recommendation carries computed objects -- a `Sizing`, and inside
    the scenarios a `Sizing` per setup. They are the honest output, so they are
    written, not dropped. A dataclass becomes its fields; anything else falls
    back to `str` rather than failing the write. Lives here because this is the
    module that persists payloads; the CLI imports it so the two agree."""
    from dataclasses import asdict, is_dataclass

    if is_dataclass(o) and not isinstance(o, type):
        return asdict(o)
    return str(o)


class SharedMemory:
    """The blackboard. Every agent reads it; only published studies are facts."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conn()) as c:
            c.executescript(SCHEMA)
            c.commit()

    def _conn(self) -> sqlite3.Connection:
        # Eight agents publish concurrently. WAL lets readers and a writer share
        # the file, but a burst of writers can still collide, so the busy timeout
        # is long and `_write` retries rather than letting an agent fail for a
        # reason it cannot see.
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        return c

    @staticmethod
    def _write(fn: Callable[[], Any]) -> Any:
        """Run a write, retrying a locked database. A lock is a scheduling fact,
        not a failure the caller should see; anything else is re-raised."""
        last: Exception | None = None
        for attempt in range(6):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) and "busy" not in str(exc):
                    raise
                last = exc
                time.sleep(0.1 * (attempt + 1))
        raise last if last is not None else RuntimeError("write failed")

    def _do_write(self, fn: Callable[[Any], None]) -> None:
        """Open a connection, run `fn(cursor)`, commit — retrying on a lock."""
        def attempt() -> None:
            with closing(self._conn()) as c:
                fn(c)
                c.commit()
        self._write(attempt)

    # ---- candles -------------------------------------------------------
    def save_candles(self, symbol: str, tf: str, bars: list[dict]) -> int:
        """Upsert bars. `volume` from the feed is always 0 on this broker; the
        real activity is `tickVolume`, which is what we keep."""
        rows = [
            (symbol, tf, b["openTime"], b["open"], b["high"], b["low"], b["close"],
             int(b.get("tickVolume") or 0), 1 if b.get("isOpen") else 0, _now())
            for b in bars
        ]
        self._do_write(lambda c: c.executemany(
            "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol,tf,open_time) DO UPDATE SET "
            "open=excluded.open, high=excluded.high, low=excluded.low, "
            "close=excluded.close, tick_volume=excluded.tick_volume, "
            "is_open=excluded.is_open, fetched_at=excluded.fetched_at", rows))
        return len(rows)

    def load_candles(self, symbol: str, tf: str) -> list[dict]:
        with closing(self._conn()) as c:
            cur = c.execute(
                "SELECT * FROM candles WHERE symbol=? AND tf=? ORDER BY open_time", (symbol, tf))
            return [dict(r) for r in cur.fetchall()]

    def candle_counts(self, symbol: str) -> dict[str, dict]:
        with closing(self._conn()) as c:
            cur = c.execute(
                "SELECT tf, COUNT(*) n, MIN(open_time) a, MAX(open_time) b "
                "FROM candles WHERE symbol=? GROUP BY tf", (symbol,))
            return {r["tf"]: {"n": r["n"], "first": r["a"], "last": r["b"]} for r in cur.fetchall()}

    # ---- quote ---------------------------------------------------------
    def save_quote(self, symbol: str, q: dict) -> None:
        self._do_write(lambda c: c.execute(
            "INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET mid=excluded.mid, bid=excluded.bid, "
            "ask=excluded.ask, market_state=excluded.market_state, stale=excluded.stale, "
            "quote_age_s=excluded.quote_age_s, day_diff_pct=excluded.day_diff_pct, "
            "source=excluded.source, fetched_at=excluded.fetched_at",
            (symbol, q.get("mid"), q.get("bid"), q.get("ask"), q.get("market_state"),
             1 if q.get("stale") else 0, q.get("quote_age_s"), q.get("day_diff_pct"),
             q.get("source"), _now())))

    def load_quote(self, symbol: str) -> dict | None:
        with closing(self._conn()) as c:
            r = c.execute("SELECT * FROM quotes WHERE symbol=?", (symbol,)).fetchone()
            return dict(r) if r else None

    # ---- news / calendar ----------------------------------------------
    def save_news(self, items: list[dict]) -> int:
        rows = [(i.get("url"), i.get("title"), i.get("description"), i.get("publisher"),
                 i.get("publishedDate"), _now()) for i in items if i.get("url")]
        self._do_write(lambda c: c.executemany(
            "INSERT INTO news VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(url) DO UPDATE SET title=excluded.title, "
            "description=excluded.description, fetched_at=excluded.fetched_at", rows))
        return len(rows)

    def load_news(self, limit: int = 60) -> list[dict]:
        with closing(self._conn()) as c:
            cur = c.execute("SELECT * FROM news ORDER BY published DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]

    def save_calendar(self, events: list[dict]) -> int:
        rows = [(e.get("id"), e.get("time"), e.get("countryCode"), e.get("currency"),
                 e.get("name"), e.get("importance"), e.get("actual"), e.get("forecast"),
                 e.get("previous"), _now()) for e in events if e.get("id")]
        self._do_write(lambda c: c.executemany(
            "INSERT INTO calendar VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET actual=excluded.actual, "
            "forecast=excluded.forecast, previous=excluded.previous, "
            "fetched_at=excluded.fetched_at", rows))
        return len(rows)

    def load_calendar(self) -> list[dict]:
        with closing(self._conn()) as c:
            cur = c.execute("SELECT * FROM calendar ORDER BY time")
            return [dict(r) for r in cur.fetchall()]

    # ---- studies (published agent results) ----------------------------
    def publish_study(self, run_id: str, agent: str, kind: str, stance: str,
                      confidence: float, score: float, summary: str,
                      payload: dict) -> None:
        self._do_write(lambda c: c.execute(
            "INSERT INTO studies (run_id, agent, kind, stance, confidence, score, "
            "summary, payload, created_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id, agent) DO UPDATE SET kind=excluded.kind, "
            "stance=excluded.stance, confidence=excluded.confidence, "
            "score=excluded.score, summary=excluded.summary, payload=excluded.payload",
            (run_id, agent, kind, stance, confidence, score, summary,
             json.dumps(payload, ensure_ascii=False), _now())))

    def load_studies(self, run_id: str) -> list[dict]:
        with closing(self._conn()) as c:
            cur = c.execute("SELECT * FROM studies WHERE run_id=? ORDER BY agent", (run_id,))
            out = []
            for r in cur.fetchall():
                d = dict(r)
                d["payload"] = json.loads(d["payload"] or "{}")
                out.append(d)
            return out

    # ---- runs ----------------------------------------------------------
    def save_run(self, run_id: str, symbol: str, started: float, finished: float,
                 verdict: str, score: float, payload: dict) -> None:
        self._do_write(lambda c: c.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET finished_at=excluded.finished_at, "
            "verdict=excluded.verdict, score=excluded.score, payload=excluded.payload",
            (run_id, symbol, started, finished, verdict, score,
             json.dumps(payload, ensure_ascii=False, default=json_default))))

    def recent_runs(self, limit: int = 10) -> list[dict]:
        with closing(self._conn()) as c:
            cur = c.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]

    def all_runs(self) -> list[dict]:
        """Every run, oldest first — the input to grading."""
        with closing(self._conn()) as c:
            cur = c.execute("SELECT * FROM runs ORDER BY started_at")
            return [dict(r) for r in cur.fetchall()]

    # ---- outcomes (the scoreboard) -------------------------------------
    def save_outcome(self, run_id: str, symbol: str, horizon: str, entry: float,
                     exit_: float, move_pct: float, bias_dir: str, bias_score: float,
                     correct: int) -> None:
        self._do_write(lambda c: c.execute(
            "INSERT INTO outcomes VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id, horizon) DO UPDATE SET exit=excluded.exit, "
            "move_pct=excluded.move_pct, correct=excluded.correct, "
            "graded_at=excluded.graded_at",
            (run_id, symbol, horizon, entry, exit_, move_pct, bias_dir,
             bias_score, correct, _now())))

    def load_outcomes(self, horizon: str | None = None) -> list[dict]:
        q = "SELECT * FROM outcomes"
        args: tuple = ()
        if horizon:
            q += " WHERE horizon=?"
            args = (horizon,)
        with closing(self._conn()) as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]

    def graded_run_ids(self) -> set[str]:
        with closing(self._conn()) as c:
            return {r[0] for r in c.execute("SELECT DISTINCT run_id FROM outcomes")}

    def save_agent_outcome(self, run_id: str, agent: str, horizon: str, stance: str,
                           score: float, move_pct: float, correct: int) -> None:
        self._do_write(lambda c: c.execute(
            "INSERT INTO agent_outcomes VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id, agent, horizon) DO UPDATE SET "
            "move_pct=excluded.move_pct, correct=excluded.correct, "
            "graded_at=excluded.graded_at",
            (run_id, agent, horizon, stance, score, move_pct, correct, _now())))

    def load_agent_outcomes(self, agent: str | None = None,
                            horizon: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM agent_outcomes", []
        clauses = []
        if agent:
            clauses.append("agent=?")
            args.append(agent)
        if horizon:
            clauses.append("horizon=?")
            args.append(horizon)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        with closing(self._conn()) as c:
            return [dict(r) for r in c.execute(q, tuple(args)).fetchall()]

    # ---- plan outcomes (did the stop or the target come first?) --------
    def save_plan_outcome(self, run_id: str, horizon: str, direction: str,
                          entry: float, stop: float, target: float,
                          result: str, bars: int, r_multiple: float | None) -> None:
        self._do_write(lambda c: c.execute(
            "INSERT INTO plan_outcomes VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id, horizon) DO UPDATE SET result=excluded.result, "
            "bars=excluded.bars, r_multiple=excluded.r_multiple, "
            "graded_at=excluded.graded_at",
            (run_id, horizon, direction, entry, stop, target, result, bars,
             r_multiple, _now())))

    def load_plan_outcomes(self, horizon: str | None = None) -> list[dict]:
        q: str = "SELECT * FROM plan_outcomes"
        args: tuple = ()
        if horizon:
            q += " WHERE horizon=?"
            args = (horizon,)
        with closing(self._conn()) as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]


class AgentMemory:
    """One agent's private store: its own notes, verdict and counters.

    Deliberately a plain JSON file, not a table, so an agent's private state can
    never be queried as if it were a shared fact.
    """

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "memory.json"

    def _read(self) -> dict:
        if not self.file.exists():
            return {"notes": [], "last_verdict": None, "runs": 0}
        try:
            return json.loads(self.file.read_text())
        except (json.JSONDecodeError, OSError):
            return {"notes": [], "last_verdict": None, "runs": 0}

    def _write(self, data: dict) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(self.file)

    def remember(self, note: str, verdict: str | None = None) -> None:
        d = self._read()
        d["notes"].append({"at": _now(), "note": note})
        d["notes"] = d["notes"][-50:]          # bounded, never a growing log
        d["runs"] = d.get("runs", 0) + 1
        if verdict is not None:
            d["last_verdict"] = {"at": _now(), "verdict": verdict}
        self._write(d)

    def recall(self) -> dict:
        return self._read()

    def last_verdict(self) -> str | None:
        v = self._read().get("last_verdict")
        return v["verdict"] if v else None
