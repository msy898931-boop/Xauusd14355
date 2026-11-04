"""Surprise agent — what the economic calendar actually printed.

The news agent gathers the calendar and the risk gate reads it to *veto* a trade
when a high-impact release is imminent. Neither of them ever reads the number
that comes out. So the desk is blind to the single most tradable event of the
day: the gap between what was expected and what was published.

This agent closes that gap. It reads the calendar rows the news agent already
stored, keeps the ones that (a) concern the traded instrument's currency, (b)
carry BOTH a forecast and an actual, and (c) are releases that actually transmit
to the dollar, and turns each into a signed score.

Two things this agent is careful about, because both are how an event feed
quietly lies:

  * A currency tag is not a transmission channel. `EIA Crude Oil Stocks` is
    tagged USD, but it is an oil-supply figure -- a crude build says nothing
    about the dollar and must not vote on gold. So the agent scores a curated
    allowlist of releases (the same "whitelist, not blacklist" rule the news
    agent uses for headlines) and reports what it dropped.
  * The sign is not uniform. For most US releases a print ABOVE forecast
    strengthens the dollar and is gold-negative. For a labour-weakness series it
    is the reverse: a higher Unemployment Rate or more Jobless Claims WEAKEN the
    dollar and are gold-positive. Each allowlisted release carries its own sign.

The reaction function is the standard FX one, applied consistently:
    USD-positive surprise (dollar-strength series) -> gold down
    USD-negative surprise (dollar-weakness series) -> gold up
A surprise is measured relative to the forecast, because a miss on an index that
sits near 50 is not the same size as a miss on a rate in the single digits.

Honest limits, stated where they belong:
  * the sign of the reaction is the *immediate* FX one. A hot inflation print is
    a long-run gold positive and a same-day gold negative; this agent models the
    day, which is the horizon the desk trades.
  * a surprise decays. A print two hours old has been priced; the window is
    bounded (DESK_SURPRISE_WINDOW_H) and the score is weighted by age with a
    half-life of a third of that window, so the cutoff is a slope, not a cliff,
    and the payload says how old the newest print is.
  * with no fresh print the agent does not vote at all -- a neutral stance from
    "nothing happened" is not the same claim as a neutral stance from balance,
    and conflating them is how a non-event becomes a permanent bias.
"""

from __future__ import annotations

import datetime as dt

from .. import arabic as ar
from ..config import base_currency
from .base import Agent, Study

# A relative surprise this large is treated as a full-conviction vote. Chosen so
# a 1.3% PMI miss reads as a moderate signal and a 5% miss saturates -- not so
# tight that every rounding error votes, not so loose that everything saturates.
SURPRISE_FULL = 0.05

# Below this absolute score an event is noise: a forecast that was met exactly
# (0.0005 of a difference) must not become a vote.
MIN_EVENT_SCORE = 0.02

# The releases that actually move the dollar, and the direction of that move.
# The bool is `higher_print_is_dollar_positive`: True when a print ABOVE forecast
# strengthens the dollar (gold-negative), False when a print above forecast
# weakens it (gold-positive). Matched as a lowercase substring of the feed name.
#
# The list is deliberately short. A release that is not here is not scored: the
# desk would rather report "no signal" than let an oil inventory or a housing
# figure it cannot sign vote on gold.
USD_EVENTS: tuple[tuple[str, bool], ...] = (
    # Labour strength: a beat is dollar-positive.
    ("non-farm payroll", True), ("nonfarm payroll", True), ("nfp", True),
    ("adp", True), ("jolts", True), ("average hourly earnings", True),
    ("employment change", True), ("participation rate", True),
    # Labour weakness: a higher print is dollar-negative, so the sign flips.
    ("unemployment rate", False), ("jobless claims", False),
    # Activity and inflation: a beat is dollar-positive (hawkish).
    ("ism manufacturing pmi", True), ("ism services pmi", True),
    ("ism non-manufacturing pmi", True), ("ism manufacturing prices", True),
    ("ism non-manufacturing prices", True), ("ism prices paid", True),
    ("manufacturing pmi", True), ("services pmi", True),
    ("cpi", True), ("ppi", True), ("pce", True),
    ("retail sales", True), ("gdp", True),
    ("consumer confidence", True), ("consumer sentiment", True),
    ("durable goods", True), ("industrial production", True),
    ("existing home sales", True), ("new home sales", True),
    ("building permits", True), ("housing starts", True),
    ("federal funds rate", True), ("fed interest rate", True), ("fomc", True),
)


