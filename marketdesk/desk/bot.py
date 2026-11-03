"""Telegram bot — the desk on demand, driven from the chat.

WHY POLLING AND NOT A WEBHOOK
A webhook needs a long-lived HTTPS endpoint, and this repository has no server.
The desk is a CLI plus a watch loop, so the button is served the same way the
watch is: a scheduled pass that calls Telegram, rather than one that waits to be
called. That keeps the whole thing free and serverless, at the cost of latency
(a reply lands on the next pass, not instantly). The trade is deliberate.

WHY THE OFFSET IS CONFIRMED ON TELEGRAM'S SIDE
A runner is a fresh VM with no persistent disk, so a local "last seen" pointer
would be lost between runs and every pass would replay the same updates. Telegram
keeps the cursor instead: an update is confirmed only when a LATER `getUpdates`
call passes a higher `offset`. Reading an update does not confirm it, and there
is no separate "ack", so the pass must end by sending the offset back -- see
`confirm()`. With the cursor at Telegram the pass is idempotent and there is no
state of ours to lose.

Replies go to the chat the request came from, which is also the only chat the
bot will answer -- an unauthenticated button that spends a model quota is a way
to be drained by whoever finds the bot.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from .config import Settings
from .notify import Notifier, escape

# The Bot API base. Overridable so a test can point the whole client at a local
# stand-in server and exercise the real request path, the same way the data
# source base is overridable.
API = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")

# Telegram itself caps a message at 4096 characters; the desk's card is far
# shorter, so this is a guard against a pathological case, not the normal path.
MAX_LEN = 4000


class TelegramError(RuntimeError):
    """The Bot API could not be reached, or refused the request.

    Kept separate from "no presses": a rejected token and an empty queue must
    not look the same from outside, or a dead bot reads as a quiet one.
    """

# The menu, in reading order: act first, then read, then the reference tools.
# Two per row keeps it legible on a phone; eleven buttons is the most that fits
# above the fold without scrolling.
MENU: list[list[tuple[str, str]]] = [
    [("📊 تحليل الآن", "analysis"), ("🎯 التوصية", "recommend")],
    [("🎬 السيناريوهات", "scenarios"), ("📈 الخطة الكاملة", "plan")],
    [("📉 الشارت", "chart"), ("⏱️ السوق", "market")],
    [("🔔 المستويات", "levels"), ("📚 دراسة الذهب", "gold")],
    [("📋 آخر تقرير", "report"), ("📈 معدل النجاح", "hitrate")],
    [("❓ مساعدة", "help")],
]

# Plain text a person types, mapped to the same actions the buttons use. A
# message that matches nothing shows the menu rather than being ignored: an
# owner who types "تحليل" and hears nothing back is worse than one who is shown
# the buttons.
TEXT_ACTIONS: dict[str, str] = {
    "/start": "menu", "/menu": "menu", "/help": "help",
    "/analysis": "analysis", "/recommend": "recommend", "/report": "report",
    "/market": "market", "/hitrate": "hitrate", "/plan": "plan",
    "/levels": "levels", "/gold": "gold", "/chart": "chart",
    "/scenarios": "scenarios",
    "تحليل": "analysis", "توصية": "recommend", "تقرير": "report",
    "سوق": "market", "نجاح": "hitrate", "خطة": "plan", "مستويات": "levels",
    "ذهب": "gold", "شارت": "chart", "رسم": "chart", "قائمة": "menu",
    "سيناريو": "scenarios", "سيناريوهات": "scenarios", "احتمالات": "scenarios",
    "مساعدة": "help", "رجوع": "menu",
}


def action_for_text(text: str | None) -> str:
    """The action a typed message means, or `menu` when it matches nothing."""
    first = (text or "").strip().split()[0] if (text or "").strip() else ""
    return TEXT_ACTIONS.get(first.lower()) or TEXT_ACTIONS.get(first) or "menu"


def _keyboard() -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row]
                                for row in MENU]}


# Every answer carries a small nav row, so a reply is never a dead end: the
# reader can refresh what they are looking at or go back to the menu without
# hunting for a /command. `menu` itself gets the full menu instead, and the
# refresh button is dropped there (it would refresh the menu onto the menu).
def _nav_keyboard(action: str) -> dict:
    rows: list[list[dict]] = []
    if action not in ("menu", "help"):
        rows.append([{"text": "🔄 تحديث", "callback_data": action}])
    rows.append([{"text": "🏠 القائمة", "callback_data": "menu"}])
    return {"inline_keyboard": rows}


def keyboard_for(action: str) -> dict:
    """The reply markup for `action`: the full menu, or the nav row."""
    return _keyboard() if action in ("menu", "help") else _nav_keyboard(action)


def _clip(text: str, limit: int = MAX_LEN) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"


class TelegramBot:
    """Reads callback presses and answers them. One pass per call to `poll`."""

    def __init__(self, settings: Settings, notifier: Notifier | None = None):
        self.settings = settings
        self.token = settings.telegram_token
        self.chat_id = str(settings.telegram_chat_id or "")
        self.notifier = notifier or Notifier(settings)

    @property
    def available(self) -> bool:
        return bool(self.token)

    # ---- transport -----------------------------------------------------
    def _call(self, method: str, payload: dict, timeout: int = 20) -> dict:
        url = f"{API}/bot{self.token}/{method}"
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    def send(self, text: str, *, chat_id: str | None = None,
             keyboard: bool = False, markup: dict | None = None) -> bool:
        """One message. `text` is expected to be send-ready HTML: every action
        escapes the fields it interpolates, so escaping again here would print
        `&amp;amp;`. The only texts this class escapes are its own errors.

        `markup` is a ready reply-markup, used for the nav row. `keyboard=True`
        attaches the full menu and is kept for the menu action.
        """
        target = chat_id or self.chat_id
        if not (self.token and target):
            return False
        payload = {"chat_id": target, "text": _clip(text), "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        if markup is not None:
            payload["reply_markup"] = markup
        elif keyboard:
            payload["reply_markup"] = _keyboard()
        try:
            return bool(self._call("sendMessage", payload).get("ok"))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError,
                TimeoutError, KeyError):
            return False

    def send_photo(self, photo: bytes, caption: str = "", *,
                   chat_id: str | None = None, markup: dict | None = None) -> bool:
        """A photo with a caption, multipart-encoded.

        The chart is drawn to a PNG in memory, so this is the only body the
        client sends that is not JSON. `caption` is send-ready HTML, like
        `send`, and is capped at Telegram's 1024-character caption limit.
        """
        target = chat_id or self.chat_id
        if not (self.token and target and photo):
            return False
        boundary = "----deskchart"
        parts: list[bytes] = []

        def field(name: str, value: str) -> None:
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\""
                f"\r\n\r\n{value}\r\n".encode())

        field("chat_id", target)
        field("caption", _clip(caption, 1000))
        field("parse_mode", "HTML")
        if markup is not None:
            field("reply_markup", json.dumps(markup))
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\";"
            f" filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n".encode())
        parts.append(photo)
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        req = urllib.request.Request(
            f"{API}/bot{self.token}/sendPhoto", data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return bool(json.loads(r.read().decode()).get("ok"))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError,
                TimeoutError, KeyError):
            return False

    def get_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict]:
        """Read pending updates. The caller passes back the `offset` returned
        here, which is what tells Telegram the previous ones were delivered.

        Raises `TelegramError` rather than returning `[]` on a transport failure:
        an unreachable API and an empty queue must not be the same answer, or a
        dead token reads as a quiet chat."""
        payload: dict = {"timeout": timeout, "allowed_updates": ["callback_query", "message"]}
        if offset is not None:
            payload["offset"] = offset
        try:
            body = self._call("getUpdates", payload, timeout=timeout + 20)
        except urllib.error.HTTPError as e:
            raise TelegramError(f"getUpdates HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise TelegramError(f"getUpdates unreachable ({type(e).__name__})") from e
        except ValueError as e:
            raise TelegramError("getUpdates returned non-JSON") from e
        if not body.get("ok", True):
            raise TelegramError(f"getUpdates rejected: {body.get('description')}")
        return body.get("result") or []

    def confirm(self, offset: int) -> bool:
        """Tell Telegram that everything below `offset` is delivered.

        The Bot API confirms an update only when a LATER call passes a higher
        offset -- reading it is not enough, and there is no separate "ack". A
        scheduled run makes exactly one `getUpdates` call, so without this
        second call *nothing is ever confirmed* and every pass re-answers the
        whole backlog forever. One extra call, no state: with the queue drained
        it just returns an empty list.
        """
        try:
            self.get_updates(offset)
            return True
        except TelegramError:
            return False

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        """Stops the client's spinner. A failure here is cosmetic, never fatal."""
        try:
            self._call("answerCallbackQuery",
                       {"callback_query_id": callback_id, "text": _clip(text, 190)})
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError):
            pass

    def edit_menu(self, chat_id: str, message_id: int) -> bool:
        """Re-pin the menu to the pressed message, so the buttons survive."""
        try:
            return bool(self._call("editMessageReplyMarkup", {
                "chat_id": chat_id, "message_id": message_id,
                "reply_markup": _keyboard()}).get("ok"))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError):
            return False

    # ---- the one pass --------------------------------------------------
    def _send_chart(self, action: str, chat: str, markup: dict) -> bool:
        """Send the chart first when the action draws one.

        The photo goes before its text so the caption sits under the picture in
        the chat. A chart that cannot be drawn is not an error -- the text reply
        still goes -- so a missing history file leaves the chat working.
        """
        if action != "chart":
            return False
        try:
            from .actions import Actions
            out = Actions(self.settings).chart_png()
        except Exception:  # noqa: BLE001 - a chart must never kill a reply
            return False
        if not out:
            return False
        png, caption = out
        return self.send_photo(png, caption, chat_id=chat, markup=markup)

    def listen(self, orchestrator=None, *, seconds: int = 300,
               max_replies: int = 8, poll_s: int = 50,
               level_every: int = 60, levels=None) -> dict:
        """Stay connected and answer presses for `seconds`, not once.

        This is what removes the up-to-five-minute wait. `poll()` makes one
        `getUpdates` call and exits, so a press is answered on the NEXT scheduled
        pass; here the call blocks for `poll_s` seconds on Telegram's side and
        returns the moment something is pressed, then loops. A press is answered
        in about a second, and the runner still costs one job.

        A live session also checks the watched levels every `level_every`
        seconds. That is the whole reason a session is worth more than the cron
        here: a scheduled pass can only see the price at its own tick, so a level
        that is touched and rejected between two passes is missed entirely. The
        session sees it, and the ledger still guarantees each signal is sent
        once. A level check failure must never kill the session -- the presses
        are the primary job.

        The loop stops at the deadline so the job ends cleanly rather than being
        killed mid-answer, and it stops early on a transport failure -- retrying
        a rejected token for five minutes would just burn the runner.
        """
        if not self.available:
            return {"ok": False, "reason": "not configured"}
        from .levels import watch_once

        started = time.time()
        handled: list[str] = []
        offset: int | None = None
        passes = 0
        checks = 0
        alerts: list[str] = []
        next_check = started          # check the levels immediately, then on cadence
        try:
            while time.time() - started < seconds:
                if time.time() >= next_check:
                    try:
                        out = watch_once(self.settings, notifier=self.notifier,
                                         levels=levels)
                        checks += 1
                        alerts.extend(out.get("alerted") or [])
                        alerts.extend(out.get("warned") or [])
                    except Exception:  # noqa: BLE001 - a level check must not kill the session
                        pass
                    next_check = time.time() + max(5, level_every)
                left = seconds - (time.time() - started)
                out = self.poll(orchestrator, offset=offset,
                                budget_s=max(20.0, min(left, 240.0)),
                                max_replies=max_replies,
                                wait_s=min(poll_s, max(1, int(left))))
                passes += 1
                if not out.get("ok"):
                    return {**out, "handled": handled, "passes": passes,
                            "checks": checks, "alerts": alerts,
                            "listened_s": round(time.time() - started, 1)}
                offset = out.get("next_offset", offset)
                handled.extend(out.get("handled") or [])
        except KeyboardInterrupt:
            pass
        return {"ok": True, "handled": handled, "passes": passes,
                "checks": checks, "alerts": alerts,
                "listened_s": round(time.time() - started, 1),
                "next_offset": offset}

    def poll(self, orchestrator=None, *, offset: int | None = None,
             budget_s: float = 120.0, max_replies: int = 5,
             wait_s: int = 0) -> dict:
        """Read the pending presses and messages and answer each. Returns a small
        summary.

        `orchestrator` is injected so tests can supply a fake; when omitted the
        real one is built, which is what the scheduled workflow does.

        `max_replies` bounds a backlog. If the bot was quiet for a while the
        queue can hold many requests; answering every one would flood the chat,
        so the newest is answered and the older ones are folded into it, with the
        reply saying how many. That trades "every request gets its own reply" for
        "the chat stays readable" -- deliberately, and only when there is a
        backlog to fold.
        """
        from .actions import Actions

        if not self.available:
            return {"ok": False, "reason": "not configured"}
        started = time.time()
        try:
            updates = self.get_updates(offset, timeout=wait_s)
        except TelegramError as exc:
            # Report the failure as itself. Returning an empty pass would make a
            # rejected token look like a quiet chat.
            return {"ok": False, "reason": str(exc), "updates": 0, "handled": [],
                    "next_offset": offset}
        acts = Actions(self.settings, orchestrator=orchestrator)
        handled: list[str] = []
        next_offset = offset

        actionable = [i for i, u in enumerate(updates)
                      if u.get("callback_query") or u.get("message")]
        folded = set(actionable[:max(0, len(actionable) - max_replies)])
        notice = len(folded)

        for i, u in enumerate(updates):
            uid = u.get("update_id")
            if time.time() - started > budget_s:
                # Leave this press and every one after it unconfirmed, so
                # Telegram redelivers them on the next pass. Confirming a press
                # we never answered would drop it silently -- the one failure a
                # chat-driven desk must not have.
                if uid is not None:
                    next_offset = uid
                break
            if uid is not None:
                next_offset = uid + 1
            if i in folded:
                continue                      # confirmed, folded into the newest

            cq = u.get("callback_query")
            msg = u.get("message")
            if cq is not None:
                action = cq.get("data") or ""
                chat = str(((cq.get("message") or {}).get("chat") or {}).get("id") or "")
                callback_id = cq.get("id", "")
            elif msg is not None:
                # A typed message is a request too. Answering only button
                # presses meant a chat of 18 plain messages got total silence.
                action = action_for_text(msg.get("text"))
                chat = str((msg.get("chat") or {}).get("id") or "")
                callback_id = ""
            else:
                continue                      # nothing actionable in this update

            # Only the configured chat may spend a model quota.
            if self.chat_id and chat != self.chat_id:
                if callback_id:
                    self.answer_callback(callback_id, "غير مسموح")
                continue
            if callback_id:
                self.answer_callback(callback_id, "جارٍ التنفيذ…")
            try:
                text = acts.run(action)
            except Exception as exc:  # noqa: BLE001 - a bad action must not kill the pass
                text = escape(f"⚠️ تعذّر تنفيذ الطلب ({exc.__class__.__name__}).")
            if notice:
                # Said once, on the first reply of the pass, so a backlog is
                # acknowledged rather than silently swallowed.
                text = (f"<i>(تجاهلت {notice} رسالة أقدم — هذا ردّ على الأحدث)</i>"
                        f"\n\n{text}")
                notice = 0
            # Every answer gets a nav row (or the full menu), so a reply is never
            # a dead end. A chart is sent first when the action has one, so the
            # picture sits above its caption.
            markup = keyboard_for(action)
            self._send_chart(action, chat, markup)
            self.send(text, chat_id=chat, markup=markup)
            handled.append(action)
        # Confirm everything read, so the next pass starts after this batch.
        # A single scheduled run makes only one getUpdates call, and the Bot API
        # confirms an update only when a LATER call passes a higher offset --
        # reading it is not enough. Without this the backlog is re-answered on
        # every pass, forever.
        confirmed = False
        if updates and next_offset is not None:
            confirmed = self.confirm(next_offset)
        return {"ok": True, "updates": len(updates), "handled": handled,
                "folded": len(folded), "confirmed": confirmed,
                "next_offset": next_offset}
