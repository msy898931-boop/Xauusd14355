"""Base agent — the contract every specialist implements.

An agent reads the shared memory, computes its study, optionally asks the model
to phrase it, publishes the study back to the shared memory, and records a note
in its private memory. It never invents a number and never writes a fact it did
not compute.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Settings
from ..llm import LLM
from ..memory import AgentMemory, SharedMemory


@dataclass
class Study:
    """What an agent publishes. `stance` is bull/bear/neutral; `score` is signed
    (-1..+1) so the orchestrator can weight it; `confidence` is 0..1."""
    agent: str
    kind: str
    stance: str
    confidence: float
    score: float
    summary: str
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"agent": self.agent, "kind": self.kind, "stance": self.stance,
                "confidence": round(self.confidence, 3), "score": round(self.score, 3),
                "summary": self.summary, "payload": self.payload}


# ---- helpers -------------------------------------------------------
# A short list of terms the desk's own text legitimately carries in Latin
# letters. They are tickers and indicator names, not prose, and Arabic prose
# uses them as-is. Anything else in Latin letters is the model drifting back
# into English, which the chat must not show.
LATIN_OK = {
    "rsi", "macd", "atr", "ema", "sma", "bos", "choch", "fib", "fibs",
    "usd", "usdjpy", "eurusd", "xauusd", "xagusd", "btcusd", "usoil", "dxy",
    "spx", "jolts", "cpi", "nfp", "fomc", "gdp", "pmi", "ecb", "fed", "boj",
    "mm", "bn", "api", "llm", "usd/jpy", "r/r", "r:r",
}


def _latin_words(text: str) -> list[str]:
    """Latin-script words in `text`, lowercased.

    A word must be 3+ letters to count: "a" and "R" are used as symbols in
    Arabic prose, and flagging them would reject good text.
    """
    import re
    return [w.lower().strip("-'") for w in re.findall(r"[A-Za-z][A-Za-z'-]*", text)]


def looks_arabic(text: str | None, *, source: str = "") -> bool:
    """True when `text` is Arabic prose, not English that leaked through.

    The model is told to answer in Arabic, and it usually does. But it drifts:
    one live run wrote "bearish BOS" inside an otherwise Arabic sentence. A
    prompt cannot guarantee a language, so the check happens after the fact and
    the caller falls back to its deterministic Arabic sentence instead of
    shipping English to the chat.

    A Latin word is tolerated only if it is a known term (a ticker, an indicator)
    or if it also appears in `source` -- the facts the model was given. That
    second rule is what lets an untranslatable proper noun through (an event name
    like "JOLTS Job Openings" is in the input) while catching a word the model
    invented in English ("bearish" is not, because the input said "هابط").
    """
    if not text or not text.strip():
        return False
    if not any("\u0600" <= ch <= "\u06ff" for ch in text):
        return False
    allowed = LATIN_OK | set(_latin_words(source))
    for w in _latin_words(text):
        if len(w) < 3 or w in allowed:
            continue
        return False
    return True


class Agent:
    """Base class. Subclasses set `name`, `kind`, `weight` and implement `study`."""

    name: str = "agent"
    kind: str = "generic"
    # How much this specialist's signed score counts in the final tally.
    weight: float = 1.0
    # Frames this agent wants from the shared memory; None = all.
    frames: tuple[str, ...] | None = None

    def __init__(self, settings: Settings, shared: SharedMemory, llm: LLM):
        self.settings = settings
        self.shared = shared
        self.llm = llm
        self.memory = AgentMemory(settings.agent_dir(self.name))

    # ---- context -------------------------------------------------------
    def frames_data(self) -> dict[str, list[dict]]:
        """Closed bars per timeframe, reshaped from the shared store.

        The newest bar on every timeframe is still forming: its high/low/close
        move with the tick, so an indicator read off it changes between runs and
        is not a fact. Only bars the feed has marked closed are handed to an
        indicator; the live price comes from the quote, not from a partial bar.
        """
        want = self.frames or self.settings.timeframes
        out = {}
        for tf in want:
            rows = self.shared.load_candles(self.settings.symbol, tf)
            out[tf] = [{"t": r["open_time"], "o": r["open"], "h": r["high"],
                        "l": r["low"], "c": r["close"], "tv": r["tick_volume"],
                        "open": bool(r["is_open"])} for r in rows if not r["is_open"]]
        return out

    def quote(self) -> dict | None:
        return self.shared.load_quote(self.settings.symbol)

    def live_price(self) -> float | None:
        """The current price: the broker quote if it is usable, else the close of
        the most recent closed bar. Never the close of a bar still forming."""
        q = self.quote() or {}
        if isinstance(q.get("mid"), (int, float)) and not q.get("stale"):
            return float(q["mid"])
        for tf in ("1m", "5m", "15m", "1h"):
            rows = self.shared.load_candles(self.settings.symbol, tf)
            closed = [r for r in rows if not r["is_open"]]
            if closed:
                return float(closed[-1]["close"])
        return None

    def band(self) -> float:
        return self.settings.neutral_band

    def stance_of(self, score: float) -> str:
        """bull/bear/neutral by the shared band — so every agent classifies the
        same number the same way the orchestrator does."""
        if score > self.band():
            return "bull"
        if score < -self.band():
            return "bear"
        return "neutral"

    # ---- to override ---------------------------------------------------
    def study(self, run_id: str) -> Study:  # pragma: no cover - abstract
        raise NotImplementedError

    # ---- template method ----------------------------------------------
    def run(self, run_id: str) -> Study:
        s = self.study(run_id)
        self.shared.publish_study(run_id, s.agent, s.kind, s.stance,
                                  s.confidence, s.score, s.summary, s.payload)
        self.memory.remember(s.summary, verdict=s.stance)
        return s

    # ---- helpers -------------------------------------------------------
    def narrate(self, facts: str, instruction: str) -> str | None:
        """Ask the model to phrase a study the agent already computed.

        Returns None when the model is unavailable OR answers in English, so
        every caller's `or <deterministic Arabic sentence>` fallback fires. The
        language check is the point: a prompt cannot guarantee a language, and
        the chat is Arabic-only, so a drifted answer is treated as a failure
        rather than shipped. The model may not add numbers either, so a failure
        here is always safe.
        """
        system = (
            "أنت محلّل أسواق متخصص داخل مكتب تحليل آلي. "
            "سيُعطى لك وقائع محسوبة مسبقاً. صُغها في جملتين إلى ثلاث. "
            "القواعد: استعمل الأرقام المعطاة فقط؛ لا تخترع شيئاً؛ لا تَعِد بربح؛ "
            "إن غابت قيمة فقُل ذلك. "
            "اكتب الجواب بالعربية الفصحى المبسّطة حصراً، حتى لو كانت الوقائع "
            "بالإنجليزية — لا تستعمل أي كلمة إنجليزية، والأرقام وحدها مستثناة. "
            "اكتب الأرقام بالأرقام اللاتينية (0-9) وبفاصلة عشرية.")
        text = self.llm.complete(system, f"{instruction}\n\nالوقائع:\n{facts}")
        if text and not looks_arabic(text, source=facts):
            return None
        return text