def _num(value) -> float | None:
    """A calendar figure as a float, or None. `actual` is null until published,
    and 0.0 is a legitimate reading -- so truthiness must never be the test."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def event_surprise(actual, forecast) -> float | None:
    """Signed surprise of one release, in [-1, +1], or None when it cannot be
    measured.

    None -- not 0.0 -- when the forecast is missing or zero: there is no
    expectation to beat, so there is no surprise. Returning 0.0 would report
    "the number came in as expected" about an event with no expectation at all.
    """
    a, f = _num(actual), _num(forecast)
    if a is None or f is None or f == 0:
        return None
    rel = (a - f) / abs(f)
    return max(-1.0, min(1.0, rel / SURPRISE_FULL))


def dollar_positive(name: str) -> bool | None:
    """Whether a print above forecast strengthens the dollar, or None when the
    release is not one that transmits to the dollar.

    None is the important return: it means "do not score this", and it is why an
    EIA oil-inventory figure -- USD-tagged but not a dollar event -- cannot vote.
    """
    t = (name or "").lower()
    for key, sign in USD_EVENTS:
        if key in t:
            return sign
    return None


def gold_score(name: str, surprise: float, currency: str, base_ccy: str) -> float:
    """Map one release to a gold score, or 0.0 when it should not score.

    Only releases in the instrument's own currency can move it (USD for gold) --
    the same rule the risk gate uses. A dollar-positive surprise is gold-negative,
    so its sign is flipped; a dollar-negative series (unemployment, claims) keeps
    its sign.
    """
    if currency != base_ccy:
        return 0.0
    dp = dollar_positive(name)
    if dp is None:
        return 0.0
    return -surprise if dp else surprise


class SurpriseAgent(Agent):
    name = "surprise"
    kind = "event_surprise"
    # Below the structural agents: it is a real signal, but a point-in-time one
    # that is silent most of the day, so it must not dominate the standing bias.
    weight = 1.2

    def _fresh_events(self, now: dt.datetime) -> tuple[list[dict], int]:
        """Calendar rows published recently, in the traded currency, that both
        transmit to the dollar and can be measured. Returns (scored, dropped).

        `load_calendar` returns every row the desk has ever seen, so the time
        filter is what keeps an old print from voting forever; `dropped` counts
        the in-currency rows that were skipped as non-dollar or unmeasurable, so
        the payload shows what the agent chose not to read.
        """
        window = dt.timedelta(hours=self.settings.surprise_window_h)
        base_ccy = base_currency(self.settings.symbol)
        out, dropped = [], 0
        for e in self.shared.load_calendar():
            when = _parse_time(e.get("time"))
            if when is None or when > now or (now - when) > window:
                continue
            if (e.get("currency") or "").upper() != base_ccy:
                continue
            s = event_surprise(e.get("actual"), e.get("forecast"))
            if s is None:
                dropped += 1
                continue
            score = gold_score(e.get("name") or "", s, base_ccy, base_ccy)
            if score == 0.0:
                dropped += 1
                continue
            if abs(score) < MIN_EVENT_SCORE:
                continue
            out.append({"name": e.get("name"), "currency": e.get("currency"),
                        "time": e.get("time"), "actual": e.get("actual"),
                        "forecast": e.get("forecast"), "previous": e.get("previous"),
                        "surprise": round(s, 4), "score": round(score, 4),
                        "age_h": round((now - when).total_seconds() / 3600, 2)})
        out.sort(key=lambda x: x["time"] or "", reverse=True)
        return out, dropped

    def study(self, run_id: str) -> Study:
        now = dt.datetime.now(dt.UTC)
        events, dropped = self._fresh_events(now)

        if not events:
            # No fresh print is a fact worth stating, not a zero to average in.
            summary = ("ما في رقم اقتصادي جديد يخصّ عملة الأداة خلال "
                       f"{self.settings.surprise_window_h:g} ساعة — لا قراءة للمفاجآت.")
            return Study(self.name, self.kind, "neutral", 0.0, 0.0, summary,
                         {"events": [], "fresh": 0, "dropped": dropped,
                          "window_h": self.settings.surprise_window_h})

        # Recency-weighted mean, for two reasons that pull the same way:
        #   * several releases in one slot are one macro moment, so the mean --
        #     not the sum -- is what avoids counting a correlated pair twice;
        #   * a surprise is priced within the first hour or two, so a print near
        #     the window edge must not weigh the same as a fresh one. The age is
        #     already computed; ignoring it here would report a staleness the
        #     score did not act on.
        # The half-life is derived from the window (a third of it), so there is
        # no second knob to keep in sync: the window is the hard cutoff and the
        # decay is what makes that cutoff continuous rather than a cliff.
        half_life = max(self.settings.surprise_window_h / 3.0, 0.25)
        for e in events:
            e["weight"] = round(0.5 ** (e["age_h"] / half_life), 4)
        wsum = sum(e["weight"] for e in events) or 1.0
        score = sum(e["score"] * e["weight"] for e in events) / wsum
        score = max(-1.0, min(1.0, score))
        mean_abs = sum(abs(e["score"]) * e["weight"] for e in events) / wsum
        conf = min(1.0, 0.4 + 0.3 * len(events) + 0.3 * mean_abs)
        stance = self.stance_of(score)

        strongest = max(events, key=lambda e: abs(e["score"]))
        age = strongest["age_h"]
        facts = (f"{len(events)} رقم جديد؛ الميل للذهب {score:+.3f}؛ "
                 f"أقوى مفاجأة {ar.event_name(strongest['name'])} "
                 f"(فعلي {strongest['actual']} مقابل متوقع {strongest['forecast']}، "
                 f"عمرها {age:g} ساعة)")
        summary = self.narrate(facts, "لخّص مفاجآت الأرقام الاقتصادية وأثرها على الذهب.") or (
            f"{len(events)} رقم اقتصادي جديد في عملة الأداة؛ الميل للذهب "
            f"{ar.stance(stance)} ({score:+.2f}). "
            f"أقوى مفاجأة: {ar.event_name(strongest['name'])} — فعلي "
            f"{strongest['actual']} مقابل متوقع {strongest['forecast']}.")
        return Study(self.name, self.kind, stance, conf, score, summary,
                     {"events": events, "fresh": len(events), "dropped": dropped,
                      "mean_abs": round(mean_abs, 4),
                      "window_h": self.settings.surprise_window_h,
                      "newest_age_h": age})


def _parse_time(value) -> dt.datetime | None:
    if not value:
        return None
    try:
        when = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return when
