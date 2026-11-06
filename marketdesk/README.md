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
              imminent-event list). Then the other 8 specialists wake
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
| `python3 -m desk.cli daemon` | **always-on**: chat + levels + readings in one process |
| `python3 -m desk.cli daemon --web` | the same, but Telegram pushes (inbound `/health`) |
| `python3 -m desk.cli agents` | the roster, with weights |
| `python3 -m desk.cli memory` | what is in the shared store |

## Quality gates

Three checks, run by `marketdesk-ci.yml` on every change and reproducible by
hand from `marketdesk/`:

```bash
python3 -m pytest tests -q     # 74 tests, keyless and offline
python3 -m mypy desk           # types
python3 -m ruff check .        # lint
```

`mypy` is not decoration: it caught a real bug the tests missed (a dict used
where a string was expected). Coverage is ~84%; `desk/sources/biquote.py` is the
lowest because it is the one module that needs the network, and the tests are
deliberately offline.

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
same press. Telegram confirms an update only when a *later* `getUpdates` call
passes a higher offset — reading it is not enough — so the pass ends by sending
the offset back. That makes each pass idempotent with no state of ours to lose.
A press the pass did not reach is left unconfirmed, so Telegram redelivers it
instead of dropping it.

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

### Expectancy by regime — the regime agent's claim, made falsifiable

The same report closes with a table that groups every graded plan by the market
regime recorded on the run that produced it, and prints the R average per bucket.
This is the test of the regime agent's claim, not a restatement of it: if the
middle volatility band really is where a plan's edge dies, the table shows a
negative R there and the agent earns its keep; if it does not, the agent's weight
stays 0.0. Runs from before the agent existed carry no regime and are excluded,
so the table comes back empty rather than inventing a bucket.

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

## Ten rules the desk holds itself to

These are not style preferences; each one was a real defect found by measurement.

1. **Indicators read closed bars; the price is live.** The newest bar on every
   timeframe is still forming, so its high/low/close move with the tick and an
   indicator read off it is not a fact. On live data the difference was 7 RSI
   points on the daily (38.0 with the partial bar, 30.8 without) — enough to flip
   a reading. The live price comes from the broker quote, never from a partial
   bar. And the price an agent *compares* against a level is the live one too: a
   close is a fact about a closed bar, not about now. Reading the daily close as
   "the price" put a value up to a day old against a fresh EMA stack — 127 points
   off on live data — and held the desk bearish for 56 straight runs.
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
6. **A locked database is retried, not reported.** All agents write at once;
   `busy_timeout` plus a bounded retry keeps a scheduling fact from surfacing as
   an agent failure.
7. **Measure a relationship before trading on it.** `tick_volume` is a stop input
   only because it was measured: on this desk's 1h history a bar above 1.2× its
   20-bar average is followed by a 1.55× wider next-bar range (t=3.96, bootstrap
   95% CI [1.25, 1.91]), and the effect is nearly independent of ATR (partial
   r=+0.39 vs r=+0.13). The same test found *no* directional edge (n=8), so the
   volume is used to widen the stop and never to pick a side — and only ever to
   widen, because a quiet bar is the absence of the signal, not a reason to put
   the stop inside noise.
8. **A live call needs live data.** A directional verdict computed from a feed
   that stopped hours ago is a stale number wearing a signal's clothes: the desk
   would wake someone to trade a price the market already left. `gather()` keeps
   the run alive on whatever it already held — right for reading, wrong for
   alerting — so the alert gate checks the age of the newest reading
   (`DESK_MAX_DATA_AGE_S`, default 3h) and stays silent if it is old. A merely
   quiet market is not stale: age is measured from the fetch time, not the bar.
