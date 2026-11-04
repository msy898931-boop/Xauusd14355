"""Webhook — the desk's inbound HTTP face.

WHY A WEBHOOK AT ALL
The desk has polled Telegram for its whole life, because it lived on a
scheduler and a scheduler cannot receive a request. Two things change on a real
host, and both point the same way:

  * Latency. A long poll answers a press when the poll returns, which is fine
    but not instant. A webhook is answered the moment Telegram pushes it.
  * Sleep. A free web host sleeps after ~15 minutes of no INBOUND traffic, and a
    long poll is entirely OUTBOUND -- it would never wake the host, and the host
    sleeping would freeze the very loop doing the polling. An inbound endpoint
    fixes both at once: Telegram's push is inbound traffic, and so is a plain
    uptime ping to `/health`.

The two transports are mutually exclusive on Telegram's side: while a webhook is
set, `getUpdates` is refused. So this is a mode, not an addition -- the daemon
runs EITHER the poll loop OR the webhook, never both, and switching to the
webhook clears any pending poll backlog.

WHAT IT IS NOT
An endpoint that answers arbitrary callers. Only the configured chat may spend a
model quota (enforced in `handle_update`), and the request itself must carry the
secret header Telegram was told to send -- a forged POST is rejected before it
reaches an action, even if the path leaks.
"""

from __future__ import annotations

import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__

# Telegram caps an update well below this; the limit exists so a hostile body
# cannot make the reader allocate without bound.
MAX_BODY = 1 << 20          # 1 MiB
# The path Telegram is pointed at. Not a secret -- the secret is the header --
# but a fixed path keeps the routing trivial and the surface small.
WEBHOOK_PATH = "/telegram"
HEALTH_PATH = "/health"


def build_marker() -> str:
    """The code revision this process is running, for `/health` to report.

    A static version string would drift from what is actually deployed -- the
    same "configured is not working" trap the desk names elsewhere. Render
    injects the deployed commit as `RENDER_GIT_COMMIT`; where there is none (a
    local run, a test) the package version is the honest answer, not a guess at
    a commit that was never built.
    """
    sha = os.environ.get("RENDER_GIT_COMMIT", "").strip()
    return sha[:12] if sha else f"v{__version__}"


class _Handler(BaseHTTPRequestHandler):
    # The server carries the collaborators, so the handler stays stateless and a
    # request cannot depend on which thread it landed on.
    server_version = "marketdesk"
    protocol_version = "HTTP/1.1"

    def log_message(self, *_) -> None:
        """Silence the default per-request line. The daemon prints its own events;
        a health ping every minute must not bury them."""
        return

    # ---- helpers ---------------------------------------------------------
    def _reply(self, code: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authorised(self) -> bool:
        secret = self.server.secret_token          # type: ignore[attr-defined]
        if not secret:
            # No secret configured: the endpoint is open. Refuse to pretend it
            # is protected -- the operator asked for no secret by not setting one.
            return True
        sent = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        # Constant-time compare: a byte-by-byte early exit leaks the token.
        return hmac.compare_digest(sent, secret)

    # ---- routes ----------------------------------------------------------
    def do_GET(self) -> None:                      # noqa: N802 - stdlib name
        """Liveness. A 200 here is what keeps a free host from sleeping, and it
        is also the honest answer to 'is the process up' -- the desk is silent by
        design, so its logs alone cannot be pinged."""
        if self.path.split("?")[0] in (HEALTH_PATH, "/"):
            # The marker is here so "is it deployed?" is answerable from outside:
            # the desk is silent by design, and a green /health on stale code is
            # indistinguishable from a green /health on the fix. This is the one
            # number that says which code is actually running.
            self._reply(200, {"ok": True, "service": "marketdesk",
                              "build": build_marker()})
            return
        self._reply(404, {"ok": False, "reason": "not found"})

    def do_POST(self) -> None:                     # noqa: N802 - stdlib name
        if self.path.split("?")[0] != WEBHOOK_PATH:
            self._reply(404, {"ok": False, "reason": "not found"})
            return
        if not self._authorised():
            # 403, not 401: Telegram retries a non-2xx, and a forged request has
            # no business being retried into a queue.
            self._reply(403, {"ok": False, "reason": "bad secret"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._reply(413, {"ok": False, "reason": "bad length"})
            return
        try:
            update = json.loads(self.rfile.read(length).decode())
        except (ValueError, UnicodeDecodeError):
            self._reply(400, {"ok": False, "reason": "bad json"})
            return
        if not isinstance(update, dict):
            self._reply(400, {"ok": False, "reason": "not an update"})
            return

        bot = self.server.bot                      # type: ignore[attr-defined]
        try:
            out = bot.answer_webhook_update(
                update, orchestrator=self.server.orchestrator)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - a bad update must not 500 into a retry storm
            self._reply(200, {"ok": False, "reason": exc.__class__.__name__})
            return
        # 200 even when the update was not ours: a 2xx is the acknowledgement, and
        # a 4xx would make Telegram redeliver an update we deliberately ignored.
        self._reply(200, out)


class WebhookServer:
    """The inbound endpoint, on its own thread so the desk keeps reading.

    The HTTP server blocks in `serve_forever`; the daemon's levels and readings
    must keep running beside it, so it is started as a daemon thread and stopped
    with the process.
    """

    def __init__(self, settings, bot=None, orchestrator=None, *, host: str = "0.0.0.0",
                 port: int = 8080, secret_token: str = ""):
        self.settings = settings
        self.host = host
        self.port = int(port)
        self.secret_token = secret_token
        self._bot = bot
        self._orchestrator = orchestrator
        self.httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def bot(self):
        if self._bot is None:
            from .bot import TelegramBot
            self._bot = TelegramBot(self.settings)
        return self._bot

    @property
    def orchestrator(self):
        if self._orchestrator is None:
            from .orchestrator import Orchestrator
            self._orchestrator = Orchestrator(self.settings)
        return self._orchestrator

    def start(self) -> str:
        """Bind, serve in the background, and return the local base URL."""
        httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        httpd.daemon_threads = True
        httpd.bot = self.bot                           # type: ignore[attr-defined]
        httpd.orchestrator = self.orchestrator         # type: ignore[attr-defined]
        httpd.secret_token = self.secret_token         # type: ignore[attr-defined]
        self.httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever,
                                        name="webhook", daemon=True)
        self._thread.start()
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
