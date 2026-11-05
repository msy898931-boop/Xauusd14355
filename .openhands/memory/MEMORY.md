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

## MarketDesk — lessons worth keeping in the index
Detail for all of these lives in `MARKETDESK.md`; the daily log is `2026-11-02.md`.

- ⭐ **A close is a fact about a closed bar, not about now.** Indicators read closed
  bars; the price *compared against them* must be `live_price()`. Taking `closes[-1]`
  (daily, up to a day old: 3938 vs live 4065) made `trend`/`structure` vote bear
  56/56 while gold rose 3% — one bet placed 13×, not 13 losses.
- ⭐ **When every outcome has the same sign, it is a bug, not a view.**
- ⭐ **A live call needs live data.** Gate the ALERT on data age (`data_fresh()`,
  `DESK_MAX_DATA_AGE_S` 3h, measured from `fetched_at` so a quiet market is not stale).
- ⭐ **"Configured" is not "working"**; **a dead webhook is worse than none**; **"the
  token works" ≠ "the owner sees the message"** (print the chat via `getChat`).
- ⭐ **A currency tag is not a transmission channel.** Score a curated allowlist and
  state what was dropped; the sign is per-release — never one sign for all.
- **`actual=0.0` is a reading; a missing forecast is not a zero** → return `None`.
- **Audit for data stored but never read before adding anything.**
- ⭐ **"Alert once" is not "follow it".** `desk/active.py` holds the ONE plan in play
  and reports only transitions (waiting→on_track→target/stopped/invalidated) + a
  heartbeat; expires an unfired plan; drops one the fresh read contradicts. The
  ledger fingerprint `symbol:verdict:score` let a COMPLETED plan suppress the next
  same-direction trade forever → now `…:entry`. **The key was lying, not the code.**
  Fresh-VM runner: `active_plan.json` rides the cached `marketdesk/data`.
- ⭐ **Owner condition: keep the 10-year study OUT of my market analysis.** Base
  reading/recommendation/scenarios ONLY on live desk output. Never cite historical
  analogs, expectancy-R, n=47, or any 10y stat; never run `desk gold`.
- **Open risks (accepted):** one data source (biquote); Actions cache is best-effort
  (a duplicate alert is possible after eviction); scoreboard mixes pre/post-fix runs.
- **`origin` had a stale embedded token** that hung `git push` on a password prompt;
  fix = `git remote set-url origin https://${GITHUB_TOKEN}@github.com/...` +
  `GIT_TERMINAL_PROMPT=0`.