9. **A plan, once issued, is followed — not re-issued.** Repeating a direction
   every fifteen minutes is not a signal, it is noise; a holder of the call needs
   to hear what *changed*. So the desk remembers the one plan in play
   (`desk/active.py`) and reports only its transitions: `waiting` (trigger not
   fired), `on_track` (filled, heading for the target), `target`, `stopped`,
   `invalidated`. An unchanged `on_track` is re-sent only on a heartbeat
   (`DESK_PLAN_HEARTBEAT_S`, default 2h) — "still running", not the direction
   again. A plan that never fires expires (`DESK_PLAN_EXPIRY_S`, default 24h),
   and a plan the desk's own fresh read now contradicts is dropped and said so.
   A finished plan is cleared, never followed forever.
10. **A level is not a position — a small account needs the lot size.** A plan
   that says "short 4,120, stop 4,162" says nothing about how many lots, and on a
   $200 account that gap is the whole risk: the smallest gold lot (0.01) risks
   $42, or 21% of the account. `desk/account.py` turns every plan and scenario
   into money — `lots = risk_budget / (stop_distance × 100 oz)` — and **floors**
   the size to the broker's lot step, because rounding 0.006 lots *up* to 0.01
   silently risks more than the budget. When the minimum lot over-risks anyway,
   the desk refuses the setup and prints the number that proves it rather than a
   size that looks fine and is not. The owner's stop tolerance is a *window*
   (`DESK_MIN_STOP_POINTS`/`DESK_MAX_STOP_POINTS`, here 10-15 points): a stop
   outside it is refused whatever the money says, and the scenario builder
   prefers the frame whose ATR lands inside the window over a nearer level it
   would only refuse. `desk account` shows it on demand; with no balance the desk
   prints levels only, as before.

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

### Level alerts — the moment a plan is decided

The desk alerts on its own verdict. `desk levels` alerts on the levels a plan is
built around, which is a different question: the orchestrator can say "no trade"
for weeks while the price sits between a trigger and a stop, and the trader still
wants to know the moment one side is decided.

The watched levels are the desk's own, read from what a run already stored:

* the **plan** — its entry, its stop, its first target;
* the **scenarios** — the breakout and breakdown triggers, which is what happens
  if the plan never fills.

A desk that says "no trade" still prints scenarios, so a plan-only watcher goes
silent exactly when the market is deciding something. Set
`DESK_WATCH_SCENARIOS=0` to watch the plan alone. The hand-typed list in
`levels.py` is only the fallback: a number typed days ago watches a trade nobody
is in.

A level fires once — the ledger keys on level and side — so a check every few
seconds does not repeat itself, and crossing back and forth is one signal, not
two. A proximity warning has its own key, so a level can warn and then fire.

## Running it always-on

On a scheduler the desk had to be two loops that never spoke: `serve` read the
market on an interval but never the chat, and `bot --listen` read the chat for a
few minutes but never the market. That is the only shape a runner allows — it is
a fresh VM each time, so nothing can stay resident.

`daemon` is the other shape, for a real host. One resident process with three
cadences that do not compete:

| cadence | default | why |
|---|---|---|
| the chat | long poll, continuous | a press is answered in about a second, whenever it happens |
| the watched levels | every 10s | a level touched and rejected *between* two readings is caught |
| a full reading | every 15m | the recommendation and the scoreboard stay current |

**"Instant" is not on offer, and is not claimed.** The price feed is polled HTTP,
not a stream, so the finest honest cadence is the level interval. And the market
closes: from Friday's New York close to Sunday's open there is nothing to watch,
so the market jobs stand down and the process only serves the chat.

A daemon that dies is worse than one that misses a tick, so every job is caught:
a level check that fails does not stop the reading, and a rejected token backs off
instead of killing the process.

```bash
python3 -m desk.cli daemon                      # forever
python3 -m desk.cli daemon --seconds 60         # a smoke run
python3 -m desk.cli daemon --level-every 5 --read-every 900
```

### As a container

