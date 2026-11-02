"""News agent — reads the headlines and the economic calendar, then studies them.

This is the agent the orchestrator tells to "go gather news". It pulls headlines
(filtered to the symbol) and high-importance economic releases into the shared
memory, then classifies each headline as bullish / bearish / neutral for gold
using a transparent keyword map, and flags any high-impact event that is still
upcoming. The model may phrase the result, but the classification is computed.
"""

from __future__ import annotations

import datetime as dt
import json

from .. import sources
from .base import Agent, Study

# Transparent keyword map. A headline is scored by which bucket its words fall in.
BULLISH = ("rally", "rise", "gain", "surge", "soar", "record high", "safe haven",
           "rate cut", "dovish", "weak dollar", "inflation", "tension", "war",
           "crisis", "stimulus", "weak jobs", "recession")
BEARISH = ("fall", "drop", "slump", "plunge", "decline", "sell-off", "selloff",
           "rate hike", "hawkish", "strong dollar", "profit-taking", "profit taking",
           "risk-on", "risk on", "rebound in stocks", "strong jobs", "tapering")


def classify(text: str) -> int:
    t = (text or "").lower()
    b = sum(1 for w in BULLISH if w in t)
    s = sum(1 for w in BEARISH if w in t)
    if b > s:
        return 1
    if s > b:
        return -1
    return 0


_SYS = (
    "You label financial headlines for GOLD (XAUUSD). For each numbered headline "
    "return strict JSON: {\"labels\": [{\"i\": <int>, \"label\": "
    "\"bullish\"|\"bearish\"|\"neutral\"}]}. Bullish means gold is likely to rise. "
    "Output JSON only, no prose.")


def parse_labels(text: str, n: int) -> list[int] | None:
    """Parse the model's labels, or None if the reply is unusable. A malformed
    reply is discarded rather than guessed at."""
    if not text:
        return None
    try:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1])
        out = [0] * n
        for item in data.get("labels", []):
            i = int(item["i"])
            if 0 <= i < n:
                out[i] = {"bullish": 1, "bearish": -1}.get(item.get("label"), 0)
        return out
    except (ValueError, KeyError, TypeError, IndexError):
        return None


class NewsAgent(Agent):
    name = "news"
    kind = "news"
    weight = 1.5

    def gather(self) -> tuple[list[dict], list[dict]]:
        """Pull headlines + calendar into the shared memory. Honest on failure."""
        news, cal = [], []
        try:
            news = sources.fetch_news(symbol=self.settings.symbol, max_results=30)
            self.shared.save_news(news)
        except sources.SourceError:
            news = []          # a failed read is empty, not an all-clear
        try:
            cal = sources.fetch_calendar(importance="high", limit=60)
            self.shared.save_calendar(cal)
        except sources.SourceError:
            cal = []
        return news, cal

    def _label(self, news: list[dict]) -> tuple[list[int], list[int], dict]:
        """Label every headline. The keyword map is the computed baseline; the
        model is asked to label the same headlines, and where it disagrees the
        disagreement is recorded rather than silently preferred. If the model is
        unavailable or answers badly, the baseline stands alone.

        Both labellers are given the *same* text — title and description — so a
        disagreement is a disagreement about meaning, not about which fields each
        one happened to see. Feeding the model only the title would make every
        keyword hit in the description look like a model correction.
        """
        def text_of(n: dict) -> str:
            return f"{n.get('title', '')} {n.get('description', '')}".strip()

        batch = news[:25]
        keyword = [classify(text_of(n)) for n in news]
        labels = list(keyword)
        stats = {"model_used": False, "disagreements": 0, "checked": 0}
        if self.llm.available and batch:
            listing = "\n".join(f"{i}. {text_of(n)}" for i, n in enumerate(batch))
            # A reasoning model spends tokens thinking before it answers, so the
            # budget must cover the scratchpad: 800 returns an empty answer for
            # 25 headlines, 1500 completes the JSON.
            raw = self.llm.complete(_SYS, listing, max_tokens=1500, temperature=0.0) or ""
            model = parse_labels(raw, len(news[:25]))
            if model is not None:
                stats["model_used"] = True
                for i, m in enumerate(model):
                    stats["checked"] += 1
                    if m != keyword[i]:
                        stats["disagreements"] += 1
                    labels[i] = m          # the model's call supersedes the map
        return labels, keyword, stats

    def study(self, run_id: str) -> Study:
        news, cal = self.gather()
        if not news:
            news = self.shared.load_news(limit=30)

        labels, keyword, lstat = self._label(news)
        scored = []
        tally = 0
        for i, n in enumerate(news):
            v = labels[i] if i < len(labels) else 0
            tally += v
            scored.append({"title": n.get("title"), "publisher": n.get("publisher"),
                           "published": n.get("published"), "url": n.get("url"),
                           "vote": v, "keyword_vote": keyword[i] if i < len(keyword) else 0})
        norm = max(-1.0, min(1.0, tally / max(len(scored), 1) * 2))

        # Upcoming high-impact events (actual is null until published).
        now = dt.datetime.now(dt.timezone.utc)
        upcoming = []
        for e in cal:
            try:
                when = dt.datetime.fromisoformat(e["time"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if when >= now and e.get("importance") == "high":
                upcoming.append({"name": e.get("name"), "time": e.get("time"),
                                 "currency": e.get("currency"), "forecast": e.get("forecast")})
        upcoming.sort(key=lambda x: x["time"] or "")

        # A high-impact event inside the window makes the desk cautious, not
        # directional. The window is the configured one, not a flat 24h.
        window = dt.timedelta(hours=self.settings.event_window_h)
        soon = [u for u in upcoming
                if dt.datetime.fromisoformat((u["time"] or "").replace("Z", "+00:00")) - now <= window]
        conf = min(1.0, 0.3 + len(scored) / 40)
        stance = self.stance_of(norm)
        top = [s["title"] for s in scored[:5]]
        facts = (f"{len(scored)} headlines, net {tally:+d}; most recent: {top}; "
                 f"high-impact events within {self.settings.event_window_h:g}h: "
                 f"{[s['name'] for s in soon] or 'none'}")
        summary = self.narrate(facts, "Summarise the news flow and event risk.") or (
            f"News tone is {stance} ({tally:+d} net over {len(scored)} headlines). "
            f"Event risk in {self.settings.event_window_h:g}h: "
            f"{[s['name'] for s in soon] or 'none'}.")
        return Study(self.name, self.kind, stance, conf, norm, summary,
                     {"headlines": scored[:20], "net": tally,
                      "upcoming_events": upcoming[:10],
                      "imminent_events": soon,
                      "event_risk_24h": bool(soon),   # kept: older readers/tests
                      "event_window_h": self.settings.event_window_h,
                      "labelling": lstat})
