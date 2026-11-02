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
confirms updates with `offset` on `getUpdates`, so the runner advances the cursor
in the same call that reads it and lets Telegram forget what it delivered. That
makes the pass idempotent with no state of our own to lose.

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

MENU: list[list[tuple[str, str]]] = [
    [("📊 تحليل الآن", "analysis")],
    [("🎯 التوصية الحالية", "recommend"), ("📋 آخر تقرير", "report")],
    [("⏱️ حالة السوق", "market"), ("📈 معدل النجاح", "hitrate")],
]


def _keyboard() -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row]
                                for row in MENU]}


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
             keyboard: bool = False) -> bool:
        """One message. `text` is expected to be send-ready HTML: every action
        escapes the fields it interpolates, so escaping again here would print
        `&amp;amp;`. The only texts this class escapes are its own errors."""
        target = chat_id or self.chat_id
        if not (self.token and target):
            return False
        payload = {"chat_id": target, "text": _clip(text), "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        if keyboard:
            payload["reply_markup"] = _keyboard()
        try:
            return bool(self._call("sendMessage", payload).get("ok"))
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
    def poll(self, orchestrator=None, *, offset: int | None = None,
             budget_s: float = 120.0) -> dict:
        """Read the pending presses and answer each. Returns a small summary.

        `orchestrator` is injected so tests can supply a fake; when omitted the
        real one is built, which is what the scheduled workflow does.
        """
        from .actions import Actions

        if not self.available:
            return {"ok": False, "reason": "not configured"}
        started = time.time()
        try:
            updates = self.get_updates(offset)
        except TelegramError as exc:
            # Report the failure as itself. Returning an empty pass would make a
            # rejected token look like a quiet chat.
            return {"ok": False, "reason": str(exc), "updates": 0, "handled": [],
                    "next_offset": offset}
        acts = Actions(self.settings, orchestrator=orchestrator)
        handled: list[str] = []
        next_offset = offset
        for u in updates:
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
            cq = u.get("callback_query")
            if not cq:
                continue
            chat = str(((cq.get("message") or {}).get("chat") or {}).get("id") or "")
            # Only the configured chat may spend a model quota.
            if self.chat_id and chat != self.chat_id:
                self.answer_callback(cq.get("id", ""), "غير مسموح")
                continue
            action = cq.get("data") or ""
            self.answer_callback(cq.get("id", ""), "جارٍ التنفيذ…")
            try:
                text = acts.run(action)
            except Exception as exc:  # noqa: BLE001 - a bad action must not kill the pass
                text = escape(f"⚠️ تعذّر تنفيذ الطلب ({exc.__class__.__name__}).")
            self.send(text, chat_id=chat)
            handled.append(action)
        return {"ok": True, "updates": len(updates), "handled": handled,
                "next_offset": next_offset}