```bash
docker build -t marketdesk .
docker run -d --name marketdesk --restart unless-stopped \
  -p 8080:8080 \
  -e TELEGRAM_BOT_TOKEN=... -e TELEGRAM_CHAT_ID=... -e GROQ_API_KEY=... \
  -e DESK_WEBHOOK_BASE=https://your-host.example \
  -e DESK_WEBHOOK_SECRET=$(openssl rand -hex 16) \
  -v marketdesk-data:/data -e DESK_HOME=/data marketdesk
```

**Mount a volume at `DESK_HOME`.** The alert ledger and the SQLite store live
there; without one, every restart forgets which signals were already sent and
re-sends them, and the scoreboard loses its own record.

### Two ways to reach the chat

Telegram refuses `getUpdates` while a webhook is set, so this is a mode, not an
addition — the daemon runs one or the other, never both.

| | poll (default) | webhook (`--web`) |
|---|---|---|
| how | the desk asks Telegram | Telegram pushes to the desk |
| needs an address | no | yes (`DESK_WEBHOOK_BASE`) |
| latency | one poll cycle | the moment it happens |
| keeps a free host awake | **no** — all outbound | **yes** — `/health` is inbound |

The last row is the one that matters for hosting. A free web host sleeps after
~15 minutes without **inbound** traffic; a long poll is entirely outbound, so it
would never wake the host — and the host sleeping would freeze the very loop
doing the polling. An endpoint fixes both: Telegram's push is inbound, and so is
a plain uptime ping to `/health`.

```bash
python3 -m desk.cli daemon --web            # serve the endpoint, push mode
curl localhost:8080/health                  # the ping that keeps a free host up
```

A forged POST is rejected before it reaches an action: Telegram echoes a secret
back in a header on every push, and the endpoint checks it in constant time.
`/health` needs no secret — it is the liveness probe, and it must be cheap.

`render.yaml` declares ONE service: a **free web service** running `--web`, kept
awake by a health ping. It deliberately does **not** declare the 15-minute cron
it used to: Render gives a workspace 750 instance-hours a month shared across all
free services, and a single always-on service already spends 720-744 h — a second
service running even 20 h would push the workspace over the cap and Render would
suspend *every* free service in it. The cron was a "belt-and-braces" second
opinion that would have taken the desk down. One service it is.

The free instance has no disk, so `DESK_HOME` is ephemeral — expect a rare
duplicate alert, or add a disk on a paid plan ($0.25/GB/month).

### Deploying to Render (free, no card)

1. **render.com → New → Blueprint**, pick the repo. Render reads `render.yaml`
   and builds `marketdesk-daemon` on its own.
