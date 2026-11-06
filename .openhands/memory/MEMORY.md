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
  token works" ≠ "the owner sees the message"** (print the chat via `getChat`). The
  session-local `TELEGRAM_BOT_TOKEN` here is 35 chars/no colon → `getMe` 404 while
  the repo secret is a valid 46-char token; only `getMe` proves a token.
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
- ⭐ **A level is not a position.** The desk printed entry/stop/target in points and
  never a lot size or dollar risk — fatal on a small account. XAUUSD 1 lot = 100 oz
  ($1 move = $100), min lot 0.01 → a 42-pt gold stop risks **$42 = 21% of $200**;
  at 1% risk the widest affordable stop is **2 points**. `desk/account.py` sizes
  every plan/scenario (`lots = risk/(stop_dist×100)`, **floored** — never rounded
  up), refuses an unsizeable setup **with the number that proves it**, and takes a
  hard `DESK_MAX_STOP_POINTS` cap. `desk account` shows it; `DESK_BALANCE=0` keeps
  points-only. **A stop is not a risk until it meets a lot size and a balance.**
- ⭐ **A tolerance the desk cannot satisfy is a tolerance it refuses forever.** The
  owner's stop window (10-15 pts) is *exactly* the 15m/30m ATR band (measured:
  5m 5.7 · 15m 10.5 · 30m 14.7 · 1h 20.2 · 4h 36.9 · 1d 72.3) — the stop IS the
  frame ATR, so the desk had to reach down to those frames AND prefer the level
  whose frame fits the window over a nearer one it would only refuse (falling back
  to nearest when nothing fits). Adding a limit is half the job; the search must be
  able to produce something inside it.


## MarketDesk — later lessons (2026-11-05)

- ⭐ **A metric too bad to be true must be checked, not "fixed".** The board read
  **0/16 plans, -1.00R**; it looked like a grader bug, but tracing the raw plans
  showed the desk was short while gold rallied ~5% and was stopped out 16 times.
  The "fix" would have relabelled every loss `unfilled` — the same sin as grading
  direction-only. Verify against the data before touching the metric.
- **Two entry semantics, deliberately:** `scenarios.py` `entry` = a **breakout
  trigger**; `recommendation.entry` = **market entry at the current price**
  (`mid@issue == entry`). Fill logic must handle both or it hides losses.
- ⭐ **A once-a-day message belongs on a SCHEDULE, not a resident process.**
  The Render free instance for this desk got **suspended** (750 instance-hour
  cap; `/health` → 503 "suspended by its owner"), taking the daemon down with it.
  The GitHub cron kept running. So the daily map moved to `desk.cli map
  --once-a-day` in `marketdesk-watch.yml` (date stamped in the cached DESK_HOME),
  verified `map sent: True` then `map already sent today`. Do NOT add a second
  free Render service to "back up" the first — that is what trips the cap.
- ⭐ **The git remote's embedded token goes stale.** A push failed with
  `could not read Password for 'https://ghu_...'`; fix is
  `git remote set-url origin https://${GITHUB_TOKEN}@github.com/<o>/<r>.git`.
- ⭐ **A map is a view, not new math — and "manipulation" is not measurable.**
  `desk/map.py` clusters `indicators.pivots` into ZONES (0.25% ≈ 1×15m ATR, min 2
  touches), splits them by the live price into sell (above) / buy (below) zones,
  and adds liquidity = repeated highs/lows (where stops sit). Supply/demand and
  S/R are the SAME prices; do not sell them as two findings. Say "liquidity",
  never "manipulation". The map carries no buy/sell instruction. Sent ONCE a day
  by the daemon at `DESK_MAP_HOUR`, keyed on the calendar date, marked sent only
  on success (a dead token retries, not loses the day).
- ⭐ **An alert must carry the trade, not just the level.** `Level` has
  `call_side/stop/target/why`; a level clearing the desk's R:R floor (1.5) renders
  `🟢 اشترِ — stop, target, R:R, why`, else it stays a silent marker (never invent
  conviction). `from_scenarios` must get the owner's window + account, or the
  alert's stop won't match sizing.

## MarketDesk — Phase 0 closed + the acceptance scoreboard (2026-11-02, late)

- ⭐ **An acceptance criterion is a computation or it is decoration.** The plan
  doc's eight "when is this strong?" rows are now `desk acceptance`
  (`desk/acceptance.py`), each printing its measured number vs its target. Same
  lesson as the open-bar / budget / scoreboard ones — *the doc row, not the code,
  was the thing that could lie.*
- ⭐ **Split a gate row from an outcome row.** `Check.gate`: structural rows (one
  machine, no stored numbers, balance, decision rate, scenarios, quality) exit
  nonzero — defects. Market-outcome rows (`edge`, `calibration`) are marked 🟠 and
  do NOT fail the command: this market has no timing edge (t=−0.28) and the desk is
  not calibrated (27.3 pts), and *saying so is the criterion*. **A CI gate that goes
  red for a true market fact is a gate people learn to ignore.**
- ⭐ **The last hardcoded number shown as fact is gone.** `plan.py`'s
  `RUNG_REACH = {1R:0.683, 2R:0.540, 3R:0.413}` was one table for *both sides of
  every symbol*. Now `evidence.rung_reach(C)` measures per side (gold: long
  73/65/58%, short 47/18/12%), using `measure`'s own first-touch / ambiguous-bar-
  takes-the-stop rule; a side with no sample returns `{}` and the card prints no
  figure. `summary_line(m, reach)` carries it onto the recommendation card, so
  `/plan` and `/recommend` read one engine (the acceptance check verifies the two
  loaders' bar sets AND the evidence dicts agree).
- **Push:** this remote has **no credential helper** — a bare `git push` prompts
  for a username and hangs. Push with the injected token inline:
  `git push "https://x-access-token:${GITHUB_TOKEN}@github.com/<o>/<r>.git" main:main`,
  then `git fetch <same-url> main:refs/remotes/origin/main` to sync the tracking ref.
- ⚠ **Standing owner condition vs. this task's acceptance criteria — RESOLVED.**
  The owner said keep the 10-year study OUT of the market reading / plan /
  recommendations / scenarios. Section 6 of the plan the owner asked for still
  *requires* study-based validation (walk-forward edge, calibration). Resolution
  (`b6f0bed`): one flag, `Settings.show_study` (`DESK_SHOW_STUDY`, default OFF),
  gates every study statistic on every live surface; the study lives on as
  **validation/dev tooling** (`desk acceptance`, `walkforward`, `calibration`,
  `gold`). The plan card now prints the study's **verdict** (supported / not) but
  no reach %, no n, no control baseline. Nothing deleted — a display switch.
- Suite: **291 passed**; mypy clean (50 files); ruff clean.
- ⭐ **A trade without a stop cannot be graded, so it is not a record.** Added
  `desk journal add|close|grade` (`desk/journal.py`, `4293b00`) — the owner's own
  trades, measured the desk's way: **first touch wins, a bar spanning both takes
  the stop, expectancy in R not points**, an unresolved trade never a win. `add`
  refuses a missing stop. `data/journal.db` is git-ignored (private, not the
  blackboard). mypy caught 4 real `float | None` bugs here — the checker is a test
  the tests do not run.
