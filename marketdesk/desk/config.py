"""Settings. One place for paths, the data source, and the LLM.

Every value has an env override so the desk can run keyless, with no model, on
any host. The shared store and each agent's private store live under one root
(`DESK_HOME`) so a single directory holds all durable state.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Data source: biquote (keyless MT5 broker feed). The base is overridable so a
# test or a future mirror can be pointed at without touching call sites.
BIQUOTE_BASE = os.environ.get("DESK_BIQUOTE_BASE", "https://biquote.io")
BIQUOTE_UA = "Mozilla/5.0 (compatible; MarketDesk/0.1)"

# The symbol the desk reads by default; XAUUSD is what the owner trades.
DEFAULT_SYMBOL = os.environ.get("DESK_SYMBOL", "XAUUSD")
# Timeframes biquote serves, coarse -> fine. The orchestrator reads all of them.
TIMEFRAMES = ("1d", "4h", "1h", "30m", "15m", "5m", "1m")

# |score| below this is neutral, not a call. One constant so an agent's stance
# and the orchestrator's verdict can never disagree about what "neutral" means.
NEUTRAL_BAND = float(os.environ.get("DESK_NEUTRAL_BAND", "0.15"))

# The currency whose high-impact events move each instrument. Gold and silver
# are quoted in dollars; the FX majors are read off their first leg. Shared, not
# owned by an agent: the risk gate decides whether to stand down and the scenario
# builder decides which event to name, and when those two disagreed the scenarios
# told a gold trader about New Zealand employment.
CURRENCY = {
    "XAUUSD": "USD", "XAGUSD": "USD", "BTCUSD": "USD", "USOIL": "USD",
    "EURUSD": "EUR", "GBPUSD": "GBP", "USDJPY": "USD", "AUDUSD": "AUD",
}


def base_currency(symbol: str) -> str:
    """The currency an event must concern to matter to `symbol`."""
    return CURRENCY.get(symbol.upper(), symbol.upper()[:3])

# The model chain, best-first. Groq meters a separate daily token quota per
# model, so listing several turns one model's spent quota into a rotation rather
# than a silent fall back to computed prose. All four answered a 25-headline
# labelling call; the reasoning model leads and the cheap fast ones back it up.
DEFAULT_MODELS = ("openai/gpt-oss-120b", "openai/gpt-oss-20b",
                  "qwen/qwen3.8-27b", "allam-2-7b")


def _models_from_env() -> tuple[str, ...]:
    """DESK_MODELS wins (comma-separated); DESK_MODEL replaces just the head."""
    raw = os.environ.get("DESK_MODELS")
    if raw:
        return tuple(m.strip() for m in raw.split(",") if m.strip())
    head = os.environ.get("DESK_MODEL")
    if head:
        return (head, *(m for m in DEFAULT_MODELS if m != head))
    return DEFAULT_MODELS


@dataclass
class Settings:
    symbol: str = DEFAULT_SYMBOL
    home: Path = field(default_factory=lambda: Path(
        os.environ.get("DESK_HOME", Path(__file__).resolve().parent.parent / "data")))
    # Groq is the narrator/model layer. Unset key -> the desk still runs, agents
    # fall back to their computed studies and say so.
    groq_key: str = field(default_factory=lambda: os.environ.get("GROQ_API_KEY", ""))
    # Groq meters a separate daily quota per model, so the desk keeps a chain and
    # rotates down it when one model's quota is spent. `DESK_MODELS` overrides the
    # whole list; `DESK_MODEL` overrides just the head.
    models: tuple[str, ...] = field(default_factory=lambda: _models_from_env())
    # A hard ceiling on wall-clock for one full run, so "fast" is a property of
    # the build, not a hope. Agents run concurrently inside this budget.
    budget_s: float = float(os.environ.get("DESK_BUDGET_S", "60"))
    offline: bool = os.environ.get("DESK_OFFLINE") == "1"
    # One source of truth for "what counts as neutral". Agents and the
    # orchestrator both read this; a hardcoded 0.15 in each of them is how a
    # specialist's stance and the final verdict drift apart.
    neutral_band: float = NEUTRAL_BAND
    # Coarse -> fine. Agents may request a subset via their own `frames`.
    timeframes: tuple[str, ...] = TIMEFRAMES
    # Symbols the correlation agent watches beside the traded one.
    peers: tuple[str, ...] = ("XAGUSD", "EURUSD", "BTCUSD", "USOIL", "USDJPY")
    # How close a high-impact event must be, in hours, to close the gate. The
    # window is deliberately short: an event 20 hours out is not a reason to
    # refuse a setup that resolves on the 15-minute chart.
    event_window_h: float = float(os.environ.get("DESK_EVENT_WINDOW_H", "6"))
    # How long a published economic number keeps voting. A surprise is priced
    # fast; an eight-hour-old print is history, not a signal. Bounded so the
    # surprise agent cannot carry yesterday's release into today's bias.
    surprise_window_h: float = float(os.environ.get("DESK_SURPRISE_WINDOW_H", "6"))
    # Alerting. Empty by default: an unconfigured desk stays silent, never
    # half-alerting. Values come from the environment and are never logged.
    telegram_token: str = field(
        default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(
        default_factory=lambda: os.environ.get("TELEGRAM_CHAT_ID", ""))
    # A signal is worth waking someone for only above this conviction.
    alert_score: float = float(os.environ.get("DESK_ALERT_SCORE", "0.25"))
    # How old the newest reading may be before the desk refuses to alert. A live
    # call is a statement about the market NOW; a directional call computed from
    # a feed that stopped hours ago is a stale number dressed as a signal. Only
    # an alert is gated -- the run, the card and the scoreboard still work on old
    # data, so the desk can still be read. Default 3h: wide enough that a normal
    # hourly bar and a best-effort schedule never trip it, narrow enough to catch
    # an outage before the desk acts on it.
    max_data_age_s: float = float(os.environ.get("DESK_MAX_DATA_AGE_S", str(3 * 3600)))
    # How often to re-send "the plan is still on track" while it runs. A change
    # of status is always sent; this is the quiet reassurance in between, so the
    # owner knows the desk is still watching rather than asleep.
    plan_heartbeat_s: float = float(os.environ.get("DESK_PLAN_HEARTBEAT_S",
                                                   str(2 * 3600)))
    # How long a plan may sit unfilled before it is abandoned. A trigger that has
    # not fired in a day is not a plan any more -- the market has moved on and
    # the level is stale. Only applies while `waiting`; a filled plan runs until
    # its stop or target.
    plan_expiry_s: float = float(os.environ.get("DESK_PLAN_EXPIRY_S",
                                                str(24 * 3600)))
    # A setup is only worth stating if reward exceeds risk by this factor. A plan
    # whose nearest target sits closer than the stop is a losing proposition even
    # when the direction is right.
    min_rr: float = float(os.environ.get("DESK_MIN_RR", "1.5"))
    # The daily map is sent once a day at this UTC hour, once the market is
    # open. 7 UTC is 09:00-10:00 in the owner's timezone (UTC+2/+3, the Levant),
    # before the London session -- the map is a plan for the day, so it should
    # arrive before the day's range is set, not after. Set DESK_MAP_HOUR to move
    # it; a day already sent is not re-sent on a restart.
    map_hour: int = int(os.environ.get("DESK_MAP_HOUR", "7"))
    # Stand down when the market is closed (the weekend, and Friday's close).
    # The schedule is 24/7 and does not know the market closes; without this the
    # desk reads a dead tape on a Saturday and spends the run for nothing. Set
    # DESK_RESPECT_SESSION=0 to run the full desk on demand regardless.
    respect_session: bool = os.environ.get("DESK_RESPECT_SESSION", "1") != "0"
    # ---- the inbound endpoint (webhook transport) ------------------------
    # When set, the daemon serves an HTTP endpoint and Telegram PUSHES updates
    # to it, instead of being polled. Two wins: a press is answered the moment it
    # happens, and the endpoint is INBOUND traffic -- which is what stops a free
    # host from sleeping (a long poll is entirely outbound and never would).
    # The two transports are mutually exclusive on Telegram's side; switching to
    # the webhook clears the poll backlog.
    webhook_base: str = field(
        default_factory=lambda: os.environ.get("DESK_WEBHOOK_BASE", ""))
    # The port the endpoint binds. Most hosts inject PORT; DESK_PORT overrides.
    webhook_port: int = field(default_factory=lambda: int(
        os.environ.get("DESK_PORT") or os.environ.get("PORT") or "8080"))
    # A shared secret Telegram echoes back in a header on every push, so a forged
    # POST is rejected before it reaches an action. Empty -> the endpoint is
    # open, which is only sane when nothing else can reach it.
    webhook_secret: str = field(
        default_factory=lambda: os.environ.get("DESK_WEBHOOK_SECRET", ""))
    # Watch the scenario triggers (breakout/breakdown) as well as the plan. A
    # desk that says "no trade" still prints scenarios, so a plan-only watcher
    # goes silent exactly when the market is deciding something. Set
    # DESK_WATCH_SCENARIOS=0 to watch the plan alone.
    watch_scenarios: bool = os.environ.get("DESK_WATCH_SCENARIOS", "1") != "0"
    # ---- position sizing -------------------------------------------------
    # The account the desk sizes every plan for. A level and a stop say where to
    # act; they say nothing about how many lots, and a small account is where
    # that gap is fatal. 0 disables the money view entirely.
    balance: float = float(os.environ.get("DESK_BALANCE", "0"))
    # The fraction of the balance one stop-out may cost. 1-2% is the standard
    # rule; the desk defaults to 1 because the owner's account is small and the
    # minimum gold lot is already large relative to it.
    risk_pct: float = float(os.environ.get("DESK_RISK_PCT", "1"))
    # Ounces one lot controls. 100 is the standard gold contract and the figure
    # every broker specification agrees on; overridable for a non-standard feed.
    contract_size: float = float(os.environ.get("DESK_CONTRACT_SIZE", "100"))
    # The smallest volume the broker accepts, and the step between sizes. Sizes
    # are floored to this -- rounding up would risk more than the budget.
    lot_step: float = float(os.environ.get("DESK_LOT_STEP", "0.01"))
    # A typical gold spread in price points, for stating the cost of a trade.
    # An estimate, labelled as one; never an input to the size.
    spread: float = float(os.environ.get("DESK_SPREAD", "0.25"))
    # The owner's stop tolerance, in price points (0 = no bound). A stop outside
    # this window is refused whatever the lot size: a stop the trader will not
    # honour is not a stop, and a stop inside the noise is not one either. On a
    # small account the window also keeps the risk honest -- 15 points at the
    # minimum lot is 7.5% of $200, so a wider stop is refused for that reason too.
    min_stop_points: float = float(os.environ.get("DESK_MIN_STOP_POINTS", "0"))
    max_stop_points: float = float(os.environ.get("DESK_MAX_STOP_POINTS", "0"))

    # ---- the study boundary ------------------------------------------------
    # The owner's standing rule: the live reading, the plan and the scenarios are
    # built from live desk output only. The ten-year study is a VALIDATION tool --
    # it grades the desk (`desk acceptance`, `walkforward`, `calibration`, `bias`,
    # `backtest`, `gold`) -- and must not be cited as information on a live card.
    # So the live cards show the study's *verdict* where a decision needs one, and
    # none of its statistics: no reach rates, no n, no expectancy in R, no control
    # baseline, no "on 146 similar cases". Set DESK_SHOW_STUDY=1 to put the
    # statistics back on the cards; nothing is deleted, only moved.
    show_study: bool = os.environ.get("DESK_SHOW_STUDY") == "1"

    @property
    def shared_db(self) -> Path:
        return self.home / "shared.db"

    @property
    def signal_ledger(self) -> Path:
        return self.home / "signal_ledger.json"

    @property
    def agents_dir(self) -> Path:
        return self.home / "agents"

    def agent_dir(self, name: str) -> Path:
        return self.agents_dir / name

    def ensure_dirs(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.agents_dir.mkdir(parents=True, exist_ok=True)