2. **Set the secrets in the dashboard** (never in git):
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GROQ_API_KEY`, and
   `DESK_WEBHOOK_SECRET` (any random string, e.g. `openssl rand -hex 16`).
3. **Keep it awake.** `.github/workflows/marketdesk-keepawake.yml` pings
   `/health` every 5 minutes from GitHub Actions — free on a public repo, no
   extra account. If Render appended a suffix to the service name, set a repo
   variable `DESK_HEALTH_URL` instead of editing the workflow. UptimeRobot also
   works if you prefer it; it is not required.
4. **Verify:** `curl https://<service>.onrender.com/health` → `{"ok": true,
   "service": "marketdesk"}`. The daemon registers its own webhook with Telegram
   on boot (Render injects `RENDER_EXTERNAL_URL`), so there is no URL to paste.

A free web service still sleeps after 15 minutes with no inbound traffic and
takes ~1 minute to wake; the keepawake ping is what prevents that. Telegram's
push would wake it anyway, but the first press after a sleep would wait a minute.


## The roster (10 agents)

| agent | craft | weight | what it decides |
|---|---|---|---|
| **structure** | price action / SMC | 2.0 | market structure, premium vs discount, break of structure → the allowed side |
| **trend** | multi-timeframe | 2.0 | do the frames agree? (halving weight per step down) |
| **momentum** | RSI + MACD | 1.2 | when a move is stretched (overbought ≠ sell) |
| **levels** | S/R + Fibonacci | 1.5 | nearest real levels, every one tagged by provenance |
| **volatility** | ATR + range | 0.8 | stop distance and the volatility regime |
| **regime** | ADX + ATR percentile | — (no vote) | is there a trend, and is the tape tradeable? |
| **news** | headlines + calendar | 1.5 | news tone and imminent event risk |
| **surprise** | calendar actual vs forecast | 1.2 | what the printed number beat or missed, and by how much |
| **correlation** | cross-market | 0.7 | is gold moving on its own or on the dollar? (DXY leads — rho ≈ −0.39) |
| **risk** | the gate | — | may the desk trade at all? (fail-closed) |

The **regime** agent is the only one that casts no directional vote. Every other
specialist answers *which way*; it answers *whether there is anything to trade*.
On the desk's own ten-year daily file, three standard methods each beat "always
long" on the same bars in the lowest and highest volatility thirds and each lost
to it in the middle third (EMA20/50 −0.176R, t=−2.88; 60-day momentum −0.234R,
t=−3.69), and a 20-day breakout returned +0.318R below ADX 25 but +0.042R
(t=0.35) above it. The agent publishes those two readings and nothing else. Its
weight is 0.0 and it is excluded from the tally — a pattern in one instrument
over one decade is a hypothesis, and the desk's rule is that a weight is earned
by a measured edge, not awarded by one.

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
| `DESK_WATCH_SCENARIOS` | `1` | also watch the scenario triggers, not just the plan |
| `DESK_WEBHOOK_BASE` | — | public base URL; set → Telegram pushes instead of polling |
| `DESK_WEBHOOK_SECRET` | — | shared secret Telegram echoes back on every push |
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
  indicators.py     EMA/RSI/ATR/MACD/pivots/fib/ADX/ATR-percentile — None when too short
  sources/biquote.py candles, quote, news, calendar, summary
  agents/           base + the 10 specialists + the ROSTER
  orchestrator.py   the central manager (gather → dispatch → synthesise)
  daemon.py         the always-on process: chat + levels + readings
  bot.py            the Telegram chat client (long poll, or webhook)
  webhook.py        the inbound endpoint: /health + the Telegram push route
  report.py         the card (Arabic by default)
  cli.py            run / gather / memory / agents / daemon
```

## Backtest

`desk backtest` walks the stored daily bars, lets the real agents decide at each
bar, and grades the plan the way the live scoreboard does. It runs offline from
the CSV the repo ships and needs no key.

```
cd marketdesk && python3 -m desk.cli backtest
```

It reports expectancy in R, not just a hit rate, because 60% at 1.2R loses money.
Read the confidence interval before the mean: a positive expectancy whose CI
spans zero is not an edge. The current daily-bar sample shows **no proven edge**,
and the command says so rather than rounding up.

## Signal scan

`desk scan` asks the question the backtest cannot: is there any edge in this data
at all? It tests five standard signal families on every daily bar with no
selection and no tuning, prints the constant-direction controls beside each, and
splits the sample in two so a signal that only lives in one half is visible.

```
cd marketdesk && python3 -m desk.cli scan
```

Two rules make it trustworthy. Five families is five chances to get lucky, so
significance is Bonferroni-corrected. And every family is split in and out of
sample. A family that is significant *and* holds out of sample is worth building
on; anything else is a regime or a fluke. The scan is evidence for what to build
next, not a strategy.

## Two-sided plan

`desk plan` answers the question the orchestrator does not: where to act and
where to get out. It prints a conditional long and a conditional short, each with
a stop at 1.5xATR and a ladder of three targets at 1R / 2R / 3R, built from the
one signal the scan found evidence for (a break of the prior 20-day range).

```
cd marketdesk && python3 -m desk.cli plan          # live quote + daily candles
cd marketdesk && python3 -m desk.cli plan --offline # read the shipped CSV
```

