"""Journal — the owner's own trades, graded the way the desk grades a plan.

The desk already grades *its* plans (stop-before-target, first touch wins,
expectancy in R). This is the same measurement for the trades the owner actually
took: it records the entry, stop and target, walks the bars forward, and reports
which came first. It exists because "I made money on that one" is a memory, and
a memory cannot tell you whether the method works -- a sequence of R multiples
can.

Deliberately the same rules as `scoreboard._walk_plan`:
  * the FIRST touch decides; a bar that spans both counts as the stop;
  * the result is reported in R, never in points, so a 60%-win method at 0.5R
    is not mistaken for a good one;
  * an unresolved trade is "open", never counted as a win.

Nothing here is a study statistic: these are the owner's own trades, not the
ten-year record, so the journal prints freely on any surface.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, direction TEXT NOT NULL,
    entry REAL NOT NULL, stop REAL NOT NULL, target REAL,
    size REAL, opened_at TEXT NOT NULL, note TEXT,
    source TEXT, run_id TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    result TEXT, exit_price REAL, bars INTEGER, r_multiple REAL,
    closed_at TEXT, graded_at REAL
);
"""


@dataclass
class Trade:
    id: int
    symbol: str
    direction: str
    entry: float
    stop: float
    target: float | None
    size: float | None
    opened_at: str
    note: str | None
    source: str | None
    run_id: str | None
    status: str
    result: str | None
    exit_price: float | None
    bars: int | None
    r_multiple: float | None
    closed_at: str | None = None
    graded_at: float | None = None

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def planned_r(self) -> float | None:
        if self.target is None or not self.risk:
            return None
        return abs(self.target - self.entry) / self.risk

    def line(self) -> str:
        d = "شراء 🟢" if self.direction == "long" else "بيع 🔴"
        risk = self.risk
        rr = self.planned_r
        head = (f"#{self.id} {d} {self.symbol} — دخل {self.entry:,.2f} · "
                f"وقف {self.stop:,.2f}")
        if self.target is not None:
            head += f" · هدف {self.target:,.2f}"
        if rr is not None:
            head += f" · R:R {rr:.2f}"
        head += f" · مخاطرة {risk:.2f} نقطة"
        if self.status == "open":
            tail = "  ⏳ مفتوحة"
        elif self.result == "target":
            tail = f"  ✅ الهدف — {self.r_multiple:+.2f}R"
        elif self.result == "stop":
            tail = f"  ❌ الوقف — {self.r_multiple:+.2f}R"
        else:
            tail = f"  ⚪ يدوي ({self.result or 'مغلقة'})"
            if self.r_multiple is not None:
                tail += f" — {self.r_multiple:+.2f}R"
        if self.note:
            tail += f"\n     ملاحظة: {self.note}"
        return head + tail


