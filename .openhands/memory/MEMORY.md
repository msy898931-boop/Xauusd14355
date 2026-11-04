# Project memory — index

Three projects live in this workspace. This file is the durable index; the full
detail for each is in the archive beside it.

| project | where | detail |
|---|---|---|
| **GoldBot** | `src/goldbot/`, bot `@Xauusd1435_bot` | `GOLDBOT.md` |
| **AnalysisDesk** | `msy898931-boop/analysisdesk`, `/workspace/project` | `ANALYSISDESK.md` |
| **MarketDesk** | `msy898931-boop/Xauusd14355`, `/workspace/project/marketdesk` | `MARKETDESK.md` |

Daily logs (`YYYY-MM-DD.md`) hold the working notes; read one on demand.
**The user speaks Levantine Arabic — answer in Arabic.** Code/comments English.
Never echo secrets (`GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).

## ⭐ The one lesson, in every shape
Every bug in these projects is **an unknown reported as a known** — silence
rendered as an all-clear, a missing read as a reassuring default, a made-up
number as a measurement. When something is unmeasured, say so; never substitute.
The corollaries that keep recurring:
- **The metric, not the code, is usually what lies.** Grade the *plan*, not the
  direction; report expectancy in R; check a result against a constant and a
  regime split before believing it.
- **"Configured" is not "working".** Probe (`getMe`, a real send, a write).
- **When two things must agree, generate one from the other** — never keep both
  by hand. When you find one copy of a rule, grep for its siblings.
- **A check that is also an intervention cannot observe** (a probe that wakes the
  thing it measures).
- **Look-ahead produces a beautiful, significant, wrong answer.**
- **Verify on every surface the value reaches**, not only the case that motivated
  the fix. Run mypy — it finds bugs tests miss.

## MarketDesk — the multi-agent market/news desk (current work)
`marketdesk/` — pure stdlib, no deps. `gather()` (biquote candles+quote → shared
SQLite) → `dispatch()` (**news agent FIRST, alone**, then the other 7
concurrently) → `synthesise()` (weighted scores + risk gate + plan). **8 agents:**
structure 2.0 · trend 2.0 · levels 1.5 · news 1.5 · momentum 1.2 · risk (veto) ·
volatility 0.8 · correlation 0.7. Each agent has private JSON memory + publishes a
`Study`. Run ~7 s with Groq, ~3 s keyless. Commands: `run` · `hit-rate` · `watch`
· `serve` · `agents` · `memory`. Add an agent = subclass + one line in `ROSTER`.
**Gates:** `pyproject.toml` + `marketdesk-ci.yml` run pytest/mypy/ruff on every
push/PR. **Never run `ruff format` here** — CI checks `check` only, and format
churns ~1200 lines. Tools are modules: `python3 -m ruff/mypy`.
Repo is **PUBLIC**, no licence, tracks `biquote/*.csv`.
- ⚠ **Groq 403 `1010` without a browser User-Agent** (Cloudflare) — same wall as
  Kraken/gold-api/FF. And a reasoning model's `max_tokens` must cover thinking AND
  the answer (800 → empty content; 1500 completes).
- ⚠ **Groq meters a SEPARATE daily quota PER MODEL** → `llm.py` keeps a chain and
  rotates on 429/402/403: `gpt-oss-120b → gpt-oss-20b → qwen3.8-27b → allam-2-7b`
  (`DESK_MODELS`/`DESK_MODEL` override). Never retry the same model on a 429.
- **biquote** (`biquote.io`, keyless MT5): quote carries `marketState`/`stale`/
  `quoteAgeSeconds`; `volume` is always 0 (use `tickVolume`); daily caps at **500
  bars (~20 mo)**, 4h=181, 1h=169. Its terms **forbid re-exposing OHLC**; the spot
  quote is the allowed case.
- **Telegram** deploy is **GitHub Actions** (free, no card, no second account —
  Render needs the owner's sign-in and Cron Jobs are paid). Bot answers pressed
  buttons via `getUpdates` long-poll; **the offset is confirmed only by a LATER
  call** — end with `confirm(next_offset)` or every pass replays the backlog.
  Secrets are owner-set (I can never set them: `/actions/secrets` → 403); a
  workflow log **cannot** distinguish an unset secret from a set one, so add a
  presence-only step.
- **Deploy facts:** the OpenHands GitHub App must have the repo in its
  `repository_selection` (switch it, don't assume); a PR opens as a **DRAFT** and
  needs GraphQL `markPullRequestReadyForReview` before merge; a commit message
  with backticks must go through `-F file`; **a GitHub `schedule` can take hours
  to register** — no runs yet is not yet evidence of breakage.
- **Session + news (PR #24):** `desk/session.py` states the spot-gold week in UTC
  (Fri 21:00 close → Sun 22:00 open); `Orchestrator.run(now=)` gates it **before any
  network call** (`DESK_RESPECT_SESSION=0` overrides). `fetch_news(symbol=...)` was
  **not** a filter — a `GOLD_TERMS` whitelist now drops what cannot move gold and
  the summary states the count.
Detail → `MARKETDESK.md`, `2026-11-02.md`.

## GoldBot — XAUUSD SMC/ICT bot
`src/goldbot/`, `PYTHONPATH=src python3 -m goldbot.cli <cmd>`. Own Groq key/bot.
**THE HEADLINE RESULT: the strategy is gold BETA, not skill** — it wins when gold
rises and loses when it falls, in every direction and frame. Do not tune
parameters to "fix" it. A control module + `cli study` now make that one command.
**AYZR** (a separate AI-first engine) is built but **not yet running**
(`AYZR_ENABLED` off by default). Live candles `kraken:PAXGUSD`; history
`data-api.binance.vision` (never `api.binance.com` → 451). State under
`GOLDBOT_DATA_DIR`. `goldbot.onrender.com` is **not** this project.
Detail → `GOLDBOT.md`, `2026-10-28.md`.

## AnalysisDesk — FastAPI market-analysis desk
Deployed `analysisdesk.onrender.com` (Render free, spins down after 15 min idle;
an outbound long poll is not inbound traffic, so the in-process clock freezes with
the host). Spot chain `biquote → Swissquote → gold-api`. **No keyless XAU/USD
candle feed exists** — gold-backed tokens are the proxy (Kraken PAXG primary,
Coinbase fallback), **~0.2% premium** which is a *level* bug, not a label bug:
`anchor_series_to_spot()` rescales before indicators, disclosed, never silent.
The in-process rate limit / error log are per-process → `--workers 1`.
Detail → `ANALYSISDESK.md`.

## marketdesk: the always-on daemon (2026-11-02)
⭐ **"Always-on" and "instant" are different promises.** Telegram was two loops
that never spoke (`serve` = market only; `bot --listen` = chat only) because a
GitHub runner is a fresh VM and nothing stays resident — the bot was awake ~33%
of the time. `desk daemon` (`desk/daemon.py`) is ONE process, three cadences:
chat long-polled (~1s reply), levels every 10s, a reading every 15m. The feed is
*polled HTTP*, so the finest honest cadence is the level interval — never promise
tick-instant on a polled feed. The market closes Fri 21:00 → Sun 22:00 UTC, so
"never sleeps" has a hole: market jobs stand down, the chat keeps serving.
A daemon must outlive its failures — catch every job (a rejected token backs off,
it must not kill the process). Cadence clocks start at `-inf`, not 0, or the first
tick runs nothing. A container needs a volume at `DESK_HOME` or it re-sends alerts
it already sent. Free Render has no worker/disk (~$7/mo for always-on).
Detail → `2026-11-02.md`.

## marketdesk: the inbound endpoint (2026-11-02, later)
⭐ **A "web page" is not what makes hosting free — INBOUND traffic is.** A static
page is free everywhere but serves files; the backend behind it has the same
problem. What keeps a free host awake is a request ARRIVING: Render free sleeps
after ~15 min without inbound traffic, and a **long poll is entirely OUTBOUND**,
so polling could never wake a sleeping host — and the host sleeping would freeze
the loop doing the polling. `desk daemon --web` serves `/health` (the ping that
keeps it up) and a Telegram push route. The two Telegram transports are mutually
exclusive (`getUpdates` is refused while a webhook is set), so `--web` is a MODE,
not an addition. The per-update reply is ONE method (`handle_update`) shared by
both, or the webhook becomes a silently diverging client. A forged POST is
rejected in constant time BEFORE an action; a bad update answers 200, not 5xx, or
Telegram redelivers it forever. And watch "configured ≠ working": I put
`DESK_LEVEL_EVERY` in render.yaml before the CLI read it. Detail → `2026-11-02.md`.

## marketdesk: watch what the desk said, not a typed list (2026-11-02, latest)
`desk levels` watches the desk's own last plan (entry/stop/target) — and now ALSO
its scenario triggers (breakout/breakdown), because **a desk that says "no trade"
still prints scenarios**: a plan-only watcher goes silent exactly when the market
is deciding something. The watched set is their UNION, deduped by price (two
sources naming one shelf is noise, not emphasis). `DESK_WATCH_SCENARIOS=0`
restores plan-only. ⭐ **Measure before calling it too expensive** — building the
scenarios looked heavy for a 10s loop, measured at **5 ms** (read-only over
shared memory, no agent/model); the cost was never the reason to skip it.
⭐ **A token embedded in a git remote expires, and `git push` then HANGS on a
password prompt** rather than failing: re-point with
`git remote set-url origin https://x-access-token:${GITHUB_TOKEN}@github.com/...`.
Detail → `2026-11-02.md`.

## Render free tier: the 750 hours are per WORKSPACE (2026-11-02)
⭐ **750 free instance-hours are shared across ALL free services in a workspace,
not 750 each.** One always-on service already spends 720-744 h (30-31 × 24); a
second free service running even 20 h → 764 h → **Render suspends EVERY free
service until next month**. So a "belt-and-braces" backup service is the failure
it pretends to prevent. **Render Cron Jobs have no free tier** (~$1/mo min);
only web services + static sites are free. **A blueprint that parses is not a
blueprint that works** — validate the resource budget, not the syntax. A failure
that is a *suspension* rather than an *exception* needs a test or it never shows
up. **A GitHub Actions cron ping to `/health` replaces UptimeRobot for free** on
a public repo (5-min cadence vs a 15-min sleep window = three chances).
Bandwidth: Hobby dropped to **5 GB/mo** (Apr 2026); marketdesk needs ~0.6 GB.
**marketdesk is NOT deployed to Render** — `render.yaml` existing ≠ deployed.
Detail → `2026-11-02.md`.

## GitHub Actions cron is best-effort — and a dropped tick looks healthy (2026-11-03)
⭐ On this repo a `*/5` cron produced **2 scheduled runs in ~17 h** while every
`workflow_dispatch` succeeded. GitHub docs: `schedule` is delayed under load and
"some queued jobs may be dropped"; **`:00` is the most congested slot and moving
off it is the only knob**. Offset every cron (`2-59/5`, `7-59/15`). This does
**not** make cron reliable — for a hard cadence, an external scheduler must call
`workflow_dispatch`. ⭐ **A dropped tick is an absence, not an error: nothing goes
red.** Compare `created_at` against expected slots via the Actions API.
⭐ **A dependency change must be verified in a CLEAN venv** — my pyyaml guard
tests passed locally (pyyaml present) and broke CI (installed `.[dev]`, no
pyyaml). ⭐ **Render response header `x-render-routing` names the real state:**
`suspend-by-user` = suspended by owner (503), `no-server` = no such service (404).
The live service is named **`marketdesk`**, not the blueprint's `marketdesk-daemon`.
Detail → `2026-11-02.md`.

**2026-11-04 — "التلغرام ما عم يشتغل" was a SLEEPING HOST, not a token.** Three
layers, each of which alone looked like "the bot is broken":
1. ⭐ **A dead webhook starves the bot.** Telegram serves updates to a webhook OR
   `getUpdates`, never both. A registration left pointing at a sleeping host made
   every poll die with a bare **HTTP 409** while `pending_updates` climbed (9) and
   `last_error` said `502 Bad Gateway`. `desk.cli telegram` now takes
   `--clear-webhook` (→ `TelegramBot.delete_webhook()`), exposed in
   `marketdesk-bot.yml` as `clear_webhook`. **A dead webhook is worse than none.**
2. ⭐ **The real cause: Render's free tier sleeps after ~15 min of no INBOUND
   traffic.** The only inbound traffic is a Telegram push, which a quiet chat never
   sends — so a quiet hour = sleep, and the next press hits a cold instance (or
   Telegram's delivery 502s and retries). The daemon now **pings its own public
   `/health`** (`Daemon._self_ping`, `DESK_PING_EVERY`, default 600s) — before the
   market-session check (a closed market is when nothing else is inbound) and
   included in `_next_due_in()` so the loop cannot sleep past its own keep-alive.
   Failure is recorded, never fatal; `DESK_PING_EVERY=0` disables it.
3. ⭐ **Never build a keep-alive on GitHub cron.** `marketdesk-keepawake.yml`
   (a `3-59/5` cron) fired **twice in ~15 hours** — a 5-minute cron cannot hold a
   15-minute window.
- ⭐ **"The token works" ≠ "the owner sees the message".** `check-alerts` said
  `getMe: ok` and `test send: delivered` while the chat looked dead. `desk.cli
  telegram` now prints the configured chat via `getChat` (here: `private`,
  `Almalek121`) so a chat_id pointing at the wrong chat cannot hide.
- ⭐ **Secrets never reach a local shell here** — `getMe` returned 404 locally
  because the harness injects secrets only into the command that STARTS a process.
  Test the alert path from the cloud workflow, never locally.
Detail → `2026-11-02.md`.
- ⭐ **A currency tag is not a transmission channel.** A USD-tagged economic
  release is not automatically a *dollar* event: `EIA Crude Oil Stocks Change`
  is an oil-supply figure and must not vote on gold. The surprise agent scores a
  curated allowlist and reports what it dropped — the same "whitelist, not
  blacklist" rule the news agent uses for headlines. And the sign is per-release:
  a higher Unemployment Rate / Jobless Claims WEAKENS the dollar (gold-positive),
  the reverse of a payroll or PMI beat. **Never assume one sign for all releases.**
- ⭐ **`actual=0.0` is a reading; a missing forecast is not a zero.** A calendar
  row with no forecast has no surprise to measure → return `None`, not `0.0`
  (0.0 claims "met expectations" when there was no expectation). No fresh print =
  no vote, not a neutral zero averaged in.
- **Audit for data stored but never read before adding anything.** `calendar.actual`
  was fetched, stored, and read by no one; `tick_volume` is in every bar and every
  `indicators.py` signature and used by no agent. The cheapest feature is often
  already in the DB. (Surprise agent added 2026-11-02 → `2026-11-02.md`.)
- ⭐ **Test the naive use of unused data before building on it — then find its
  real home.** `tick_volume` as a *directional* signal failed (breakout on high
  volume, n=8, 38%). As a *volatility* input it is real: 1h bar >1.2x its 20-bar
  avg → 1.55x wider next range (Welch t=3.96, CI [1.25,1.91]), and **additive to
  ATR** (partial r=+0.385; ATR alone only r=+0.13). It now widens the stop, never
  picks a side — and only ever widens, since a quiet bar is the absence of a
  signal, not a reason to tighten into noise.
- ⭐ **A number and its verb are one fact.** A live line read "وسّعنا الوقف 0.90×"
  (widened 0.90x) when 0.9x *tightens*. When code prints a magnitude with a
  direction word, test the direction, not just the value.
- **Test fixture trap:** marketdesk's `_wave()` repeats hour-of-day every 24 bars,
  so `load_candles` collapses it to 24 hourly bars — editing the "last" raw bar
  then does NOT change the last 1h bar. Space timestamps hourly when a test must
  control the newest closed bar.
