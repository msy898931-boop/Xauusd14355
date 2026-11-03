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

