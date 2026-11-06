# MarketDesk (project 3)

_Archived from `MEMORY.md` on 2026-11-03 (it had grown to ~68 KB across three projects)._
_This is the full detail for MarketDesk (project 3); `MEMORY.md` keeps only the durable index._

## MarketDesk — the multi-agent market/news desk (2026-11-02)
`/workspace/project/marketdesk/` — pure stdlib, no deps. Central orchestrator:
`gather()` (biquote candles+quote → shared SQLite) → `dispatch()` (**news agent
FIRST, alone**, then the other 7 concurrently) → `synthesise()` (weighted scores +
risk gate + plan). **8 agents:** structure 2.0 · trend 2.0 · levels 1.5 · news 1.5
· momentum 1.2 · risk (veto) · volatility 0.8 · correlation 0.7. Each agent has
private JSON memory + publishes a `Study` to the shared blackboard. Run **~7 s**
with Groq, ~3 s keyless. **35 tests.** Add an agent = subclass + one line in
`ROSTER`. Commands: `run` · `hit-rate` · `watch` · `serve` · `agents` · `memory`.
⚠ **Groq 403 `error code: 1010` without a browser User-Agent** (Cloudflare) —
the model silently returned None and agents self-narrated until `llm.py` set the
UA. Same wall as Kraken/gold-api/FF.
⚠ **A reasoning model's `max_tokens` must cover thinking AND the answer.**
`gpt-oss-120b` with `max_tokens=800` spends it all on the scratchpad and returns
**empty content** (looks like a failure, is a budget bug); 1500 completes.
⚠ **Groq meters a SEPARATE daily token quota PER MODEL** (200k TPD each) — so a
spent quota on one model is not a spent quota on the desk. `llm.py` takes a
*chain* and rotates on `429/402/403` (cooling that model for the provider's own
`try again in …` hint). Chain: `gpt-oss-120b → gpt-oss-20b → qwen/qwen3.8-27b →
allam-2-7b` (`DESK_MODELS` / `DESK_MODEL` override). **When asked to "pick a
better model", check first whether the options are interchangeable *resources*
rather than alternatives — a chain can beat a choice.** On 10 hand-labelled gold
headlines `gpt-oss-20b` and `qwen3.8-27b` both scored 10/10; `allam-2-7b` cannot
return the JSON, keep it last. A `429` is never retried on the same model
(retrying turned a 7s run into 22s). The desk still runs keyless — the *prose*
falls back to deterministic sentences, and the **report says so**; it also prints
`↻ النموذج: … (دار N مرة)` when it rotated.

**Lessons this desk taught (each cost a real bug):**
- ⭐ **Indicators must read CLOSED bars only.** `frames_data()` handed every agent
  the newest bar, which is still forming — its h/l/c move with the tick, so an
  indicator read off it is not a fact. Measured: 1d RSI **38.0 with the partial
  bar vs 30.8 without** (Δ7.2); 1h 59.8 vs 65.1. The `is_open` flag was already
  stored *and* carried into the reshaped bar but **no consumer ever read it** —
  the data was honest, the plumbing dropped it. `live_price()` = quote if fresh,
  else last closed close.
- ⭐ **A budget you cannot enforce is a claim.** `as_completed(timeout=None)`
  waits for the first *completion*; `future.result(timeout=remaining)` only bounds
  an already-done future. A 3s task under `budget_s=0.1` still took 3.0s. Use
  `cf.wait(futures, timeout=remaining)`.
- ⭐ **Measure a relationship, do not award one.** The correlation agent gave a
  fixed 0.3 whenever gold and USDJPY moved opposite ways, whether or not they had
  ever co-moved. Now: Pearson rho of hourly returns, score scaled by it.
- **A comparison is only fair if both sides get the same input.** The keyword map
  was fed title+description, the model only the title — so "model disagreements"
  partly measured the missing field, not the judgement.
- **The neutral band was hardcoded 0.15 in 13 places.** One `NEUTRAL_BAND`
  constant now feeds every agent's stance and the orchestrator's verdict.
- ⭐ **A scoreboard that grades direction flatters the desk.** A short that
  reaches its target *after* first taking out its stop was counted a win. Grade
  the **plan**: walk the bars, take the *first* touch, count an ambiguous bar as
  the stop, and report **expectancy in R** (60% at 1.2R loses money; only R shows
  it). This is the same lesson as the open bar and the budget — **the metric, not
  the code, was lying.**
