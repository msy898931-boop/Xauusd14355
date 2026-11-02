# MarketDesk — محلل سوق وأخبار متعدد الوكلاء

An AI multi-agent market & news analyst. One central manager (the orchestrator)
gathers the market data, wakes a roster of specialist agents, collects their
studies from a shared memory, and issues the final recommendation.

Pure Python 3.13 stdlib — no pip install to run. Data from **biquote.io**
(keyless), narration from Groq (optional; the desk runs fully without it).

## Quick start

```bash
cd marketdesk
python3 -m desk.cli run --lang ar      # full analysis + recommendation
python3 -m desk.cli run --json         # same, plus the machine-readable payload
python3 -m desk.cli gather             # only pull data into the shared memory
python3 -m desk.cli memory             # what the shared memory holds
python3 -m desk.cli agents             # the roster
```

A full run takes **~7 s** with the model on, **~3 s** without. The wall-clock
budget (`DESK_BUDGET_S`, default 60 s) is enforced: agents run concurrently and
an overrunning one is reported as timed-out, never as neutral.

## How a run works

```
 gather    →  orchestrator pulls every candle biquote serves + the live quote
              into the SHARED memory (SQLite).
 dispatch  →  the NEWS agent runs FIRST, alone: it pulls headlines + the
              economic calendar and publishes its study (including the
              imminent-event list). Then the other 7 specialists wake
              concurrently, each reads the shared memory, computes its study,
              and publishes it back. The order is not cosmetic — the risk gate
              reads the news agent's event risk, and a gate that reads a
              not-yet-written row waves through the event it exists to block.
 synthesise→  the orchestrator weights the signed scores, respects the risk
              gate, and writes the final recommendation with its levels.
```

## Commands

| command | what it does |
|---|---|
| `python3 -m desk.cli run [--lang ar] [--json]` | one full analysis |
| `python3 -m desk.cli hit-rate` | grade past calls against what price did |
| `python3 -m desk.cli watch` | one scheduled pass; alerts only if actionable |
| `python3 -m desk.cli bot` | one poll pass: answer the chat's button presses |
| `python3 -m desk.cli menu` | publish the button menu to the chat |
| `python3 -m desk.cli serve --interval 15` | the watch loop, for a host with no cron |
| `python3 -m desk.cli agents` | the roster, with weights |
| `python3 -m desk.cli memory` | what is in the shared store |

## The Telegram bot — the desk on demand

A button menu in the chat. Press one and the desk answers on the next poll.

```bash
python3 -m desk.cli menu          # publish the buttons to the chat (once)
python3 -m desk.cli bot           # one poll pass: read the presses, answer them
python3 -m desk.cli bot --greet   # also re-publish the menu, then poll
```

| button | what it does | cost |
|---|---|---|
| 📊 تحليل الآن | a full run, then the recommendation | ~7 s, agents + model |
| 🎯 التوصية الحالية | the last stored reading | instant |
| 📋 آخر تقرير | the full card from the last run | instant |
| ⏱️ حالة السوق | a live quote and its age | one HTTP call |
| 📈 معدل النجاح | the scoreboard | instant |

**Why it polls instead of using a webhook.** A webhook needs a long-lived HTTPS
endpoint and there is no server here — the desk is a CLI. So the chat is polled
on a schedule (`marketdesk-bot.yml`, every 5 minutes, free), which means a reply
lands on the *next* pass rather than instantly. That latency is the price of
running with no server and no card; the design is deliberate.

**The cursor lives at Telegram, not here.** A runner is a fresh VM with no disk,
so a local "last seen" pointer would be lost and every pass would replay the
same press. `getUpdates` carries the cursor forward in the same call, which makes
each pass idempotent with no state of ours to lose. A press the pass did not
reach is left unconfirmed, so Telegram redelivers it instead of dropping it.

You can also just type: `/start` or `تحليل` or `توصية` — an unknown message
shows the menu rather than being ignored. If the bot was quiet for a while the
queue can hold many requests; the newest few are answered and the older ones are
folded into the first reply, which says how many, so the chat stays readable and
nothing is silently swallowed.

Only the configured chat may press a button; anything else is refused.

