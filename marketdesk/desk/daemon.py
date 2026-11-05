"""Daemon — the desk as one always-on process.

WHY THIS EXISTS
Until now the desk was two separate loops that never spoke to each other:
`serve` ran a full reading on an interval but never read the chat, and
`bot --listen` read the chat for a few minutes but never ran a reading. On a
scheduler that is the only shape available -- a runner is a fresh VM, so nothing
can stay resident. On a real host it is the wrong shape: the buttons are only
answered during the fraction of the time a listen window is open, and a level
touched between two passes is invisible.

This is the other shape. One process, resident, with three cadences that do not
compete:
  * the chat is polled continuously (a long poll, so a press is answered in about
    a second, whenever it happens);
  * the watched levels are checked every few seconds, so a level touched and
    rejected between two readings is caught;
  * a full reading runs every `read_every` (15 minutes by default), so the
    recommendation and the scoreboard stay current.

WHAT IT IS NOT
"Instant" is not on offer and is not claimed. The price feed is polled HTTP, not
a stream, so the finest honest cadence is the level interval (a few seconds). And
the market closes: from Friday's New York close to Sunday's open there is nothing
to watch, so the market jobs stand down and the process only serves the chat.
Sleeping through a closed market is not a limitation -- reading a frozen tape is
the failure this avoids.

FAILURE ISOLATION
A daemon that dies is worse than one that misses a tick, so every job is caught:
a level check that fails does not stop the reading, and a rejected token does not
kill the process -- it backs off and tries again. Each job reports its own
failure, so a silent process can always be told from a broken one.

TWO WAYS TO REACH THE CHAT
The chat is either polled or pushed to, never both -- Telegram refuses
`getUpdates` while a webhook is set. Polling needs no inbound address and works
anywhere; the webhook answers a press the instant it happens AND is inbound
traffic, which is what keeps a free host from sleeping. `--web` selects it, and
the choice is deliberate rather than automatic: the two are different transports
with different failure modes, and silently switching would hide the switch.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .config import Settings
from .session import market_session

if TYPE_CHECKING:                     # avoid importing the http stack at module load
    from .webhook import WebhookServer

# The level cadence: fine enough to catch a touch between two readings, coarse
# enough not to hammer the quote source (one HTTP call per check).
DEFAULT_LEVEL_EVERY = 10.0
# The reading cadence: the agents cost a model call, so this is minutes, not
# seconds. Matches the cron the desk used to run on.
DEFAULT_READ_EVERY = 900.0
# How long one long poll waits on Telegram's side. A press returns immediately;
# this is only the ceiling when the chat is quiet.
DEFAULT_POLL_WAIT = 50.0
# A floor on the loop, so a zero interval cannot spin the CPU.
MIN_LOOP_S = 1.0
# While the market is closed, re-check the session this often.
CLOSED_RECHECK_S = 60.0
# How often to ping our OWN public /health. A free host spins down after ~15
# minutes with no INBOUND traffic, and the inbound traffic that keeps it awake
# is Telegram's push -- which only arrives when someone presses a button. In a
# quiet hour there is no push, so the host sleeps, and the next press (or the
# webhook delivery Telegram retries) hits a cold or dead instance. Pinging
# ourselves every 10 minutes is inbound traffic on a timer we control, so the
# window can never elapse. It does NOT depend on the GitHub cron, which is
# best-effort and on this repo has fired twice a day against a 5-minute
# schedule -- far too rare to hold a 15-minute window open.
DEFAULT_PING_EVERY = 600.0
# A bounded tail of what was alerted and what failed, so a months-long process
# does not grow a list without end.
KEEP = 50


def _map_stamp_path(settings) -> Path:
    return settings.home / "map_sent.json"


def _load_map_date(settings) -> str | None:
    """The date the last map went out, from disk. Missing or unreadable means
    "nothing sent yet" -- a corrupt stamp costs one duplicate map, never a
    missing one, which is the right way round for a message the owner wants."""
    try:
        return json.loads(Path(_map_stamp_path(settings)).read_text()).get("date")
    except (OSError, ValueError, AttributeError):
        return None


def _save_map_date(settings, date: str) -> None:
    """Write the stamp atomically, so a crash mid-write cannot leave a torn file
    that reads as 'nothing sent' and re-sends the map."""
    p = Path(_map_stamp_path(settings))
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"date": date}))
        tmp.replace(p)
    except OSError:
        # Best-effort: a failed write costs a duplicate map on the next restart,
        # which is better than failing the send that already succeeded.
        pass


class Daemon:
    """One resident process: chat, levels, and readings, on three cadences."""

    def __init__(self, settings: Settings, *, level_every: float = DEFAULT_LEVEL_EVERY,
                 read_every: float = DEFAULT_READ_EVERY,
                 poll_wait_s: float = DEFAULT_POLL_WAIT,
                 ping_every: float = DEFAULT_PING_EVERY,
                 bot=None, orchestrator=None, web: bool = False):
        self.settings = settings
        self.level_every = max(MIN_LOOP_S, float(level_every))
        self.read_every = max(self.level_every, float(read_every))
        self.poll_wait_s = max(MIN_LOOP_S, float(poll_wait_s))
        # 0 disables the self-ping: a host that does not sleep does not need it,
        # and a caller should be able to say so rather than get a surprise call.
        self.ping_every = max(0.0, float(ping_every))
        # Webhook mode replaces the poll: the chat is pushed to, not read. The
        # daemon still runs the market jobs on their cadences either way.
        self.web = bool(web)
        self.webhook: WebhookServer | None = None
        self._bot = bot
        self._orchestrator = orchestrator
        self._notifier = None
        self.offset: int | None = None
        # Monotonic, not wall-clock: a clock step must not stall the cadence.
        # Start at -inf so the very first tick runs both jobs instead of sitting
        # idle for a full interval before doing anything.
        self.last_level = float("-inf")
        self.last_read = float("-inf")
        self.last_ping = float("-inf")
        # The daily map keys on the wall-clock date, not a monotonic interval:
        # "once a day" is a calendar fact, and a monotonic counter would drift
        # across a restart or a clock step. Read from disk, not just memory: the
        # host restarts the process on every deploy, and an in-memory date would
        # re-send the map each time. None means "nothing sent yet".
        self.last_map_date: str | None = _load_map_date(settings)
        self.maps = 0
        self._stop = False
        self._was_open: bool | None = None
        # Explicit typed counters, not a `dict[str, object]`: the dict form makes
        # every read an `object`, which mypy rightly refuses to add to a number.
        self.ticks = 0
        self.level_checks = 0
        self.reads = 0
        self.pings = 0
        self.alerts: list[str] = []
        self.errors: list[dict] = []

    # ---- lazily built collaborators --------------------------------------
    @property
    def notifier(self):
        if self._notifier is None:
            from .notify import Notifier
            self._notifier = Notifier(self.settings)
        return self._notifier

    @property
    def bot(self):
        if self._bot is None:
            from .bot import TelegramBot
            self._bot = TelegramBot(self.settings, self.notifier)
        return self._bot

    @property
    def orchestrator(self):
        if self._orchestrator is None:
            from .orchestrator import Orchestrator
            self._orchestrator = Orchestrator(self.settings)
        return self._orchestrator

    # ---- scheduling ------------------------------------------------------
    def due(self, now: float) -> list[str]:
        """Which jobs are due at monotonic time `now`. Pure, so a test can ask
        without sleeping."""
        jobs = []
        if now - self.last_level >= self.level_every:
            jobs.append("levels")
        if now - self.last_read >= self.read_every:
            jobs.append("read")
        if self.ping_every and now - self.last_ping >= self.ping_every:
            jobs.append("ping")
        return jobs

    def _next_due_in(self, now: float) -> float:
        due = [self.level_every - (now - self.last_level),
               self.read_every - (now - self.last_read)]
        if self.ping_every:
            due.append(self.ping_every - (now - self.last_ping))
        return max(0.0, min(due))

    def _self_ping(self) -> dict:
        """Fetch our own public `/health`, so the host sees inbound traffic.

        This is the honest answer to the free-tier sleep window: the only other
        inbound traffic is a Telegram push, and a quiet chat produces none, so
        the host sleeps and the next press meets a cold start (or, mid-sleep,
        Telegram's delivery fails outright and the update is retried later).

        It is best-effort by design: a failure is recorded and the loop moves on.
        If the ping itself cannot reach us, we are already asleep or the base is
        wrong -- nothing here can make that worse, and a hard failure would take
        down the market jobs that do work.
        """
        base = (self.settings.webhook_base
                or os.environ.get("RENDER_EXTERNAL_URL", "")).rstrip("/")
        if not base:
            # No public address known: a local process has nothing to ping.
            return {"skipped": "no public base"}
        from .webhook import HEALTH_PATH
        url = f"{base}{HEALTH_PATH}"
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=20) as r:
                ok = 200 <= r.status < 300
            return {"ok": ok, "url": url}
        except Exception as exc:  # noqa: BLE001 - a failed ping must not stop the desk
            self._note_error("ping", exc)
            return {"ok": False, "error": exc.__class__.__name__}

    # ---- jobs ------------------------------------------------------------
    def _level_check(self) -> dict:
        try:
            from .levels import watch_once
            out = watch_once(self.settings, notifier=self.notifier)
            self.level_checks += 1
            self.alerts.extend(out.get("alerted") or [])
            self.alerts.extend(out.get("warned") or [])
            self.alerts = self.alerts[-KEEP:]
            return out
        except Exception as exc:  # noqa: BLE001 - a failed check must not stop the reading
            self._note_error("levels", exc)
            return {"error": exc.__class__.__name__}

    def _map_due(self, now: dt.datetime | None = None) -> bool:
        """Whether the daily map should go out now. Pure, so a test can ask
        without waiting for a clock.

        Due once per calendar day (UTC), at or after `map_hour`. Sending only
        after the hour keeps it a morning map rather than a midnight one, and
        the date guard keeps a restart from re-sending a map already delivered.
        """
        now = now or dt.datetime.now(dt.UTC)
        if now.hour < self.settings.map_hour:
            return False
        return self.last_map_date != now.date().isoformat()

    def _send_map(self, now: dt.datetime | None = None) -> dict:
        """Compute and send the daily map. One message a day, by design: the map
        is context, and context repeated is noise."""
        try:
            from .actions import Actions
            text = Actions(self.settings, self.orchestrator).map()
            ok = self.notifier.send(text)
            now = now or dt.datetime.now(dt.UTC)
            # Mark the day sent only on success, so a failed send is retried on
            # the next tick rather than lost until tomorrow.
            if ok:
                self.last_map_date = now.date().isoformat()
                _save_map_date(self.settings, self.last_map_date)
                self.maps += 1
                self.alerts.append(f"map:{now.date().isoformat()}")
                self.alerts = self.alerts[-KEEP:]
            return {"sent": ok, "date": now.date().isoformat()}
        except Exception as exc:  # noqa: BLE001 - a failed map must not stop the desk
            self._note_error("map", exc)
            return {"error": exc.__class__.__name__}

    def _read(self) -> dict:
        try:
            out = self.orchestrator.watch_once(notifier=self.notifier)
            self.reads += 1
            if out.get("alerted"):
                self.alerts.append(f"read:{out.get('verdict')}")
                self.alerts = self.alerts[-KEEP:]
            return out
        except Exception as exc:  # noqa: BLE001 - one bad reading must not kill the process
            self._note_error("read", exc)
            return {"error": exc.__class__.__name__}

    def _note_error(self, job: str, exc: BaseException) -> None:
        self.errors.append({"job": job, "error": exc.__class__.__name__,
                            "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")})
        self.errors = self.errors[-KEEP:]

    def tick(self, now: float | None = None) -> dict:
        """One scheduling decision and the jobs it says are due.

        Never raises for a job failure. When the market is closed it runs no
        market job -- the chat is still served by the loop, but there is no tape
        to read and no level worth watching.
        """
        now = time.monotonic() if now is None else now
        out: dict = {"levels": None, "read": None, "closed": False}
        # The self-ping is NOT a market job: it must run even when the market is
        # closed, which is exactly when no push arrives and the host is most
        # likely to sleep. Running it before the session check is the point.
        if self.ping_every and now - self.last_ping >= self.ping_every:
            self.last_ping = now
            out["ping"] = self._self_ping()
            self.pings += 1
        if self.settings.respect_session:
            sess = market_session()
            if not sess.is_open:
                out["closed"] = True
                out["reason"] = sess.reason
                return out
        for job in self.due(now):
            if job == "levels":
                self.last_level = now
                out["levels"] = self._level_check()
            elif job == "read":
                self.last_read = now
                out["read"] = self._read()
        # The daily map is a wall-clock job, not a cadence one: it fires once a
        # day at `map_hour`, and only while the market is open (a map of a
        # frozen tape is not a plan for the day).
        if self._map_due():
            out["map"] = self._send_map()
        return out

    # ---- the loop --------------------------------------------------------
    def _start_webhook(self) -> dict:
        """Bring up the inbound endpoint and point Telegram at it.

        The endpoint is started FIRST and the webhook is registered SECOND, so a
        press cannot arrive at an address that is not listening yet. The public
        base comes from `DESK_WEBHOOK_BASE` (or Render's `RENDER_EXTERNAL_URL`);
        with neither, the endpoint still serves `/health` but Telegram is left
        alone -- there is no address to point it at.
        """
        from .webhook import WEBHOOK_PATH, WebhookServer

        base = (self.settings.webhook_base
                or os.environ.get("RENDER_EXTERNAL_URL", "")).rstrip("/")
        srv = WebhookServer(self.settings, bot=self.bot,
                            orchestrator=self.orchestrator,
                            port=self.settings.webhook_port,
                            secret_token=self.settings.webhook_secret)
        srv.start()
        self.webhook = srv
        out: dict = {"listening": srv.port, "base": base, "registered": False}
        if base:
            url = f"{base}{WEBHOOK_PATH}"
            out["registered"] = self.bot.set_webhook(
                url, secret_token=self.settings.webhook_secret)
            out["url"] = url
        return out

    def _poll_buttons(self, wait: float) -> None:
        """Answer the chat. A transport failure backs off rather than spinning;
        it must not end the process, because the process is meant to outlive it."""
        try:
            out = self.bot.poll(orchestrator=self.orchestrator, offset=self.offset,
                                budget_s=max(120.0, wait + 60.0),
                                wait_s=int(max(1.0, wait)))
        except Exception as exc:  # noqa: BLE001 - a bad poll must not kill the daemon
            self._note_error("bot", exc)
            time.sleep(min(5.0, wait))
            return
        if out.get("ok"):
            self.offset = out.get("next_offset", self.offset)
        else:
            # A rejected token or an unreachable API. Reported as itself, then
            # backed off -- retrying a dead token in a tight loop burns nothing
            # but looks like progress.
            self._note_error("bot", RuntimeError(str(out.get("reason") or "poll failed")))
            time.sleep(min(5.0, wait))

    def _log(self, out: dict) -> None:
        """Print a line only when something happened. A tick that found nothing is
        not news; a level check every ten seconds would otherwise bury the alerts
        it exists to surface."""
        if out.get("closed"):
            if self._was_open is not False:
                print(json.dumps({"state": "closed", "reason": out.get("reason")},
                                 ensure_ascii=False), flush=True)
            self._was_open = False
            return
        if self._was_open is False:
            print(json.dumps({"state": "open"}, ensure_ascii=False), flush=True)
        self._was_open = True

        payload: dict = {"tick": self.ticks}
        interesting = False
        lv = out.get("levels")
        if isinstance(lv, dict):
            if lv.get("alerted") or lv.get("warned"):
                payload["alerted"] = lv.get("alerted")
                payload["warned"] = lv.get("warned")
                interesting = True
            if lv.get("error"):
                payload["level_error"] = lv["error"]
                interesting = True
        rd = out.get("read")
        if isinstance(rd, dict):
            payload["read"] = {k: rd.get(k) for k in
                               ("verdict", "score", "alerted", "reason", "graded")}
            interesting = True
        if interesting:
            print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)

    def run_forever(self, *, seconds: float | None = None) -> dict:
        """Run until stopped, or for `seconds` (used by a test and a smoke run)."""
        started = time.monotonic()
        print(json.dumps({"state": "starting", "symbol": self.settings.symbol,
                          "level_every_s": self.level_every,
                          "read_every_s": self.read_every,
                          "transport": "webhook" if self.web else "poll"},
                         ensure_ascii=False), flush=True)
        if self.web:
            # The chat is pushed to, so the loop's only job is the market. A
            # failure to register is reported, not fatal: /health still answers,
            # which is the thing that keeps a free host awake.
            try:
                print(json.dumps({"webhook": self._start_webhook()},
                                 ensure_ascii=False), flush=True)
            except Exception as exc:  # noqa: BLE001 - a busy port must not kill the desk
                self._note_error("webhook", exc)
        while not self._stop:
            now = time.monotonic()
            if seconds is not None and now - started >= seconds:
                break
            out = self.tick(now)
            self.ticks += 1
            self._log(out)
            if self.web:
                # Nothing to poll: the endpoint answers in its own thread. Sleep
                # to the next job, but never longer than a health-ping cadence so
                # the process stays responsive to a stop.
                wait = max(MIN_LOOP_S, min(self._next_due_in(time.monotonic()),
                                           self.poll_wait_s))
                self._sleep(wait)
            else:
                wait = (self.poll_wait_s if out.get("closed")
                        else min(self.poll_wait_s,
                                 max(MIN_LOOP_S, self._next_due_in(time.monotonic()))))
                self._poll_buttons(wait)
        if self.webhook is not None:
            self.webhook.stop()
        return self.summary(uptime_s=round(time.monotonic() - started, 1))

    def _sleep(self, seconds: float) -> None:
        """Sleep in small steps so a stop signal is noticed promptly, not after a
        full interval."""
        end = time.monotonic() + max(0.0, seconds)
        while not self._stop and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))

    def summary(self, *, uptime_s: float | None = None) -> dict:
        return {"ticks": self.ticks,
                "level_checks": self.level_checks,
                "reads": self.reads,
                "pings": self.pings,
                "alerts": self.alerts[-20:],
                "errors": self.errors[-10:],
                "level_every_s": self.level_every,
                "read_every_s": self.read_every,
                "uptime_s": uptime_s}

    def stop(self, *_) -> None:
        """Ask the loop to finish the current tick and return. Safe as a signal
        handler: it sets a flag and nothing else."""
        self._stop = True

    def install_signals(self) -> None:
        """Stop cleanly on SIGTERM/SIGINT, so a container restart is not a kill
        mid-write. Signal handlers need the main thread; a caller off it is left
        to call `stop` itself."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self.stop)
            except (ValueError, OSError):
                pass
