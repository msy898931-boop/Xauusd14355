"""Notify — send an alert, and never send the same one twice.

The desk is silent most of the time (that is the point). A watcher that pings on
every scheduled run would train its reader to ignore it, so the rule is: alert
only when a run is *actionable*, and suppress a repeat of a signal already sent.

Secrets are read from the environment and never logged. The Telegram token and
chat id are used to build a URL and nothing else — no printing, no echoing.
"""

from __future__ import annotations

import html
import json
import urllib.error
import urllib.request

from .config import Settings


def escape(text: str) -> str:
    """Escape for Telegram's HTML parse mode.

    A model-written summary can contain `&` or `<` (e.g. "R&D", "a<b"), and
    Telegram rejects the whole message with 400 for an unknown entity. Escaping
    at the one place that sends means every caller can pass text as it is.
    """
    return html.escape(text, quote=False)


class Notifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.token = settings.telegram_token
        self.chat_id = settings.telegram_chat_id
        self.sent: list[dict] = []

    @property
    def available(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        """Send one message. Returns False when unconfigured or on failure —
        the caller decides whether that matters."""
        if not self.available:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        body = json.dumps({"chat_id": self.chat_id, "text": escape(text),
                           "parse_mode": "HTML",
                           "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode()).get("ok", False)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError):
            return False


class SignalLedger:
    """Remembers which signals were already alerted, so a watcher that runs every
    15 minutes does not send the same message four times an hour."""

    def __init__(self, path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, d: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2))
        tmp.replace(self.path)

    def seen(self, fingerprint: str) -> bool:
        return fingerprint in self._read()

    def record(self, fingerprint: str, note: str) -> None:
        d = self._read()
        d[fingerprint] = note
        # Keep the ledger bounded; old fingerprints are irrelevant once stale.
        if len(d) > 200:
            d = dict(list(d.items())[-200:])
        self._write(d)