## The scoreboard — the desk's record

`desk hit-rate` grades every stored run against what price actually did at +1h,
+4h and +1d, and reports a hit-rate for the desk and for each agent. This is what
turns the weights from taste into something that can be measured.

Two rules keep it honest:

* **A horizon is graded only once a candle exists past it.** A run that is too
  recent is skipped, never scored against the current price.
* **A flat bias and a move inside the deadband are excluded**, not counted as
  wins — that would inflate the number.

The desk records its *implied* bias on every run, even a `no-trade` one, so the
caution itself can be graded later.

### The plan is graded too — not just the direction

Grading the direction alone is flattering. A short that reaches its target only
*after* first taking out its stop was a losing trade, and a direction-only score
counts it as a win. So `hit-rate` also walks the bars between the run and the
horizon and reports **which was touched first**:

* `target` — the target came first: a win, worth the plan's R multiple.
* `stop` — the stop came first: a loss, `-1R`, whatever the price did afterwards.
* When a single bar contains both, the **stop** is assumed. The conservative
  reading, and the one that cannot flatter the desk.

The summary line reports the win count and the **expectancy in R**. That is the
number that matters: a 60% win rate at 1.2R loses money, and only the R average
shows it.

## The gate, and why it is not a blunt instrument

The risk gate is fail-closed, but a gate that blocks on *any* high-impact event
is a gate that is always shut. It closes only when an event is (a) inside a short
window (default 6h, `DESK_EVENT_WINDOW_H`) **and** (b) in the traded
instrument's currency. An RBA decision does not block gold; a JOLTS release does.

## Reward:risk is enforced, not suggested

A direction can be right and the setup still untradeable. The planner walks the
level list outward and keeps only targets that pay at least `DESK_MIN_RR`
(default 1.5) against the stop; the nearest level is usually too close to pay for
it. If no real level clears the bar, the desk states no plan rather than a losing
one — and a projection is used only when there is no structure on that side at
all, tagged as the weakest kind.

## Six rules the desk holds itself to

These are not style preferences; each one was a real defect found by measurement.

1. **Indicators read closed bars only.** The newest bar on every timeframe is
   still forming, so its high/low/close move with the tick and an indicator read
   off it is not a fact. On live data the difference was 7 RSI points on the
   daily (38.0 with the partial bar, 30.8 without) — enough to flip a reading.
   The live price comes from the broker quote, never from a partial bar.
2. **One definition of "neutral".** `NEUTRAL_BAND` (default 0.15) is the single
   constant an agent uses to name its stance and the orchestrator uses to name a
   verdict. A hardcoded band in each of them is how a specialist and the final
   call drift apart.
3. **Both news labellers see the same text.** The keyword map and the model are
   given the title *and* the description. Feeding the model only the title would
   make every keyword hit in the description look like a model correction.
4. **The budget is a real ceiling.** `cf.wait` returns when the deadline passes
   whether or not the work is done; an agent that overruns is reported as
   timed-out, never waited out and never counted as neutral. Waiting on
   `as_completed` made the ceiling a claim rather than a limit.
5. **A plan is graded on the stop, not the direction.** A short that reaches its
   target only after first taking out the stop was a loss. Only the first touch
   counts, and an ambiguous bar counts as the stop.
6. **A locked database is retried, not reported.** Eight agents write at once;
   `busy_timeout` plus a bounded retry keeps a scheduling fact from surfacing as
   an agent failure.

## When the model is unavailable

Every number is computed, so the desk runs keyless. The model only phrases what
was computed. When it is missing — no key, or every model's quota is spent — the
prose falls back to a deterministic sentence and **the report says so**; it never
pretends a computed line was written by the model.

## The model chain (why one model is not enough)