- ⭐ **Run mypy; it finds bugs tests miss.** First run: 36 errors. Most were
  inference noise (`per = {}` → `dict[str,str]`) but one was real — `summary`
  used as both a dict and a string in `correlation.py`, so `Study(summary=...)`
  was about to get a dict. Tests never covered that line.
- **A locked SQLite database is a scheduling fact, not an agent failure.**
  `busy_timeout=30000` + a bounded retry on "locked"/"busy" only; route every
  write through one helper rather than sprinkling try/except.
- ⭐ **"Configured" is not "working".** `Notifier.available` only checks the token
  and chat-id strings are non-empty. The `TELEGRAM_BOT_TOKEN` in this environment
  is **invalid (`getMe` → 401)** — `available` is True, `send()` returns False.
  The desk stays silent, which is the designed behaviour, but the owner would
  think alerting works. A `getMe` probe is the only honest check, one API call.
- ⭐ **Secrets must be passed on the SAME command that starts a background
  process.** The harness injects them into that command's environment; a later
  command does not see them. Verify with
  `tr '\0' '\n' < /proc/$PID/environ | grep -c TELEGRAM_BOT_TOKEN=`.
- ⭐ **A write probe leaves a real commit on the remote — it is not free.**
  When GitHub App access looks blocked, `PUT contents` is the honest test (the
  `/repos` `push: true` field lies). But that probe creates a commit; after
  access is granted, **list the remote before any `--force`** — here it held
  only my own probe commits, so forcing over them was safe.
- ⭐ **"Denied to <owner>" is the signature of an App, not of the user.**
  `remote: Permission to <o>/<r> denied to <o>` + 403 + `x-oauth-scopes` empty
  = the OpenHands GitHub App is the actor and the repo is not in its
  `repository_selection`. Fix is owner-side in the UI; code is unchanged. The
  selection can be **switched** (analysisdesk → Xauusd14355) rather than
  extended — re-probe, don't assume.
- **News must run BEFORE the risk gate.** All 8 in one pool → the gate reads
  SQLite instantly while news waits on the network, so the gate read an empty row
  and waved through the event it exists to block. **When one agent's output is
  another's input, concurrency is a correctness bug, not a speed knob.**
- **A gate that blocks on *any* event is a gate that is always shut.** It now
  needs the event inside a short window (6h) AND in the *instrument's currency*
  (USD for gold). An RBA decision must not block gold; JOLTS must.
- **Enforce reward:risk by walking the levels outward.** Taking the nearest 3
  levels and rejecting them refuses *every* setup (the nearest level is always too
  close to pay for the stop). No real level clears the bar → state no plan.
- **The scoreboard must stay honest:** grade a horizon only once a candle exists
  past it (never against the current price), and exclude flat bias + the deadband
  rather than counting them as wins. Record the desk's *implied* bias even on
  `no-trade`, or 13/13 silent runs leave nothing to grade.
