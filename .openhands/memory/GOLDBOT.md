# GoldBot (project 1)

_Archived from `MEMORY.md` on 2026-11-03 (it had grown to ~68 KB across three projects)._
_This is the full detail for GoldBot (project 1); `MEMORY.md` keeps only the durable index._

Telegram bot for XAUUSD (MT5) analysis on SMC/ICT. Own Groq key, own bot, own
config. Python 3.13, stdlib urllib only, pytest. Package root `src/goldbot/`,
run via `PYTHONPATH=src python3 -m goldbot.cli <cmd>`.
**The user speaks Levantine Arabic — answer in Arabic.** Code/comments English.
Never echo secrets (`GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).

**AYZR** = a *separate* AI-first gold engine in `src/goldbot/ayzr/` (pushed to
`main`): a model *decides* the trade (vs `ai/analyst.py` which narrates), then
four arithmetic checks validate it; every signal is journalled with a coin-flip
control; own key `AYZR_AI_KEY` (falls back to `GROQ_API_KEY`), own quota, own
journal `<AYZR_DATA_DIR>/`, one `ayzr:`-namespace Telegram row. Shadow-test
~1 month before deciding if AI replaces the plan. Details: daily log 2026-10-28.md.

**AYZR ready to start (commit `5d936e5`).** Two faults fixed before the test:
(a) **dedup** — `signal_key` was dead code, so the engine journalled the same
idea every pass and `report` counts each line, reaching `MIN_SAMPLE=100` on ~10
ideas; identity is now `idea_key = direction + zone_key` (the validator's zone,
**not** the model's drifting prose) and `Engine.run` skips an open idea into
`Run.skipped`. (b) **a clock** — `ayzr/runner.py` (`ShadowRunner`, one pass per
bar, never raises), CLI `loop`/`--once`/`--interval`, and `Bot._start_ayzr`
gated on opt-in `AYZR_ENABLED` (**off by default**). Start the test by setting
`AYZR_ENABLED=1` + its own `AYZR_AI_KEY`. **Not yet running** — no confirmed
live deployment, see daily log.

## Commands
- `analyse` | `card` | `news` | `send` | `bot` | `backtest` | `study`
- Tests: `PYTHONPATH=src python3 -m pytest tests starter/tests -q` (**812**;
  `testpaths` adds `starter/tests`). `pip install -e .` then `pytest`.
  `make check` = ruff/format/mypy/bandit/pip-audit/pytest.
  **Run keyless before pushing** (local secrets mask a CI failure):
  `env -u TELEGRAM_BOT_TOKEN -u TELEGRAM_CHAT_ID -u GROQ_API_KEY python -m pytest tests starter/tests`.
- `python -m goldbot.cli card` is the cheapest *real* check — it hits the live
  model, which no stubbed test does. Run it before a deploy.
- Start the loop: re-export the two Telegram vars **in the same command** as the
  launch (nohup drops injected secrets). Log `run/bot.log`; state under
  `GOLDBOT_DATA_DIR`. First cycle primes and sends nothing.

## Data sources (decided with the user; detail in the daily log)
- **Live candles** `kraken:PAXGUSD` (tokenised gold, spot-linked, tracks MT5
  XAUUSD closely; ignores `since`, ~720 bars max).
- **History** `binance:PAXGUSDT` via **`data-api.binance.vision`** (NOT
  `api.binance.com` → 451); pages backwards with `endTime`, 1000/request.
  Depth M15 ~7mo · M30 ~14mo · H1 ~2y · H4 ~6y.
- **Spot cross-check only** `gold-api:XAU` (no candles). Yahoo `GC=F` off by
  default (contango ~0.28%; 429s).
- `MarketFeed.series(tf, bars, prefer_depth=False)`: a live read wants the
  freshest tape, a backtest the longest. `data/archive.py` folds fetches into
  the archive and serves as the offline fallback.

## Root causes of the losing backtest (all three fixed)
1. **No top-down gate** — `pick_setup` took the higher of both directions, so a
   downtrend kept emitting buys (buy WR 6.2%). Counter-bias is refused early.
2. **Unweighted frame tally** — choppy M5/M15/M30 outvoted a clean H4.
   `FRAME_WEIGHTS` halves per step down.
3. **Inconsistent frame depths** — each frame is now windowed to `bars` (220).

After: sell profitable (+6.9R, WR 36.4%), buy weak (9.1%) — ~7.5 days.

## Groq notes
- Free tier: **8000 tok/min, 200k tok/day, per model**. A full fact-sheet call
  ≈ 5250 tok ⇒ **~1 call/minute is the ceiling**; *your own probing* trips it.
  `RETRY_BACKOFF` (20s) cleared a per-minute limit; a *per-day* limit needs the
  model **parked**. `GROQ_FALLBACK_MODELS` is tried in order, own quota each.
- **`max_tokens` on a reasoning model covers scratchpad + answer**, so size it
  for the thinking. 3000 made `gpt-oss-20b` return `400 json_validate_failed`
  (JSON cut mid-object); 6000 is valid. The 400 must **advance the chain**,
  never re-raise. Empty `content` is a failure, not an answer.
- Cloudflare rejects urllib's default UA and a custom bot UA with 403 (1010).
  Only a browser-like UA passes — same for Kraken/gold-api.

## THE HEADLINE RESULT (5-year sample): the strategy is gold BETA, not skill
1657 trades (M15) · 1452 (M30) · 753 (H1). M15 avgR -0.021 · M30 +0.044 ·
H1 +0.109 (PF 1.14). Only H1 is positive and it does **not** survive Bonferroni.
The decisive test is the regime split: **it wins when gold rises and loses when
gold falls, in every direction and every frame** — a long-gold bet wearing an
SMC costume. The one cell that is *not* beta: H1 **BOS continuation** (n=202),
but its CIs straddle zero and the halves decay. **Do not tune parameters to
"fix" this — the problem is not the settings.** Full tables in the daily log.

Now **measured**, not argued: `backtest/control.py` (session 9) re-runs the
strategy's trades beside matched surrogates (same geometry/cost/fill/horizon,
random entry time), with a second control drawing only from the trade's own
trend regime. Verdicts: skill (beats both at α=.05) / beta / inconclusive.
`research/study_control.py` runs it over the archive. **The design detail that
matters:** surrogates must draw from the window the real trades occupy, not the
whole sample — a mismatched window turned the control's false-positive rate on
zero-skill entries from 0% to **63%**. Fixed by drawing from the trades' own
span; a test guards it. Zero-skill cells that beat a mismatched control were
reading drift as timing.

**`cli study` (session 10) makes the whole thing one command.** `backtest/report.py`
computes the headline metrics as pure functions of the trade list; `study.py`
loads the deepest series per frame, splits the trades into contiguous windows,
and runs the control. Signature rule: **a metric that cannot be computed is
`None` → em dash, never `0`** — zero is a number a thin sample didn't earn.
On 833 days of H1 (archive filled via Binance paging, 800 filled trades) the
verdict is **beta**, expectancy +0.025R, PF 1.03, 2/5 windows positive —
the hand-made call, reproduced on a sample ~20× longer. `study` must read the
frames the **live bot** reads (`settings.timeframes`), not an ad-hoc subset; a
reduced `PRIMARY_PREFERENCE` only picks the walked frame. Full H1 study ≈ 4–5
min — run in background. **Per-setup control** (`setup_control`) scores each
setup against its *own* surrogates: BOS continuation read `skill` on H1
(+0.355R, p=0.003) but **`beta` on H4** (p=0.72) — a single-frame `skill` is
**not evidence** until it replicates across frames. This is the keystone: the
tool exists to stop one tempting frame from becoming a claim.

## Telegram
- Bot `@Xauusd1435_bot` (id 9261676602); `Telegram` (not `TelegramClient`) is
  the class in `telegram/client.py`. Keyboard is **two buttons only**
  (🔄 تحديث, ⚙️ الإعدادات); `MENU` is the single source of truth.
- `Bot.tick` logs "فشل إرسال فكرة" when `send` returns False.
- **Secrets reach a conversation only at start** — a running sandbox keeps its
  frozen copy, so edit the secret then start a NEW conversation. `nohup ... &`
  drops injected secrets; export them in the same command as the launch.
- Read a stored secret without the UI: `GET
  https://app.all-hands.dev/api/v1/sandboxes/search` (`Bearer $OPENHANDS_API_KEY`)
  → sandbox id + `session_api_key`; then `GET
  /api/v1/sandboxes/<id>/settings/secrets/<NAME>` with `X-Session-API-Key`.

## Durable state under `GOLDBOT_DATA_DIR` (session 8)
- One root for `bot_state.json`, the candle archive, and `calendar.json`.
  `Settings.state_path` is a **property**; `Bot.__init__(..., state_path=None)`
  falls back to it (an explicit path still wins — that is what tests pass).
- **Why:** the state carries the per-day AI counter. On an ephemeral filesystem
  a restart reset the cap — *the exact failure the cap exists to prevent* — and
  re-fired the day's announced news. `render.yaml` mounts 1 GB at `/var/data`.
  Empty keeps the old relative paths; an explicit `GOLDBOT_ARCHIVE_DIR` wins.
- **`goldbot.onrender.com` is NOT this project** (its `/` says "PONG!"). The
  real service URL is unknown to the owner, so `keepalive.yml` skips green.
- **Liveness without intervention:** the bot IS running — proven by a
  `getUpdates` probe returning Telegram **`409 Conflict`**. A health `200`
  proves only that you just asked.

## 🧠 THE LESSONS THAT KEPT BITING (detail in the daily log)
Every failure was **an unknown reported as a known**:
1. **A check that is also an intervention cannot observe** (my curl *was* the
   traffic resetting Render's idle timer).
2. **Silence must never be rendered as an all-clear** — a failed read says
   "تعذّر الفحص", never the reassuring default. Health flags live in the *data*.
3. **Report the reason, not the exception class.**
4. **A number must carry its provenance** — frame alignment is not a chance of
   success; the fallback confidence is 0 and omitted.
5. **Record what a path does not do, not only what it saves.**
6. **A dedupe key built from a drifting value is not a dedupe key** (identity
   only: `direction:setup`, `country|title|time`).
7. **A stub at the HTTP boundary hides a broken prompt or budget.** The AI was
   dead in production for want of a token count and *no* test could see it.
8. **A fail-closed gate needs a fallback, or it is a silent off-switch** (see
   the calendar note below). The dangerous failure is **no trade at all**, and
   it looks exactly like "the strategy found nothing".
9. **"Is the source down?" ≠ "is the source refusing *me*?"** — a browser
   answered the first and hid the second.

## News sources (ask "شو المصدر؟" and mean it)
- **Calendar** — `nfs.faireconomy.media/ff_calendar_thisweek.json` (FF mirror,
  keyless). Only *thisweek* exists; *nextweek* is 404. Rate-limits **per IP** via
  Cloudflare (`Retry-After` up to 300s), and probing keeps the limit alive. It is
  the **IP**, not the UA: a browser UA gets 429 while the bot UA gets 200 in the
  same minute. Test reachability from the bot's own request, never a browser.
- **The saved read (PR #11).** A transient 429 used to withhold **every** idea.
  The last good read is now saved to `GOLDBOT_DATA_DIR/calendar.json` and
  restored on start, so a blocked fetch degrades to *stale but usable*. The flag
  that matters in `digest` is `calendar.usable` (ok **or** a saved read), not
  `ok`; `checked=False` is reserved for genuinely-nothing-to-reason-about. A
  saved read is refused past `MAX_STALE` (3d) or when every event in it is past.
- **Headlines** — 3 keyless RSS feeds. Labels used to **lie**: the "Reuters
  Markets" URL (`feeds.a.dj.com`) is **WSJ**, and `news_301.rss` is Investing's
  **Cryptocurrency** stream. Now: WSJ Markets, Investing Commodities (`news_11`),
  Investing Forex (`news_1`). Feeds carry **no body text** — `Headline` is
  `title/source/link/published` only, so "reading the news" is headlines.
- **Finnhub free tier (measured, not adopted).** `news?category=...` is free but
  **`calendar/economic` is 403 premium**. Weaker than keyless RSS ⇒ do not add
  the key; the keyless invariant holds.
- Blackout: `build_plan` is **not** gated (by design — the price card always goes
  out), but every *presentation* path is, through `format.blackout_blocks_idea`
  (`active or calendar_unchecked`). Three facts stay distinct in the cards:
  window open / read+calm / could-not-read. Invariant #5: "fail closed, not open".

## Cloned repo `msy898931-boop/my-awesome-app`
Clone at **`/workspace/my-awesome-app`** (NOT `/workspace/project`). CI on
py3.10+3.12. Hardened over 11 PRs — **full history in the daily log**. Recent:
#9 AI was dead in production (budget 3000→6000, 400 advances the chain); #10 the
card names the model that wrote the read; **#11 the calendar read is saved to
disk** (612 tests). `main` = merge of #13. #13 added the **time axis** to the equity curve and
  the **honesty window**: the study reported the *bar* span as the sample, but
  fills sit in the last ~18% — a plan needs an HTF+LTF and the low frames reach
  back the fewest days, so the walk can't fire until the deepest LTF appears.
  **H4 = every cell beta** (pooled +0.083R below both controls); BOS read skill
  on H1, beta on H4 — the edge does **not** survive off H1.
- ruff/format take `src tests research starter`; mypy/bandit take
  `src/goldbot starter/goldkit`; `testpaths = ["tests", "starter/tests"]`.
- Repo has **`delete_branch_on_merge: false`** — delete a merged branch
  explicitly, only after `git merge-base --is-ancestor <sha> main` confirms it.
- ⚠️ **Ruff's yoda-condition autofix reverses `X == CONST`**; assert membership
  (`"http" in ALLOWED_SCHEMES`) not equality.
- ⚠️ **Bare `pip-audit` audits the whole env** — scope it `pip-audit .`.
- **`create_pr` opens the PR as a DRAFT** (observed for #5, #7, #11). Un-draft via
  GraphQL `markPullRequestReadyForReview(input:{pullRequestId:<node_id>})`
  (`node_id` from `gh pr view <n> --json id`); REST has no un-draft, and
  `PUT /pulls/<n>/merge` refuses a draft with "still a draft".
- After pushing, **keep `origin` tokenless** and push with the token URL
  explicitly: `GIT_TERMINAL_PROMPT=0 git push https://${GITHUB_TOKEN}@...`.
  A plain push against the tokenless URL hangs; leaving the token in the remote
  writes a secret to `.git/config`.
- GitHub Actions `schedule` is **best-effort** (0 runs across 13+ slots in 2h).
- Render Free: 15-min idle spin-down, "may restart at any time".
- Sandbox **Docker daemon is down by default** → `sudo dockerd`.

Full detail → `2026-10-28.md`.
