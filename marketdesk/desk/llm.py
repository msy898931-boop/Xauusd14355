"""LLM — the model layer, used only to narrate a study the agent already
computed. It never decides a number.

Every call is optional: if there is no key, or the model errors, the caller gets
`None` and must fall back to a deterministic sentence. That keeps the desk
runnable and honest with no key, which is how it is tested.

Groq is OpenAI-compatible. `max_tokens` covers the reasoning scratchpad AND the
answer on a reasoning model, so it is sized generously; a 400 from the model is
a failure that advances to the next candidate, never a silent empty string.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Sequence

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Groq sits behind Cloudflare, which answers urllib's default UA with 403
# (error 1010). Only a browser-like UA passes — the same wall the other feeds
# in this workspace hit. This is the one header the call cannot omit.
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class LLM:
    """A chain of Groq models, tried in order.

    Groq meters a *separate* daily token quota per model, so a spent quota on
    one model is not a spent quota on the desk: when a model answers 429 the
    client rotates to the next instead of giving up, and only reports itself
    unavailable once every model in the chain is cooling.
    """

    def __init__(self, key: str, models: str | Sequence[str], timeout: int = 40):
        self.key = key
        self.models = (models,) if isinstance(models, str) else tuple(models)
        self.timeout = timeout
        self.calls = 0
        self.failures = 0
        self.rate_limited = 0
        self.rotations = 0
        # model -> unix time until which it is quota-blocked.
        self._cooling: dict[str, float] = {}

    # ---- availability --------------------------------------------------
    def _ready(self) -> list[str]:
        now = time.time()
        return [m for m in self.models if now >= self._cooling.get(m, 0.0)]

    @property
    def available(self) -> bool:
        return bool(self.key) and bool(self._ready())

    @property
    def model(self) -> str:
        """The model the next call will use — the first that is not cooling."""
        ready = self._ready()
        return ready[0] if ready else self.models[0]

    def cool(self, model: str, seconds: float) -> None:
        self._cooling[model] = time.time() + seconds

    @staticmethod
    def _cooldown_seconds(body: str) -> float:
        """Read the provider's own retry hint when it gives one, else a minute."""
        import re
        m = re.search(r"try again in (?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?", body or "")
        if m and (m.group(1) or m.group(2)):
            return int(m.group(1) or 0) * 60 + float(m.group(2) or 0)
        return 60.0

    # ---- the call ------------------------------------------------------
    def complete(self, system: str, user: str, *, max_tokens: int = 900,
                 temperature: float = 0.2, retries: int = 2) -> str | None:
        """One completion from the first model that will answer. Returns None on
        any failure — never a fabricated text."""
        if not self.key:
            return None
        body = json.dumps({
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        })
        while True:
            ready = self._ready()
            if not ready:
                return None
            model = ready[0]
            for i in range(retries + 1):
                self.calls += 1
                payload = json.loads(body)
                payload["model"] = model
                try:
                    req = urllib.request.Request(
                        GROQ_URL, data=json.dumps(payload).encode(),
                        headers={"Authorization": f"Bearer {self.key}",
                                 "Content-Type": "application/json",
                                 "User-Agent": BROWSER_UA})
                    with urllib.request.urlopen(req, timeout=self.timeout) as r:
                        data = json.loads(r.read().decode())
                    content = data["choices"][0]["message"].get("content") or ""
                    if content.strip():
                        return content.strip()
                    self.failures += 1
                except urllib.error.HTTPError as e:
                    self.failures += 1
                    if e.code in (429, 402, 403):
                        # Quota spent or access refused: this model cannot help
                        # for a while, so cool it and try the NEXT model. A
                        # retry of the same model, or a sleep, cannot help.
                        try:
                            detail = e.read().decode()
                        except Exception:  # noqa: BLE001
                            detail = ""
                        self.cool(model, self._cooldown_seconds(detail))
                        self.rate_limited += 1
                        break                      # rotate, do not retry
                    if i < retries:
                        time.sleep(1.5 * (i + 1))
                except (urllib.error.URLError, KeyError, ValueError,
                        TimeoutError, IndexError):
                    self.failures += 1
                    if i < retries:
                        time.sleep(1.5 * (i + 1))
            else:
                # Retries exhausted on a non-quota error: the chain cannot do
                # better, so stop rather than hammer every model.
                return None
            if len(self._ready()) < len(ready):
                self.rotations += 1            # a model was just cooled

    def stats(self) -> dict:
        now = time.time()
        cooling = {m: round(max(0.0, until - now), 1)
                   for m, until in self._cooling.items() if until > now}
        return {"available": self.available, "model": self.model,
                "models": list(self.models), "cooling": cooling,
                "calls": self.calls, "failures": self.failures,
                "rate_limited": self.rate_limited, "rotations": self.rotations}
