"""Market session — when gold is actually tradable.

The desk runs on a schedule, and a schedule does not know that the market
closes. Before this module, a Saturday pass would wake all eight agents, read
whatever the broker last published, compute levels and a plan, and only the risk
agent could object -- and only if the feed happened to say `closed`. Two costs:
the owner gets a "reading" on a dead market, and the Actions minutes are spent
24/7 for a market that trades about five days a week.

Gold (XAUUSD) trades on the spot FX clock: it opens Sydney's Monday morning and
closes New York's Friday afternoon, with a daily break. This module states that
calendar once, in UTC, and answers one question: *is the market open right now?*
It is deliberately conservative -- an uncertain minute is treated as closed, so
the desk says nothing rather than reading a stale tape.

The weekly shape, in UTC:
  * Sunday   22:00 -> Monday 24:00   open
  * Monday   all day                 open
  * Tuesday  all day                 open
  * Wednesday all day                open
  * Thursday all day                 open
  * Friday   00:00 -> 21:00          open  (New York closes ~21:00 UTC)
  * Friday   21:00 -> Sunday 22:00   closed

The daily maintenance break (~21:00-22:00 UTC) is *not* modelled here: it moves
with daylight saving and its effect is a thinner tape, not a closed market, and
the quote's own `stale` flag already covers a feed that stops. Modelling a fixed
hour for it would close the market during London/NY overlap for half the year.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

# The market's own week, in UTC. Friday's close and Sunday's open are the two
# edges that matter; the rest of the week is continuously open.
FRIDAY_CLOSE_UTC = dt.time(21, 0)
SUNDAY_OPEN_UTC = dt.time(22, 0)


@dataclass(frozen=True)
class Session:
    """The answer, with enough detail to explain it to a person."""
    is_open: bool
    # A short Arabic reason, ready to print. Never English: the chat is Arabic.
    reason: str
    # The next moment the market is expected to be open, when it is closed.
    opens_at: dt.datetime | None = None

    def __bool__(self) -> bool:
        return self.is_open


def _next_open(now: dt.datetime) -> dt.datetime:
    """The next UTC moment the market opens, assuming the weekly shape above."""
    wd = now.weekday()          # Mon=0 .. Sun=6
    if wd == 4 and now.time() >= FRIDAY_CLOSE_UTC:      # Friday after the close
        days = 2                                          # -> Sunday
    elif wd == 5:                                         # Saturday
        days = 1                                          # -> Sunday
    elif wd == 6 and now.time() < SUNDAY_OPEN_UTC:      # Sunday before the open
        days = 0
    else:                                                 # any other closed moment
        days = (7 - wd) % 7
    target = (now + dt.timedelta(days=days)).replace(
        hour=SUNDAY_OPEN_UTC.hour, minute=SUNDAY_OPEN_UTC.minute,
        second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=7)
    return target


def market_session(now: dt.datetime | None = None) -> Session:
    """Whether the spot gold market is open at `now` (UTC, defaults to the clock).

    A naive datetime is read as UTC rather than rejected: the callers are all
    UTC-aware, and a naive value arriving here is a bug worth surfacing as a
    closed market, not a crash mid-run.
    """
    now = now or dt.datetime.now(dt.UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.UTC)
    else:
        now = now.astimezone(dt.UTC)
    wd = now.weekday()
    t = now.time()

    if wd == 4 and t >= FRIDAY_CLOSE_UTC:                 # Friday, after the bell
        return Session(False, "السوق مقفول (إغلاق الجمعة) — بيفتح الأحد",
                       _next_open(now))
    if wd == 5:                                           # Saturday
        return Session(False, "السوق مقفول (السبت) — بيفتح الأحد", _next_open(now))
    if wd == 6 and t < SUNDAY_OPEN_UTC:                   # Sunday, before the open
        return Session(False, "السوق مقفول (الأحد قبل الافتتاح)", _next_open(now))
    return Session(True, "السوق مفتوح", None)
