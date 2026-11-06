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
    closed_at TEXT, graded_at REAL,
    pending INTEGER NOT NULL DEFAULT 0, fill_time TEXT
);
"""

# Columns added after the first release. `CREATE TABLE IF NOT EXISTS` will not
# touch an existing file, so a new column is added here rather than silently
# missing on every store that predates it.
MIGRATIONS = (
    ("pending", "ALTER TABLE trades ADD COLUMN pending INTEGER NOT NULL DEFAULT 0"),
    ("fill_time", "ALTER TABLE trades ADD COLUMN fill_time TEXT"),
)


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
    pending: int = 0
    fill_time: str | None = None

    @property
    def is_pending(self) -> bool:
        return bool(self.pending) and self.status == "open"

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def planned_r(self) -> float | None:
        if self.target is None or not self.risk:
            return None
        return abs(self.target - self.entry) / self.risk

    @property
    def money(self) -> float | None:
        """Realised money. One lot is 100 oz, so 0.01 lot pays $1 per point.
        None when the size was never recorded -- a trade with no size cannot
        be turned into money, and guessing one would be inventing a result."""
        if self.size is None or self.exit_price is None:
            return None
        pts = (self.exit_price - self.entry) if self.direction == "long" \
            else (self.entry - self.exit_price)
        return pts * self.size * 100.0

    @property
    def weak_reward(self) -> bool:
        """True when the plan risks more than it stands to gain. A trade that
        pays less than 1R for 1R of risk needs a win rate above 50% just to
        break even, which is the opposite of an edge."""
        rr = self.planned_r
        return rr is not None and rr < 1.0

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
            if self.weak_reward:
                head += " ⚠️"
        head += f" · مخاطرة {risk:.2f} نقطة"
        if self.size is not None:
            head += f" · حجم {self.size:g}"
        if self.status == "open":
            if self.is_pending:
                tail = "  ⏳ أمر معلّق — لسا ما تنفّذ"
            else:
                tail = "  ⏳ مفتوحة"
        elif self.result == "target":
            tail = f"  ✅ الهدف — {self.r_multiple:+.2f}R"
        elif self.result == "stop":
            tail = f"  ❌ الوقف — {self.r_multiple:+.2f}R"
        else:
            tail = f"  ⚪ يدوي ({self.result or 'مغلقة'})"
            if self.r_multiple is not None:
                tail += f" — {self.r_multiple:+.2f}R"
        money = self.money
        if money is not None:
            tail += f" · {money:+,.2f}$"
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
            self._migrate(c)
            c.commit()

    def _migrate(self, c: sqlite3.Connection) -> None:
        """Add columns a pre-existing store lacks. A schema change must not lose
        the owner's record, so this is additive only -- never a drop or rewrite."""
        have = {r["name"] for r in c.execute("PRAGMA table_info(trades)")}
        for col, sql in MIGRATIONS:
            if col not in have:
                c.execute(sql)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def add(self, symbol: str, direction: str, entry: float, stop: float,
            target: float | None = None, size: float | None = None,
            opened_at: str | None = None, note: str | None = None,
            source: str | None = None, run_id: str | None = None,
            pending: bool = False) -> int:
        """Record a trade. The stop is required: a trade without a stop cannot
        be graded in R, and a trade that cannot be graded is a story.

        `pending=True` is a limit/stop order the market has not filled yet. It
        is stored, but `grade` will not settle it until price actually reaches
        the entry -- a target on an unfilled order is a plan, not a result."""
        opened_at = opened_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with closing(self._conn()) as c:
            cur = c.execute(
                "INSERT INTO trades (symbol, direction, entry, stop, target, size, "
                "opened_at, note, source, run_id, status, pending) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,'open',?)",
                (symbol.upper(), direction, entry, stop, target, size, opened_at,
                 note, source, run_id, 1 if pending else 0))
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

    def set_size(self, trade_id: int, size: float) -> bool:
        """Fill in a size the trade was recorded without. A trade with no size
        still grades in R, but its money cannot be reported."""
        if not self._rows("id=?", (trade_id,)):
            return False
        self._do_write("UPDATE trades SET size=? WHERE id=?", (size, trade_id))
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
            filled = not t.is_pending
            for n, (_i, b) in enumerate(window[:max_bars], 1):
                if not filled:
                    # A pending order is not a trade until price reaches it. Only
                    # the ENTRY counts as the fill; a bar that reaches both the
                    # entry and the stop is read as filled-then-stopped, the
                    # conservative order.
                    reached = (b["low"] <= t.entry) if long else (b["high"] >= t.entry)
                    if not reached:
                        continue
                    filled = True
                    self._mark_filled(t.id, b["open_time"])
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

    def _mark_filled(self, trade_id: int, fill_time: str) -> None:
        """The order was reached: it is now a live trade, graded from here."""
        self._do_write("UPDATE trades SET pending=0, fill_time=? WHERE id=?",
                       (fill_time, trade_id))

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
            "pending": len([t for t in self.open_trades() if t.is_pending]),
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
        pending = [t for t in rows if t.is_pending]
        live_open = [t for t in rows if t.status == "open" and not t.is_pending]
        if pending:
            L.append(f"⏳ أوامر معلّقة (ما تنفّذت بعد): {len(pending)}")
        if live_open:
            L.append(f"📈 صفقات مفتوحة: {len(live_open)}")
        if st.get("n"):
            pf = st.get("profit_factor")
            pf_s = f"{pf:.2f}" if pf is not None else "—"
            L.append(f"<b>الحصيلة:</b> {st['n']} صفقة مغلقة · "
                     f"{st['wins']} ربح / {st['losses']} خسارة "
                     f"({st['win_rate']:.0%}) · توقّع <b>{st['expectancy_r']:+.2f}R</b> "
                     f"· مجموع {st['total_r']:+.2f}R · معامل ربح {pf_s}")
            monies = [t.money for t in rows if t.money is not None]
            if monies:
                unpriced = len([t for t in rows if t.status == "closed"
                                and t.money is None])
                L.append(f"<b>المال:</b> {sum(monies):+,.2f}$"
                         + (f" · {unpriced} صفقة بلا حجم مسجّل (ما انحسبت)"
                            if unpriced else ""))
            L.append("<i>التقييم بالـR مو بالنقاط — نسبة ربح عالية بمضاعف "
                     "صغير بتخسر فلوس.</i>")
        else:
            L.append("<i>ما في صفقات مغلقة بعد — ما في حصيلة تُقاس. "
                     "الأوامر المعلّقة ما بتنحسب لحد ما تنفّذ.</i>")
        # A weak plan is a fact about the plan, not about the closed record, so
        # it is reported even before the first trade has been settled.
        weak = [t for t in rows if t.weak_reward and t.planned_r is not None]
        if weak:
            worst = min(weak, key=lambda t: t.planned_r or 0.0)
            L.append(f"⚠️ <b>{len(weak)} صفقة بمضاعف أقل من 1</b> "
                     f"(خاطرت أكتر مما كسبت) — الأسوأ #{worst.id} عند "
                     f"{worst.planned_r:.2f}R. هاد نمط تراكمي، مو صفقة وحدة.")
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