Each side carries the sample evidence that supports it, and the two sides are
labelled as they are found: in this data the long side is supported (n=47,
+0.915R, t=+4.80) and the short side is noise (n=16, t=-0.10). The plan also
states that the trigger adds only +0.015R over always-long, so the long side's
profit is the market's drift rather than a proven edge in the trigger.

## Long history and the cross-market test

The desk's own history is ~500 daily bars (about 20 months), and that ceiling is
the single biggest limit on what can be claimed. `desk longdata` pulls 10+ years
of daily bars for seven markets from Yahoo's keyless chart API into the desk's CSV
shape, and `desk crossmarket` runs the same trend rule, unchanged, on every one.

```
cd marketdesk && python3 -m desk.cli longdata --out ../longdata --range 10y
cd marketdesk && python3 -m desk.cli crossmarket --dir ../longdata
```

The result is negative, and it is the most useful thing the desk has produced:

| market | bars | trend | best constant | trend - best | t |
|---|---|---|---|---|---|
| XAUUSD | 2514 | +0.299 | +0.255 (alwaysL) | +0.045 | +1.55 |
| XAGUSD | 2513 | +0.023 | +0.194 (alwaysL) | -0.171 | -4.89 |
| EURUSD | 2601 | -0.066 | +0.069 (alwaysS) | -0.135 | -4.29 |
| USDJPY | 2601 | +0.153 | +0.191 (alwaysL) | -0.038 | -1.40 |
| USOIL | 2514 | +0.064 | +0.186 (alwaysL) | -0.122 | -3.92 |
| SPX | 2512 | +0.105 | +0.244 (alwaysL) | -0.139 | -5.83 |
| DXY | 2514 | -0.018 | +0.087 (alwaysL) | -0.105 | -3.17 |

**0 of 7 markets.** The signal does not beat a constant chosen over the whole
sample on any of them. The baseline is one constant, not `max(long, short)` per
bar -- the latter is an oracle no trader can hold, and using it would make the
comparison meaningless.

The window-sensitivity table answers why the desk's 500-bar result looked good:
gold over its last 500 bars shows +0.159R (t=+2.97), and over the full 2514 bars
the same rule shows +0.045R (t=+1.55). **The result was the sample size, not the
rule.** A rule that is significant in one window and noise in the full sample was
never a rule.

This is why the desk's recommendation is honest about being a reading, not a
proven edge.

## The 10-year gold study

`desk gold` is the gold-only answer, on ten years of daily bars instead of the
desk's ~500. It describes the current state -- trend versus the 50-day mean, the
RSI bucket, where price sits in its 20-day range -- then finds every historical
day in the same state and reports what the next 5 and 10 days actually did.

```
cd marketdesk && python3 -m desk.cli gold --csv ../longdata/XAUUSD_1d.csv
```

Every bucket is printed with its `n` and beside the base rate, because a 59%
up-rate means nothing without knowing the unconditional rate. The last section
grades the trade under consideration -- entry, stop and target in ATR units --
first-touch and stop-first, on every historical day in the same state, so the
answer is an **expectancy in R** rather than a feeling.

For the state of 2026-11-03 (below the 50-day mean, RSI 36, bottom of the 20-day
range), 389 of 2452 days matched:

| side | horizon | n | hit% | expectancy R | base R |
|---|---|---|---|---|---|
| SHORT | 10d | 389 | 28.0% | **-0.194** | -0.108 |
| LONG | 10d | 389 | 36.5% | **+0.049** | +0.062 |

**Neither side has an edge in this state.** The short side is *worse* than the
unconditional base rate, and the long side is slightly worse too. That is the
honest reading: this state carries no information about the next ten days.

The forming bar is excluded here too -- the last bar of a live chart is the day
still moving, and `longdata` marks it `isOpen` rather than calling it closed.