- ⭐ **Reading a Telegram update does not confirm it.** The Bot API confirms only
when a LATER `getUpdates` passes a higher offset; there is no "ack". A scheduled
run makes one call, so the offset was never sent back and *every pass replayed
the whole backlog*. Tell: two runs whose `next_offset` went **down**. Fix:
`poll()` ends with `confirm(next_offset)`. Verified live: run 1 answered 23,
run 2 `updates: 0`.
⭐ **A fake that is too generous cannot catch the bug it models.** The test
double replayed every update every call, so it could not show the cursor was
stuck — it modelled the response, not the rule. Copy the *rule* (the watermark
moves only on a later call) and the bug becomes a failing test in one run.
⭐ **A stale comment can BE the bug.** Three places asserted `getUpdates`
confirms "in the same call that reads it" — the false belief that made the
missing confirmation look deliberate. When you fix a bug, grep the *prose* that
justified it.
✅ **GitHub `schedule` DOES work here** — it was only the new-schedule delay.
By 05:39Z: `schedule: 2`. The watch fired on cron (03:38Z) and the bot fired on
cron (03:56Z) and **answered a real button press**. End-to-end loop proven.
**A new GitHub schedule can take hours; no runs yet is not yet evidence of
breakage.** Check `gh api .../runs --jq '[.workflow_runs[].event]'`.
⭐ **A stop inside the noise is not a stop — and it inflates the R:R.** The plan
hung the stop on the *nearest* level + 0.3 ATR. Live that was **0.42 ATR** —
noise — and it printed **R:R 3.60**. Now floored at 1 ATR, clearing the level by
half an ATR; the same setup now says **no trade**. Third time here: the metric,
not the code, was lying.
⚠ **The desk has ZERO graded runs** — no track record at all. Biggest gap now.
Data is thin below daily: 1d 500 bars, 4h 181, **1h 169 (~11 days), 15m 193
(~4 days)**, so only 1d can support a real backtest.
⭐⭐ **A backtest that reuses the live store reads the future.** `save_candles`
upserts and never deletes, so bars from *after* the decision bar stay. Reusing
the package store made the trend agent see all 500 bars at every step and call
**"bear" 379/379** on a market that **rose 18%** — 306 trades, all shorts, at a
"significant" **+0.23R**. Fresh temp store per walk + a guard that raises when
the newest bar is not the decision bar. **Look-ahead produces a beautiful,
significant, wrong answer.** Tell: an all-one-direction book, or a direction
hit rate *below* 50% with a positive expectancy (profit only from the R skew).
⭐ **The stop's ATR came from `frames["1h"]` alone** — absent that frame it was
`None` and the stop silently became **1% of price**. Pick the frame from what
the volatility agent reported; record it as `atr_frame`; warn when unmeasured.
✅ **`desk backtest` exists now (PR #10) and the answer is: no proven edge.**
Across warmups 60–300: meanR +0.13..+0.24, **every 95% CI spans zero** (t≤1.8);
direction hit 55% on 235 (z=1.6). Sign flips with horizon; long 36%/-0.32R vs
short 52%/+0.48R on samples too small to separate. Scope: news+correlation
neutral (no historical feed), only 1d has history — a floor, not the whole desk.
⭐⭐⭐⭐ **A positive expectancy on one sample is not an edge — test it against
constants.** The desk scored +0.37R, but it was short on **60% of a window where
gold rose 18%**. Replaying its own decisions with the direction replaced:
`always short +0.374R` == `desk +0.371R`; paired diff **-0.003R, t=-0.02, CI
[-0.249,+0.244]**. A constant wins in **all three regime thirds**, and a
**two-line EMA20/EMA50 scores +0.315R vs the desk's +0.371R**. So the agents add
nothing separable; it was a regime, not a method. **Every backtest result must be
printed with (a) a constant-direction control, (b) a regime split, (c) a trivial
benchmark.** Fifth time here that the number lied — first time it was mine.
⭐ **Measure excursions only up to the exit.** The first pass measured MFE over
the whole window *even after the stop was hit*, flattering every exit rule.
Replay bar by bar and check the stop first.
⭐⭐⭐⭐ **A long-biased signal in an uptrend must beat the DRIFT, not just the
desk.** "always long" scored +0.283R on 20 months of gold. Paired on the same
bars, `ema20>ema50 - always_long = +0.249R (t=+3.83)` and `price>ma50 - always_long
= +0.235R (t=+3.40)` — both clear a Bonferroni bar for 5 families (|t|>2.58) and
hold in a 70/30 split. **The first result in this project to survive a control.**
But it is ONE sample of ONE market, and on the desk's own 184 bars trend
(+0.621R) is only *ahead* of the desk (+0.522R), not provably ahead (t=1.26).
⭐⭐ **A "neutral" score that is only ever negative is a bias with a disclaimer.**
`volatility` returned -0.2 on high vol while its own comment said it is not
directional: bear 46.2%, bull 0.0% over 379 decisions. Skip non-directional
agents **by `kind`**, not by name.
⭐ **A stop/target grid is not where a missing edge hides.** 25 cells, each with
constants + regime split: none survived. `stop 1.0/tgt 1.0` gave desk +0.185 ==
constant-short +0.185 exactly.
⭐ **biquote caps at 500 daily bars (~20 months)** regardless of `limit=`
(2000/5000/10000 all returned 500). 4h=181, 1h=169. There is no more history.
**Gates:** `pyproject.toml` + `marketdesk-ci.yml` run pytest/mypy/ruff on every
push and PR. Coverage 84%; `sources/biquote.py` 36% (the only network module).
**The repo is PUBLIC, has no licence, and tracks `biquote/*.csv`** (biquote's
market data) — a licence question, not a code bug.

**Model news labelling:** model labels vs the keyword map, disagreements
  recorded. Live 13/25 disagreed and the model was right — the map scored
  "Alphabet bond sale" as *bullish for gold*. Keep the keyword baseline as
  fallback; never let the model silently replace a computed value.
Detail → `2026-11-02.md`.

## MarketDesk deployment (session 3) — the Telegram question
Owner: "بدي اعمل جلسة ريندر جديدة مشان التلغرام شغلو هنيك بعتلي التفاصيل كاملة".
Asked to move the alerting to Render; the brief was requested before any deploy.

**RESOLVED at the end of the session** (owner pasted a real token in chat):
- ✅ `getMe` → **ok**, bot **`@Xauusd14355_bot`** (first_name `Xauusd14355`).
- ⚠ **The old `TELEGRAM_CHAT_ID` secret was ALSO wrong** — 10 digits vs the real
  **`933571066`** (9 digits), read from `getUpdates` after the owner sent `/start`
  (from `Almalek121` / رمضان عوينات). **Both Telegram secrets were wrong, not one.**
- ✅ End-to-end proved with the *desk's own code*:
  `check-alerts --send` → `getMe: ok · test send: delivered` (exit 0); a real
  `watch` pass ran and returned `{"verdict":"short","score":-0.157,"alerted":false,
  "reason":"conviction -0.16 below 0.25"}` — i.e. **the pipeline is live and the
  desk correctly stayed silent** (below the 0.25 threshold). That is the designed
  behaviour, not a failure.
- ✅ `render.yaml` written at repo root (cron job shape). 55 tests pass, mypy clean.

**Facts measured earlier in the session (each one a probe, not a guess):**
- ⭐ **The first `TELEGRAM_BOT_TOKEN` was STRUCTURALLY invalid, not just
  rejected.** len **35**, **no colon**, `getMe` → **404** (a malformed token path).
  A real BotFather token is `NNNNNNNN:AA…` ≈ **46** chars, **2** segments. So the
  value was not a token at all. **A shape check must assert the colon+segments,
  not only length** — `Notifier.available` checks neither.
- **The secret update DOES land across sessions.** Fingerprint `30c8f696` (len 35)
  is *different* from the 46-char `0c9f76d8` of session 2 → the sandbox is NOT
  frozen to a start-time snapshot (that hypothesis was wrong, or the harness
  changed). The owner's edits do propagate; this particular value is just not a
  valid token.
- ⚠ **A fresh command sees the secrets; `check-alerts` does not.** Plain
  `env | grep` shows `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` **set**, but
  `python3 -m desk.cli check-alerts` printed "NOT CONFIGURED" (token set: False)
  while the *same* command run right after DID see them. Injection is per-command
  and flaky/async — **put the secret names in the same command text and re-run if
  a probe says "unset"**. (Related: `nohup` drops injected secrets.)
- **MarketDesk has NO HTTP server** (no http.server/FastAPI/flask anywhere). It is
  a CLI/loop only. So a Render **Web Service** would not fit it; the honest
  primitive is a **Render Cron Job** (`python3 -m desk.cli watch`, one pass per
  trigger). ⚠ Render: **Cron Jobs and Background Workers are PAID — no free
  instance type** (min ~$1/mo per cron service); Free tier = static sites + web
  services (web sleeps after 15 min idle). Detail: render.com docs on async
  primitives.
- **The desk runs fine** from a fresh clone: keyless `run` **9.8 s**, verdict
  **بيع (short)** score **-0.22**, entry 3946.99 / stop 3956.53 / **R:R 1.90**,
  targets pivot/fib/pivot. With `GROQ_API_KEY` named in the command it took
  **13.9 s** and wrote model prose: `↻ النموذج: openai/gpt-oss-20b (دار 3 مرة بعد
  نفاد حصة موديل)` — i.e. **the first 3 models' daily quota is spent and the
  chain rotated down**, which is the designed behaviour. ⚠ **The earlier
  "النموذج غير متاح" run was NOT a Groq outage — that command text did not name
  `GROQ_API_KEY`, so injection skipped it.** Same trap as Telegram: a missing
  secret name in the command reads as "the service is down". `GROQ_API_KEY` and
  `TELEGRAM_CHAT_ID` (10 digits, plausible) are both fine.
- **The desk state lives under `marketdesk/data/`** (gitignored): `shared.db`,
  `signal_ledger.json`, `agents/`, `serve.log`. On a host without a mounted disk
  this is **lost on every redeploy/restart** → the alert dedupe ledger resets and
  an old signal could re-fire. Mount a disk or accept the reset (say which).
- **A cron pass every 15 min = ~2 880 runs/month**, each hitting biquote (~8
  timeframe pulls) + Groq + news. Billing is per cron *service*, not per run, so
  cost is flat — but rate limits and the "one reading per 15 min" cadence must be
  a deliberate choice, not a default.

**Recommended shape (owner-side, I cannot deploy — Render needs sign-in):**
Cron Job → `cd marketdesk && python3 -m desk.cli watch`, schedule `*/15 * * * *`,
env `TELEGRAM_BOT_TOKEN` (sync:false), `TELEGRAM_CHAT_ID`, `GROQ_API_KEY`
(sync:false), `DESK_HOME=/var/data` + a mounted disk for the ledger. Health of the
pipeline = `check-alerts --send`, not "the job is green".
**Blocker order: (1) a real token, (2) confirm the chat id, (3) the deploy.**

### Deploying the desk: GitHub Actions beats Render
**Render needs the owner's sign-in; I must not authenticate as them.** The
owner asked "can't you just work on Render?" — answer: no, and here is the
better route. Render Cron Jobs are also paid (~$1/mo min). **GitHub Actions is
free on a public repo, needs no card and no second account**, so it is the
default answer for "run this on a schedule" in this project.
**The App's scope forces a clean split — probe it, don't assume:**
`/actions/workflows` → 200 ⇒ *I can write workflow files*;
`/actions/secrets*` and `/actions/variables` → **403 "Resource not accessible by
integration"** ⇒ *I can never set secrets*. So **the agent authors the
automation and the owner adds the secrets by hand.** Repo `permissions` claims
`admin: true` while secrets are 403 — same "advisory vs actual" trap as the push
probe; only a real call tells the truth.
**⭐ A workflow log cannot distinguish an unset secret from a set one** — both
print `NAME:` with an empty value. A silent green run is not a verified run.
Add a presence-only step (`[ -z "${!v}" ]` → MISSING, else `set (length N)`)
from the start; it is what turned three unreadable runs into a diagnosis. Then
prove the alert path on demand with `desk.cli check-alerts --send`, which does
the `getMe` probe and delivers a real message — `test send: delivered` is the
only honest end-to-end proof. **Live result: token set, `getMe: ok · bot
@Xauusd14355_bot`, `test send: delivered`.**
**Watch out:** secrets get pasted into **Variables** (the tab beside Secrets)
or into the wrong repo — the owner has 12. Ask for repo name + which tab, or a
screenshot with values masked; never ask for the value. Also: a PR must be
**marked ready** before `merge-async`, and a commit message containing
backticks must go through `-F file`, or the shell runs them.
Detail → `2026-11-02.md`.

**Session + news (PR #24).** Two more "the metric, not the code, was lying":
- ⭐ **A cron does not know the market closes.** `*/15` on a Saturday wakes 8
  agents to read a frozen tape. `desk/session.py` states the spot-gold week in
  UTC (Fri 21:00 close → Sun 22:00 open); `Orchestrator.run(now=)` gates it
  **before any network call** and records a `no_trade` with the reason + next
  open (a silent skip is indistinguishable from a broken workflow).
  `DESK_RESPECT_SESSION=0` overrides. ⚠️ **`datetime.timetz()` keeps tzinfo and
  won't compare to a naive `time` — use `.time()` after `astimezone(UTC)`.**
- ⭐ **`fetch_news(symbol=...)` was not a filter.** biquote returned Apple,
  Aramco, a Binance hack — and the keyword map scored "Alphabet bond sale" as
  *bullish for gold*. A `GOLD_TERMS` whitelist now drops what can't move gold,
  and the summary states the count ("+3 على 16 من أصل 30"); an all-irrelevant
  feed says "ما في عنوان يخصّ الذهب" rather than a tone of zero. **A score whose
  input is the wrong market is a confident number about nothing.**
Detail → `2026-11-02.md`.



---

## Archived from MEMORY.md index (2026-11-02..04 detail)

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
- ⭐ **A deploy you cannot identify is a deploy you cannot trust.** The live
  marketdesk service exposed no revision: `/health` was `{ok:true}` only, so a
  green check could not distinguish new code from a stale build. It now reports
  `RENDER_GIT_COMMIT[:12]` (Render's default env var) or `v<version>` off-Render.
  One env var, one field — worth adding to any always-on service.
- ⭐⭐ **When every outcome has the same sign, it is a bug, not a view.**
  marketdesk was short 19/19, bias down 24/24, `trend` bear 56/56 (bull never),
  and 0/13 plans reached target (-13R) — while gold ROSE 3%. Cause: `trend` and
  `structure` took the price as `closes[-1]` (last CLOSED bar); on the daily that
  is up to a day old (**3938 vs live 4065**, 127 pts off), compared against a
  fresh EMA stack → a full-weight (8) bearish daily vote every run. It was one
  bet placed 13 times, not 13 losses. ⭐ **A close is a fact about a closed bar,
  not about now:** indicators read closed bars, but the price *compared against
  them* must be `live_price()`. Grep `closes[-1]` in any agent. Fixed + 2 tests
  proven to fail on the old code. Verdict went sell → no-trade.
- ⭐ **A live call needs live data.** `gather()` survives a source outage by
  reusing the DB — right for reading, wrong for alerting: a directional verdict
  from a feed dead for hours is a stale number dressed as a signal. marketdesk
  now gates only the ALERT on data age (`data_fresh()`, `DESK_MAX_DATA_AGE_S`
  default 3h, measured from `fetched_at` so a quiet market is not "stale").
  Reproduced by ageing readings + simulating an outage (verdict still came out).
- ⭐ **Do not judge production by the sandbox's injected secret.** marketdesk's
  Telegram token in the sandbox is malformed (35 chars, `getMe` 404), but the
  GitHub secret is valid (len 46) and the bot is live (`chat: Almalek121`,
  webhook). `check-alerts` locally lies; run `desk.cli telegram` from CI instead.
- marketdesk **open risks (accepted, not fixed):** one data source (biquote, no
  fallback); Actions cache is best-effort so a duplicate alert is possible after
  eviction; the scoreboard mixes pre-fix (always-short) runs with post-fix ones.
- ⭐ **Owner condition: keep the 10-year study OUT of my market analysis.**
  Base reading/recommendation/scenarios ONLY on live desk output (9 agents, live
  levels, scenarios, news). Never cite historical analogs, expectancy-R, n=47 or
  any 10y stat; never run `desk gold`; never show `plan`'s hardcoded
  `SIDE_EVIDENCE`. The desk's core (verdict + scenarios) was already study-free —
  the study only surfaced via `gold`, `plan`'s frozen evidence, and `chart`.

- ⭐ **"Alert once" is not "follow it".** The desk alerted on a new signal then
  went silent, so an issued call was never watched. `desk/active.py` now holds the
  ONE plan in play and reports only transitions (waiting->on_track->target/
  stopped/invalidated) + a heartbeat, expires an unfired plan, and drops one the
  fresh read contradicts. Two self-inflicted bugs: (a) it reported `alerted: True`
  on a FAILED send — never hide a failure; (b) the ledger fingerprint
  `symbol:verdict:score` let a COMPLETED plan suppress the next same-direction
  trade forever -> now `...:entry`. **The key was lying, not the code.** Fresh-VM
  runner: `active_plan.json` rides the cached `marketdesk/data`.
- **`origin` had a stale embedded token** and hung `git push` on a password
  prompt (no output for 400s). Fix: `git remote set-url origin
  https://${GITHUB_TOKEN}@github.com/...` + `GIT_TERMINAL_PROMPT=0`.


# --- Archived from MEMORY.md (condensed out 2026-11-02) ---

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

## MarketDesk — the always-on chart and the alert (2026-11-06)

- ⭐ **Serving a directory serves everything under it.** The chart URL was
  `http.server` from the repo root, so the owner's **trade journal**
  (`marketdesk/data/journal.db`) and `.openhands/memory/MEMORY.md` both returned
  **200** to anyone with the link, for hours before anyone noticed. Build into
  `chart_live/` and serve that alone; private paths now 404. **A URL is a
  capability** — what it may reach is a security decision, not a formatting one.
- ⭐ **An alert whose line moves is not an alert.** Watching the desk's own 5m
  scenario triggers made the "door" drift **4,073.15 → 4,071.65 → 4,070.67 in 40
  minutes**, so it could fire and un-fire on its own. Watch **printed** levels
  (pivots, stop clusters, round numbers), and read state from the **closed** bar
  — same lesson as the open bar that started all this.
- ⭐ **A rule that does not clear the spread is not a rule.** Simulated the
  owner's own "buy on a 15m close over 4,071": 43 trades, 35% win, **+0.19
  pts/trade before costs vs a 0.25-pt spread** (worse with wider stops). Measure
  the rule he names *before* building the alert for it — and say "no edge".
- ⭐ **4,071 is the middle, not a level.** Printed above: 4,073.15/4,076.38/
  4,081.34; below: 4,068.35/4,067.40 (stop cluster)/4,065.89. A 4-pt stop under
  it lands *on the cluster* — the stop is placed where price goes to hunt it.
- **Telegram cannot deliver here** (`getMe` → Not Found, no chat id;
  `desk telegram` → UNREADABLE) though `available` reads True. Alerts go where
  the owner already looks (chart banner + `alerts.log`), not a promise.
- **Check the JS, do not eyeball it.** Node was available; stubbing `document`
  proved the banner goes green only on a real trigger.
- `chart_server.py` loops gather→rebuild→sleep (`CHART_REFRESH_S`, 120s) into
  `chart_live/`; a failed pass logs and retries. Verified 296 tests, mypy 50
  files, ruff clean. Commits `b1efa58` · `3b2119d` · `21ce3f0`.



# --- Archived from MEMORY.md (2026-11-02, acceptance/scoreboard era) ---

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
- ⭐ **A level's side is where it SITS, not what pivot it was.** The map tagged
  liquidity "above"/"below" by swing-high vs swing-low; once price trades past a
  level the two disagree and the map states the opposite of what it measured (3
  of 4 live levels wrong). Label from the price, after the positional filter. And
  the regression test must be **directional** — a symmetric zig-zag passes on the
  broken code, so the test, not the code, was the bug.
- ⭐ **A number typed into a render rots.** The chart generator carried literal
  level lists and a literal "NFP in ~5 hours" alert, still showing the 4,052-4,083
  shelf hours after price left it. Zones now come from `map.build`, setups from
  `scenarios.build`, the event from the calendar. Two shapes to watch:
  `map.build` reads shared-memory bars, `scenarios.build` reads the agent shape.
- ⭐ **A trade without a stop cannot be graded, so it is not a record.** Added
  `desk journal add|close|grade` (`desk/journal.py`, `4293b00`) — the owner's own
  trades, measured the desk's way: **first touch wins, a bar spanning both takes
  the stop, expectancy in R not points**, an unresolved trade never a win. `add`
  refuses a missing stop. `data/journal.db` is git-ignored (private, not the
  blackboard). mypy caught 4 real `float | None` bugs here — the checker is a test
  the tests do not run.
- ⭐ **The log must judge the PLAN at the moment it is written, not the week
  after.** The desk refuses its own setups below `min_rr` (1.5), but the owner's
  trades entered the journal unexamined — three of four shorts planned to risk 8
  points to make 5, and the week still read as a clean 7/7 (+$56, +6.87R). Under
  1R a trade needs a win rate above 50% just to break even, so `weak_reward`
  (planned_r < 1) now marks the line, `journal add` prints the arithmetic and the
  fix, and the report counts them and names the worst — closed **or not**, because
  a weak plan is a fact about the plan, not about the settled record. And
  `Trade.money` returns **None** when the size was never recorded, never a guess:
  a report that silently drops half the money reads as if that half did not
  happen. `journal size <id> --size N` fills it in later. (One lot = 100 oz, so
  0.01 lot = $1/point — the owner's size.)

