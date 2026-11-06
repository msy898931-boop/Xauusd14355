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

## MarketDesk — lesson index (detail → `MARKETDESK.md`, daily log `2026-11-02.md`)

Every MarketDesk lesson from 2026-10-30 → 11-06 now lives in `MARKETDESK.md`
(moved verbatim 2026-11-02 to keep this index small). The recurring ones:

- ⭐ **An unknown reported as a known** is the root of nearly every bug here: a
  close read off an open bar, a stop falling back to 1% of price silently, a
  `regime` field stored and read by nobody, `NEUTRAL_BAND` hardcoded in 13
  places, a gate reading a not-yet-written row. **Audit for data stored but never
  read before adding anything.**
- ⭐ **The metric, not the code, is usually what lies.** Grade the *plan* (first
  touch wins, ambiguous bar takes the stop) and report **expectancy in R**; check
  every result against a constant-direction control and a regime split; a
  horizon is graded only once a candle exists past it.
- ⭐ **"Configured" is not "working".** Probe it (`getMe`, a real send, a write,
  a `getChat`). A dead webhook is worse than none.
- ⭐ **A rule the desk cannot enforce is a claim.** An unenforceable budget, a
  tolerance nothing can satisfy, a level list typed into a render.
- ⭐ **An alert must carry the trade and a fixed line** — watch *printed* levels,
  read state from the *closed* bar, keep the 10-year study out of live surfaces
  (`Settings.show_study`, default off).
- **Deploy:** GitHub Actions cron (not Render); a bare `git push` hangs → inline
  token; the GitHub App must list the repo; secrets are owner-set only.

## MarketDesk — acceptance, the journal, and the map (2026-11-02, late)

Detail → `MARKETDESK.md` (`desk/acceptance.py`, `desk/journal.py`, `desk/map.py`).

- ⭐ **An acceptance criterion is a computation or it is decoration.** The plan
  doc's eight "when is this strong?" rows are `desk acceptance`, each printing
  its measured number vs its target. `Check.gate` splits **structural** rows
  (defects → exit nonzero) from **market-outcome** rows (`edge` t=−0.28,
  `calibration` 27.3 pts → 🟠, do not fail): *saying so is the criterion*. A CI
  gate that goes red for a true market fact is a gate people learn to ignore.
- ⭐ **A trade without a stop cannot be graded, so it is not a record.**
  `desk journal add|close|grade` measures the owner's own trades the desk's way:
  first touch wins, a bar spanning both takes the stop, expectancy in R, an
  unresolved trade never a win; `add` refuses a missing stop; `Trade.money`
  returns **None** when the size was never recorded, never a guess.
- ⭐ **A level's side is where it SITS, not what pivot it was**, and the
  regression test must be **directional** — a symmetric zig-zag passes on the
  broken code, so the test, not the code, was the bug. Same for "a number typed
  into a render": zones now come from `map.build`, setups from `scenarios.build`.
- ⚠ **Owner condition:** keep the 10-year study OUT of live surfaces. One flag,
  `Settings.show_study` (`DESK_SHOW_STUDY`, default OFF), gates every study stat
  on the card; the study lives on as validation tooling (`acceptance`,
  `walkforward`, `calibration`, `gold`). Nothing deleted — a display switch.
- **Push:** no credential helper on this remote; a bare `git push` hangs. Use
  `git push "https://x-access-token:${GITHUB_TOKEN}@github.com/<o>/<r>.git" main:main`.

## MarketDesk — the indicator hunt: the scanner rejected nearly all of them (2026-11-02, night)

- ⭐ **Most "strong" indicators add EXPOSURE, not TIMING.** EMA cross, momentum60,
  breakout20 and RSI-reversion all failed to beat **"always long" on the SAME
  bars** (10y daily, 2,514 bars) — they were long more often and gold rose, so
  they looked skilful without adding information. RSI reversion died
  out-of-sample. **A backtest that compares a strategy to nothing blesses a coin
  that is long 80% of the time.** Use the desk's own `scan` (same `grade()`, same
  in/out split, t≥2.58) as the judge, not a blog post.
- ⭐ **The one thing that survived — the regime pattern.** EMA20/50, mom60 and
  breakout20 each beat always-long in the LOW and HIGH vol thirds and each LOST
  in the **middle** third (EMA −0.176R t=−2.88; mom60 −0.234R t=−3.69); breakout20
  made **+0.318R below ADX 25 vs +0.042R (t=0.35) above it**. ATR **percentile
  rank** separated where fixed ATR bands did not (t=+2.44 vs −0.05).
- ⭐ **A weight is earned by a measured edge, not awarded by a hypothesis.** New
  `regime` agent (`desk/agents/regime.py`): ADX + ATR percentile, **`weight=0.0`**,
  and the orchestrator excludes `kind="regime"` from the tally beside `risk` and
  `volatility`. Both guards, so a refactor cannot re-enable it by halves.
- ⭐ **A claim the desk cannot test is a claim it should not trade on.**
  `Grader._regime_expectancy()` buckets every graded plan by the regime recorded
  on its own run and prints R per bucket (under "التوقّع حسب نظام السوق"). Empty
  for pre-agent runs — never an invented bucket.
- **DXY is the measured driver: rho −0.393** (stable −0.468/−0.409/−0.359 across
  the thirds; also works on 1h). The agent already picked by measured rho, but the
  peer list led with USDJPY (~0.00) — so the strongest real relationship was never
  in the list. As a *filter* DXY adds only +0.042R → peer, not gate.
- ⭐ **Third instance of "the data was honest, the plumbing dropped it":** the
  volatility agent's `regime` field was stored, carried, and **read by nobody**
  (same as `is_open` and the 13 hardcoded `NEUTRAL_BAND`s). **Audit for data
  stored but never read before adding anything.**
- **Phase 1 was never blocked:** biquote ignores `limit` (≤301 1m bars/call) but
  the desk **accumulates with no pruning** — 1m went 4,154→4,278 rows in ~2h.
  Intraday history builds itself forward; nothing to build.
- Suite **312 passed**; ruff + mypy clean (51 files). Commits `267366e`, `a8429af`.