Groq meters a **separate daily token quota per model**. So the desk keeps a
*chain* — `openai/gpt-oss-120b` → `openai/gpt-oss-20b` → `qwen/qwen3.8-27b` →
`allam-2-7b` — and when the head model answers `429` it cools that model (for the
provider's own `try again in …` hint) and rotates to the next. One model's spent
quota is therefore a rotation, not a silent drop to computed prose. The report
shows `↻ النموذج: … (دار N مرة)` when a rotation happened, so you always know
which model wrote the text. Override with `DESK_MODELS=a,b,c` or `DESK_MODEL=x`.

A `429` is never retried on the same model: a quota error is not transient, and
retrying it once turned a 7-second run into 22 seconds for nothing.

## Alerting

The desk is silent most of the time; that is the point. `watch` sends a Telegram
message only when a run is *actionable* — a live directional call, cleared by the
gate, above `DESK_ALERT_SCORE` (default 0.25) — and a signal already sent is not
sent again. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to enable it; with
neither set the desk stays silent rather than half-alerting.

## The roster (8 agents)

| agent | craft | weight | what it decides |
|---|---|---|---|
| **structure** | price action / SMC | 2.0 | market structure, premium vs discount, break of structure → the allowed side |
| **trend** | multi-timeframe | 2.0 | do the frames agree? (halving weight per step down) |
| **momentum** | RSI + MACD | 1.2 | when a move is stretched (overbought ≠ sell) |
| **levels** | S/R + Fibonacci | 1.5 | nearest real levels, every one tagged by provenance |
| **volatility** | ATR + range | 0.8 | stop distance and the volatility regime |
| **news** | headlines + calendar | 1.5 | news tone and imminent event risk |
| **correlation** | cross-market | 0.7 | is gold moving on its own or on the dollar? |
| **risk** | the gate | — | may the desk trade at all? (fail-closed) |

## Memory — two tiers

* **Shared memory** (`data/shared.db`, SQLite) is the blackboard every agent
  reads and writes: `candles`, `quotes`, `news`, `calendar`, and `studies`.
  A specialist's scratch reasoning is *not* here — only its published study is a
  fact. The candle tables are the shared data source the orchestrator fills and
  the agents read from.
* **Private memory** (`data/agents/<name>/memory.json`) is one agent's own store:
  its running notes, its last verdict, its run count. Bounded at 50 notes.

## The rules the desk enforces

1. **A number carries its provenance.** A level is `pivot` (printed by the
   market), `fib` (a measured leg) or `projection` (one stop beyond the last real
   level). Nothing is printed without a source.
2. **A metric that cannot be computed is `None`, never `0`.** Zero is a number a
   thin sample did not earn. (`indicators` returns `None`; the card shows `—`.)
3. **The risk gate is fail-closed.** Market closed, stale quote, or a high-impact
   event inside 24 h → no live entry, and the card says why.
4. **The model narrates, it does not decide.** Every number is computed before
   the model is called; the model is told it may not add one.

## Configuration (all env-overridable)

| var | default | meaning |
|---|---|---|
| `DESK_HOME` | `./data` | root for the shared DB and agent memories |
| `DESK_SYMBOL` | `XAUUSD` | the instrument read |
| `DESK_MODEL` | `openai/gpt-oss-120b` | Groq model for narration |
| `DESK_BUDGET_S` | `60` | wall-clock ceiling for one run |
| `GROQ_API_KEY` | — | unset → deterministic summaries, no model |

## Data source

biquote.io — a keyless MT5 broker feed. Candle depth is a **retention ceiling**,
not the `limit`: M1 ~5 h, M5 ~4 d, M15/M30 ~6 d, H1 ~11 d, H4 ~6 w, D1 ~19 mo.

⚠ **Licence:** biquote's terms forbid re-exposing OHLC / bulk data through a
public API. The desk reads the candles locally for its own analysis and does not
serve them onward. Keep it that way.

## Tests

```bash
python3 -m pytest tests -q      # 19 tests, keyless and offline
```

## Layout

```
desk/
  config.py         settings, paths, the symbol/timeframes
  memory.py         SharedMemory (SQLite) + AgentMemory (private JSON)
  llm.py            Groq client (browser UA — Cloudflare needs it)
  indicators.py     EMA/RSI/ATR/MACD/pivots/fib — None when too short
  sources/biquote.py candles, quote, news, calendar, summary
  agents/           base + the 8 specialists + the ROSTER
  orchestrator.py   the central manager (gather → dispatch → synthesise)
  report.py         the card (Arabic by default)
  cli.py            run / gather / memory / agents
```