class Journal:
    """The owner's trade log. One table, same file as shared memory is not used:
    this is private to the owner, so it lives beside it, not inside the
    blackboard every agent reads."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conn()) as c:
            c.executescript(JOURNAL_SCHEMA)
            c.commit()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def add(self, symbol: str, direction: str, entry: float, stop: float,
            target: float | None = None, size: float | None = None,
            opened_at: str | None = None, note: str | None = None,
            source: str | None = None, run_id: str | None = None) -> int:
        """Record a trade. The stop is required: a trade without a stop cannot
        be graded in R, and a trade that cannot be graded is a story."""
        opened_at = opened_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with closing(self._conn()) as c:
            cur = c.execute(
                "INSERT INTO trades (symbol, direction, entry, stop, target, size, "
                "opened_at, note, source, run_id, status) VALUES (?,?,?,?,?,?,?,?,?,?,'open')",
                (symbol.upper(), direction, entry, stop, target, size, opened_at,
                 note, source, run_id))
            c.commit()
            return int(cur.lastrowid or 0)

    def _rows(self, where: str = "", args: tuple = ()) -> list[Trade]:
        q = "SELECT * FROM trades"
        if where:
            q += " WHERE " + where
        q += " ORDER BY id"
        with closing(self._conn()) as c:
            return [Trade(**{k: r[k] for k in r.keys()}) for r in c.execute(q, args)]

    def open_trades(self) -> list[Trade]:
        return self._rows("status='open'")

    def all_trades(self) -> list[Trade]:
        return self._rows()

    def close_manual(self, trade_id: int, exit_price: float,
                     note: str | None = None) -> bool:
        """Close a trade by hand at a price the owner reports. The R multiple is
        computed from the same entry and stop, so a manual close is comparable
        with an automatic one."""
        rows = self._rows("id=?", (trade_id,))
        if not rows:
            return False
        t = rows[0]
        risk = abs(t.entry - t.stop)
        r = None
        if risk:
            r = (exit_price - t.entry) / risk if t.direction == "long" \
                else (t.entry - exit_price) / risk
        self._do_write(
            "UPDATE trades SET status='closed', result='manual', exit_price=?, "
            "r_multiple=?, closed_at=?, graded_at=?, note=COALESCE(?, note) WHERE id=?",
            (exit_price, r, _utcnow(), time.time(), note, trade_id))
        return True

    def _do_write(self, sql: str, args: tuple) -> None:
        last: Exception | None = None
        for attempt in range(6):
            try:
                with closing(self._conn()) as c:
                    c.execute(sql, args)
                    c.commit()
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) and "busy" not in str(exc):
                    raise
                last = exc
                time.sleep(0.1 * (attempt + 1))
        if last is not None:
            raise last

    def grade(self, bars_by_tf: dict[str, list[dict]], tf: str = "15m",
              max_bars: int = 2000) -> int:
        """Walk every open trade forward and settle the ones that resolved.

        `bars_by_tf[tf]` must be CLOSED bars, oldest first, each with `open_time`,
        `high`, `low`. First touch wins; a bar that spans both takes the stop.
        Returns how many trades were settled."""
        bars = bars_by_tf.get(tf) or []
        if not bars:
            return 0
        settled = 0
        for t in self.open_trades():
            start = _parse(t.opened_at)
            if start is None:
                continue
            window = [(i, b) for i, b in enumerate(bars)
                      if (ts := _parse(b["open_time"])) is not None and ts >= start]
            if not window:
                continue
            long = t.direction == "long"
            risk = abs(t.entry - t.stop)
            target = t.target
            for n, (_i, b) in enumerate(window[:max_bars], 1):
                hit_stop = (b["low"] <= t.stop) if long else (b["high"] >= t.stop)
                hit_tgt = target is not None and (
                    (b["high"] >= target) if long else (b["low"] <= target))
                if hit_stop:
                    self._settle(t.id, "stop", t.stop, n, -1.0)
                    settled += 1
                    break
                if hit_tgt and target is not None:
                    r = (abs(target - t.entry) / risk) if risk else None
                    self._settle(t.id, "target", target, n, r)
                    settled += 1
                    break
        return settled

    def _settle(self, trade_id: int, result: str, exit_price: float,
                bars: int, r_multiple: float | None) -> None:
        self._do_write(
            "UPDATE trades SET status='closed', result=?, exit_price=?, bars=?, "
            "r_multiple=?, closed_at=?, graded_at=? WHERE id=?",
            (result, exit_price, bars, r_multiple, _utcnow(), time.time(), trade_id))

    def stats(self) -> dict:
        """The measured record. Expectancy in R is the headline: a high win rate
        at low R loses money, and only R shows that."""
        rows = [t for t in self.all_trades() if t.status == "closed"
                and t.r_multiple is not None]
        if not rows:
            return {"n": 0}
        r_values = [t.r_multiple for t in rows if t.r_multiple is not None]
        wins = [r for r in r_values if r > 0]
        losses = [r for r in r_values if r <= 0]
        total_r = sum(r_values)
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        return {
            "n": len(rows),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(rows),
            "expectancy_r": total_r / len(rows),
            "total_r": total_r,
            "profit_factor": (gross_win / gross_loss) if gross_loss else None,
            "open": len(self.open_trades()),
        }

    def report(self, lang: str = "ar") -> str:
        rows = self.all_trades()
        if not rows:
            return ("📒 سجل الصفقات فاضي.\n"
                    "أضف صفقة: desk journal add --direction long --entry 4063.6 "
                    "--stop 4055.3 --target 4083")
        L = ["📒 <b>سجل الصفقات</b>"]
        st = self.stats()
        for t in rows:
            L.append("  " + t.line())
        L.append("")
        if st.get("n"):
            pf = st.get("profit_factor")
            pf_s = f"{pf:.2f}" if pf is not None else "—"
            L.append(f"<b>الحصيلة:</b> {st['n']} صفقة مغلقة · "
                     f"{st['wins']} ربح / {st['losses']} خسارة "
                     f"({st['win_rate']:.0%}) · توقّع <b>{st['expectancy_r']:+.2f}R</b> "
                     f"· مجموع {st['total_r']:+.2f}R · معامل ربح {pf_s}")
            if st.get("open"):
                L.append(f"مفتوحة: {st['open']}")
            L.append("<i>التقييم بالـR مو بالنقاط — نسبة ربح عالية بمضاعف "
                     "صغير بتخسر فلوس.</i>")
        else:
            L.append("<i>ما في صفقات مغلقة بعد — ما في حصيلة تُقاس.</i>")
        return "\n".join(L)


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse(stamp: str | None):
    import datetime as dt
    if not stamp:
        return None
    s = stamp.replace("Z", "+00:00")
    if "+" not in s and s.count("-") <= 2:
        s += "+00:00"
    try:
        d = dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.UTC)
        return d
    except ValueError:
        return None
