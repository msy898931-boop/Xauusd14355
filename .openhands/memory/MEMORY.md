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
