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
        """Ask the model to phrase a study the agent already computed. The model
        is told it may not add numbers, so a failure or a stray figure is safe."""
        system = (
            "You are a specialist market analyst inside an automated desk. "
            "You will be given computed facts. Phrase them in 2-3 sentences. "
            "Rules: use ONLY the numbers given; invent nothing; never promise "
            "profit; if a value is missing, say so. Answer in the same language "
            "as the instruction.")
        return self.llm.complete(system, f"{instruction}\n\nFACTS:\n{facts}")
