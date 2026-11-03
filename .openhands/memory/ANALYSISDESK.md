# AnalysisDesk (project 2)

_Archived from `MEMORY.md` on 2026-11-03 (it had grown to ~68 KB across three projects)._
_This is the full detail for AnalysisDesk (project 2); `MEMORY.md` keeps only the durable index._

AI-driven market-analysis desk at `/workspace/project` — **original work, not a
clone** of tradliai.com (the user was refused on cloning/pentesting that site).
Full detail → `2026-10-29.md`. The non-obvious bits:

- **⚠ Never run `ruff format` in this repo.** CI runs `ruff check` only; the code
  is hand-compressed (many dict entries per line) and `ruff format` rewrites ~14
  files, exploding the diff (test_desk.py 1214 vs ~700 real lines). Append new
  code in the file's own style. Recovery: `git checkout -- <f>` then re-apply
  logic by hand.
- **Features added (branch `feature/backtest-store-alerts-news`, commit
  `e12b753`, 1 ahead of main, unpushed):** `app/backtest.py` (walk-forward, stop
  checked before target; EURUSD 1h expectancy ≈ −0.08R — honest negative),
  `app/store.py` (SQLite, `DESK_DATA_DIR`, `durable:false` on ephemeral host),
  `app/alerts.py` + `scripts/run_alerts.py` (crossing, Telegram off unless
  `ALERTS_ENABLED`), `app/news.py` (keyless RSS via **defusedxml**, titles only).
  Endpoints `/api/backtest`, GET/POST/DELETE `/api/alerts`, `/api/news`.

- **One keyless source covers all asset classes**: Yahoo
  `query1.finance.yahoo.com/v8/finance/chart/{symbol}` (forex `EURUSD=X`, crypto
  `BTC-USD`, commodities `GC=F`/`CL=F`, indices `^GSPC`, stocks `AAPL`).
- **pandas 3 is strict**: `pd.NA` + `.astype(float)` raises. Use `np.nan`.
- **Groq `gpt-oss-120b` needs `max_tokens` ≈2500 *and* `reasoning_effort:"low"`**,
  or `content` returns empty (budget eaten by hidden reasoning). Free tier has a
  daily token cap → keep a **fallback model chain**; not all models accept
  `reasoning_effort` (400 → retry without it).
- **An LLM will invert a factual relation.** Pre-digest comparisons into tokens
  (`price_vs_ema20=ABOVE`) and add a `_verify()` guard that rejects invented
  numbers / profit language / inverted relations, falling back to computed prose.
- **Verifying a JS-rendered page via `browser_get_content` is misleading** — it
  re-fetches the page. Trust the interactive-element **count** and server logs
  (`/api/candles` is only called from inside `paint()`), or run the page's JS in
  a Node shim with a fake DOM.
- **A number must carry its provenance — including which INSTRUMENT it is.**
  `GC=F` is COMEX **futures**, not spot: user saw spot 4021 vs desk 4033.6 and was
  right; the label "الذهب" was the bug, not the value. The gap is the basis.
  Fix: `Series` carries `short_name`/`instrument_type`; API returns an
  `instrument{kind,name,exchange}` block + a separate `spot` quote (Swissquote
  first, gold-api fallback) + basis %, shown side by side. If both spot sources
  fail, say "تعذّر الفحص" — never substitute silently. Tests still passed while
  the label was wrong, so this class of bug needs a *provenance* assertion, not
  a value assertion.
- **No keyless XAU/USD candle feed exists.** Yahoo has no spot-gold symbol;
  LBMA/FRED/Dukascopy/Binance/stooq are all walled or blocked. **Gold-backed
  tokens are the proxy**: **Kraken** `public/OHLC?pair=PAXGUSD&interval=<min>`
  (native 5m/15m/1h/1d/1wk, ~7y history) primary, **Coinbase** `products/PAXG-USD/candles`
  fallback (no weekly → fold daily to ISO weeks). **The premium is ~0.2%, not
  ~0.05%** — measured 0.17–0.24% over three live reads. Small enough to look
  right, big enough to move every level.
- **⚠ The premium is not a labelling bug, it is a level bug (PR #14).** Showing
  PAXG's price *and* the real spot side by side was not enough: the big number
  and every level under it (entry, stop, target, support, resistance) were still
  on the token's scale. A stop read off the card sat at a price the market never
  had. Fix: `market.anchor_series_to_spot()` rescales the candles onto the live
  quote by one factor *before* `indicators.snapshot()` — so the card and the
  chart are on the broker's scale while every ratio (RSI, ATR%, bb_position) is
  unchanged, because the factor cancels. Proven live: gap +6.75 → 0.00, RSI
  19.56 → 19.56. The shift is disclosed in the `anchor` field and on the card;
  past `MAX_ANCHOR_PCT` (1.5%) the proxy is treated as **diverged** — *not*
  rescaled, and a bilingual warning says the levels are the proxy's. Anchoring
  must never be silent: an undeclared rescale is the same hidden substitution
  the desk refuses everywhere.
- **A fixed footer label lies too.** "computed from real market data via Yahoo
  Finance" printed on *every* symbol, including spot gold, which comes from
  Kraken. The API now carries `instrument.provider` (Yahoo's `exchangeName` is
  an internal code like `CCY`/`CCC`, not a name) and the footer names it.
- **⚠ Kraken and Coinbase use DIFFERENT OHLC column orders** — Kraken
  `[t,open,high,low,close,...]`, Coinbase `[t,low,high,open,close,...]`. Swapping
  them silently inverts every candle into a plausible-looking lie. Verify
  empirically (`low<=open,close<=high`) and pin both in tests.

## AnalysisDesk — a SECOND, separate project (not GoldBot)
`msy898931-boop/analysisdesk`, local clone at **`/workspace/project`**, deployed at
**https://analysisdesk.onrender.com** (Render free, Docker, blueprint `render.yaml`,
`autoDeploy: true`). FastAPI + a static `web/index.html`; analysis over Yahoo Finance
(OHLC) with a **spot-gold cross-check** (Kraken PAXG/USD primary, Coinbase fallback,
Swissquote mid as the independent second opinion) — the user trades **spot XAU/USD,
not futures**. AI narrative is a *narrator* only: `ai._verify` rejects prose that
invents a figure, promises profit, or inverts a relation, falling back to a computed
sentence. Key `GROQ_API_KEY` set in the Render dashboard (`sync: false`), never git.

**Spot chain is now `biquote → Swissquote → gold-api` (PR #16, `e1c87af`, v1.2.0).**
biquote (keyless MT5 broker feed, `biquote.io`) is first *because it states the
market's own verdict*: `marketState`/`stale`/`quoteAgeSeconds` on every tick, so a
closed market reads as closed instead of a Friday price passing for Sunday's.
`SpotOut` carries those fields; the card says "market closed — last price".
⚠ Traps: `last`/`volume` are **always 0** (broker CFD, no tape) — price is `mid`;
`/api/latest` needs **repeated** `symbols=`, never commas. ⚠ Licence: biquote
forbids re-exposing the raw feed via a public API; our one-quote `spot` field is the
"inside your product" case, an OHLC pass-through is not. Terms + fallback in
`docs/SOURCES.md`, permission email draft in `docs/EMAIL-biquote-permission.md`.
Gate after a version bump: `pip install -e .` (the metadata test reads it).

**The alert loop has a bot + a clock (PR #18, `43335e2`, v1.3.0).**
`app/bot.py` (Telegram `getUpdates` long poll, off by default via `BOT_ENABLED`)
arms alerts from the chat: `/alert /list /remove /status /help`. **Fail closed** —
the allowlist is `TELEGRAM_ALLOWED_CHATS` → fallback `TELEGRAM_CHAT_ID`, empty ⇒
answers nobody (a *token is a credential, not a permission*). `app/scheduler.py`
calls `alerts.run_once()` every `SCHEDULER_PERIOD_S`. Both start from the **app
lifespan**, each gated on its own env var; the default deploy starts **no**
background work. `/api/status` has typed `bot` + `scheduler` blocks.

⚠️ **THE DECISIVE FACT: Render Free spins down after 15 min with no *inbound*
traffic; an outbound long poll is NOT inbound.** So the in-process clock **freezes
with the host** — it is **best-effort, not 24/7**, and its `state()["note"]` says
so in the API. The supported 24/7 route is a **Render Cron Job on
`scripts/run_alerts.py`**. Never describe the in-process timer as a real schedule.
- ⚠️ **ruff S106/S107 fire on a test-helper param named `token`** — rename it
  (`with_token=`), don't noqa.
- ⚠️ **A test that pins the version literal is a second home for it.** On a bump,
  grep tests for the old string. (This bit on 1.2.0→1.3.0.)
- The biquote permission email was **NOT sent, by owner decision** (proceed on the
  terms as written). `docs/SOURCES.md` records the *decision*, not a request.

**⚠️ Reading `/api/backtest` honestly (measured 2026-11-02, detail in the daily log).**
XAUUSD **1d** reports expR **+0.56 / totalR +387 / hit 50%** — it is **beta, not
skill**: gold rose **+54%** over the sample and the engine was **LONG 439/687**.
The raw backtest is also **overlapping** (a signal every bar) → inflates n and
understates risk. **Always: (a) non-overlap, (b) compare to buy&hold, (c) run a
random-entry control.** Against random entries the engine has a modest daily edge
(BTC/GC=F/ETH beat ~97% of runs) but **none on the hourly** (XAUUSD 1h ≈ 1.3rd
percentile). **No filter generalised** — daily-trend alignment helped gold but lost
on 4/6 other symbols; conf/|score|/MACD/stack/ATR-band/regime-ER all flat-to-harmful.
**The one improvement that generalised: a trailing exit (~2×ATR)** — daily pooled
expR +0.156 → **+0.368**, winners 5/8 → **7/8**; hourly +0.004 → +0.065. One sample
⇒ justifies *experimenting*, not declaring it solved. `docs/how-it-works.html` is
the self-contained bilingual (RTL) pipeline diagram + these results.

### ⭐ The defect this project is made of — two things that must agree, kept apart
Four separate bugs, **one shape**. All invisible in the UI, all found by reading the
**raw API response** (the page rounds, falls back, and looks healthy):

| two things that must agree | how they drifted | symptom |
|---|---|---|
| `_verify` regex vs thousands separator | `\d+\.\d+` split `3,982.69` into `982.69` | correct prose rejected as invented |
| `_verify` allow-list vs `_fact_sheet` | hand-written list omitted `bb_position`, which the sheet *shows* the model | correct quote rejected |
| `_clean` / `_n` / `_r` / 36 `:.4f` literals | **four** copies of one rounding rule | `3992.80005`, `1.1186` vs `1.11857`, `3997.5179` |

**Fix and rule:** `indicators.price_decimals()` is the single source of truth
(5 decimals <10 for FX pipettes, 4 in [10,100), 3 in [100,1000), 2 at 1000+ for
gold/BTC); the guard's allow-list is **derived from the sheet**. Carry forward:
**when two things must agree, generate one from the other — never keep both by
hand; and when you find one copy of a rule, grep for its siblings** (there were
four here). Verification corollary: **a fix verified only on the case that
motivated it is not verified** — check every surface the value reaches.

### Hardening pass (2026-10-30, after a 62/100 self-eval; Security was 6/20)
- **XSS closed at two layers**: `esc()` in the external `web/app.js` (every
  server string), plus `_strip_markup()` in `ai.py`; inline `<script>`/`onclick`
  removed so CSP can be `script-src 'self'`. Full header set on every response.
- **Abuse guard**: per-IP in-memory window (`RATE_LIMIT_PER_MIN`, 60) + optional
  `DESK_API_KEY`. ⚠️ **The old page exemption was a bypass.** It trusted
  `Sec-Fetch-Site: same-origin` — a browser-set, page-unforgeable header — but a
  *script* sets it freely, so `curl -H 'Sec-Fetch-Site: same-origin'` reached
  every endpoint with no key. Removed (PR #13): when the key is set it is
  required from **every** caller, `hmac.compare_digest`. **A public page cannot
  hold a secret, so no honest page exemption exists** — the gate fails closed.
  Production leaves `DESK_API_KEY` unset, so the page is unaffected. The guard
  runs **after** param validation (a 400 is free). Per-process, so a restart
  clears it — stated in code, not hidden.
- **Two real bugs surfaced while linting, not while testing**: `signals.evaluate`
  divided by `price` before its `fatal` check (`ZeroDivisionError` at price 0;
  `price <= 0` is now fatal → NEUTRAL); the market `_cache` was unbounded (now
  256, oldest-evicted).
- **`# noqa: S310` (ruff) and `# nosec B310` (bandit) are different markers** —
  both are needed on the same line, and bandit also flags `Request(...)`.
- **`pip-audit`** here must be `-r requirements.txt` (no `pyproject.toml`, so
  `pip-audit .` errors). Sandbox Docker needs `sudo rm -f /var/run/docker.pid`
  (stale pid) then `chmod 666 /var/run/docker.sock`.
  ⚠ **Superseded 2026-10-30: `pyproject.toml` now EXISTS** (single source of
  truth: deps + `[tool.ruff]`/`[tool.pytest.ini_options]`/coverage/mypy);
  `ruff.toml` and `pytest.ini` were deleted. `pip-audit .` still needs the
  venv/project install to be meaningful; CI uses `pip-audit -r requirements.txt`.
  A test asserts `requirements.txt` == `[project.dependencies]` so they can't drift.
- Now: **134 tests** (128 offline / 6 `network`) + **8 UI tests** under
  `node --test`; coverage **87%**; ruff/mypy/bandit/pip-audit clean; image runs
  as `desk` (uid 10001) with **`--workers 1`** pinned; XAUUSD live in-container.
  Full detail → daily log.
- **Third pass (2026-10-30) — "close the remaining gaps", the user asked for it
  professionally.** (a) **`market.py` 49% → 99%**: canned-payload tests for every
  venue (Yahoo/Kraken/Coinbase/Swissquote/gold-api) and every failure (non-200,
  network error, unreadable body, source `error` field, empty result, too little
  history, null bar), plus Coinbase pagination + its end-of-history stop, cache
  eviction, the spot fallback chain. (b) **The card got tests**: pure helpers
  (`esc`,`fmt`,`priceDecimals`,`basisPct`) moved to **`web/lib.js`** (an ES module
  the page and node share) so escaping/precision have ONE copy; `app.js` imports
  it; `index.html` loads `app.js` as `type="module"`; 8 node tests + a `ui` CI job.
  (c) **Chart axes** (price labels right, dates bottom — a chart with no numbers
  is decoration), canvas `role=img`+`sr-only` description, labelled selects,
  `role=status` live region, `:focus-visible`, `prefers-reduced-motion`, 560px
  breakpoint. (d) **The rate limit's scope is pinned**: Dockerfile runs
  `--workers 1` on purpose (counter is in-process → N workers = N× the limit);
  two tests hold it. (e) **`ARCHITECTURE.md`** written. Commits `13708cb`,
  `928484c`; CI green on 3.11+3.12+ui.
- ⚠ **`actions/setup-node@v4` warns on Node 24 runners** (Node 20 deprecated) —
  bumped to `@v6`. Same class as the earlier `setup-python` bump.
- ⚠ **ruff `PLW2901`** (loop var rebound) and **`S603`** (subprocess in tests)
  fired on the new tests; fixed by a new var name and adding `S603` to the
  `tests/*` per-file-ignores.

- **Fourth pass (2026-10-30, "كمل كمل") — the ~85/100 gaps closed. Now 160 tests,
  coverage 95%** (main.py 74→98, ai.py 77→89). Commits `e42ea5d`, `331bea1`,
  `fed0562`; **all four CI jobs green** (ui, test 3.11, test 3.12, docker).
  - **main.py 15 integration tests** (TestClient): provenance block, futures label
    + spot attached, spot failure reported not hidden, unknown interval/lang→400,
    bad param not spending the rate budget, upstream failure→502, candles capped
    200, CSP header, 429+Retry-After, API-key gate admits page / refuses direct call.
  - **ai.py chain walked model-by-model** (first answers, 2nd after a 429, 400
    retries without reasoning_effort, every model contradicting facts → computed
    sentence, network error, empty completion, markup stripped, no call without a
    key). Found+fixed a **real defect**: the 401/403 path returned `config:` but
    the loop kept going, burning the rest of the budget — now it `break`s.
  - **`requirements.lock`** (28 pkgs, all `==`+sha256, `--require-hashes`); a
    `docker` CI job builds + waits for `/api/health` + asserts non-root;
    `.github/dependabot.yml` (pip/actions/docker). `scripts/check_lock.py` fails on
    range/pin drift, a missing dep, an unhashed entry, or a **wrong compile target**.
  - ⭐ **THE TRAP (bit me on the first push):** the lock was compiled for the
    image's 3.12 → numpy 2.5.4 (>=3.12 only) → the **3.11** CI job died on
    `pip-audit -r requirements.lock` while the image built fine. **A lock must be
    compiled for the LOWEST supported Python** (`requires-python`, 3.11), not the
    image's; `uv pip compile --python-version 3.11` gives multi-version hashes so
    one lock serves 3.11+3.12. CI now also runs
    `pip install --dry-run --require-hashes -r requirements.lock` per matrix
    interpreter — the resolve that catches this before the audit does.
  - ⚠ **`ruff format` churns ~1200 lines** (line-length rewrap) and CI does NOT
    check format → revert it; keep `ruff check --fix` only, so the diff shows the
    real change. ⚠ `pip install -r requirements.lock` needs no `--require-hashes`
    (pip enables hash mode from the file). ⚠ dev tools are modules, not scripts
    on PATH in a fresh sandbox: use `python3 -m ruff/mypy/bandit/pip_audit`.

- **Fifth pass (2026-10-30, "بلش بالرصد") — the observability gap closed. 170
  tests, coverage 95%.** Commit `089e3ce`; CI 4/4 green; **deploy-watch fired
  automatically on the push** (run 41043507637) and proved the running revision
  was the pushed one.
  - ⭐ **THE FINDING: the deploy watcher was in the repo and doing NOTHING.** It
    had a `*/30` cron and **ZERO scheduled runs** — GitHub's `schedule` is
    best-effort and heavily throttled (sibling repo `my-awesome-app`'s `*/5`
    cron fires only every 4–6h). **A watchdog that never runs is silent in
    exactly the way a healthy service is** — the same "unknown reported as a
    known" shape as every other bug here. Fix: a **`push` trigger** is the
    reliable driver (the probe reads `/robots.txt` and never wakes the service,
    so running it often is free); the cron stays as a backstop.
  - **`/api/status`** (live on prod): uptime, `served`, deployed `commit`
    (`RENDER_GIT_COMMIT`), model+key, `cache` size, `tracked_clients`. All
    process-local → `uptime_s` is what separates "recently restarted" from
    "nothing happened". `market.cache_stats()` added.
  - **`check_deploy.py --gap-threshold`** — the watchdog watches itself; a
    newest reading older than the threshold (12h in CI) is reported, an **empty
    log is deliberately NOT an alarm** (fresh install).
  - **`--expect-commit <sha>`** on push: polls `/api/status` until the running
    revision equals the pushed one (Render Docker build lags ~1min, so
    `--wait-seconds 600`). **A green run that only says "something is serving"
    is a false all-clear** — the same rule the whole desk is built on.
  - ⚠ **Test hygiene:** a `body["cache"] == {"entries": 0, ...}` assertion broke
    when earlier tests filled the process-wide cache → assert the **shape and
    bound**, not a count. And patching `cd.fetch = lambda...` leaked between
    tests → use `monkeypatch.setattr`.
  - ⚠ **`/api/status` wakes a sleeping service** (unlike `/robots.txt`); it is
    read only on push, never on the zero-wake schedule.

- **Sixth pass (2026-10-30, "كمّل عتتبع الأخطاء") — error tracking, in-repo. 178
  tests, coverage 95%.** Commit `edbece0`; CI 4/4 green; deploy-watch proved
  revision + **"no unhandled errors since start"** live.
  - ⭐ **Before: an unexpected exception produced an empty 500 and left no trace
    anywhere** — no log, no counter. Same "failure reported as nothing" shape.
  - **Decision: NO Sentry/third-party tracker.** Shipping stack traces + request
    context to an external account is the owner's call, not mine. Instead:
    `app/errors.py` writes **one structured JSON line per unhandled exception to
    stdout** (Render captures stdout → that IS the durable log) + a bounded
    in-memory count by type for `/api/status`.
  - **`X-Request-Id` on every response** (even 404); the generic 500 handler
    returns `{"detail":"internal error","request_id":...}` — **traceback stays in
    the log, never to the caller**.
  - **Redaction** of any configured secret value in message/traceback → `***`;
    message+trace capped; type map capped at 20 + `_other`.
  - **⭐ Key distinction: an upstream outage is NOT our error.** A market source
    down = 502 with reason, excluded from the count — else the count is noise
    and gets ignored. Tested explicitly.
  - **`check_deploy.py --max-errors 0`** on push: a deploy answering `/api/health`
    while throwing on real requests is what a liveness probe cannot see. Proven
    end-to-end (raise → counter 1 → verdict False).
  - ⚠ **Env reset mid-session:** the sandbox terminal lost its venv (no pytest/
    fastapi). Fix: `pip install -r requirements.txt` + dev extras
    (`pytest pytest-cov ruff mypy bandit pip-audit`). Tools are modules:
    `python3 -m ruff/mypy/bandit`.
  - ⚠ When a test patches a module-level function (`cd.fetch = lambda…`), use
    `monkeypatch.setattr` or it leaks into later tests.

- **Seventh pass (2026-10-30, "فتت عالرابط… ماشتغل زر التحليل") — the analyze
  button shipped DEAD and SILENT.** Commit `acb420e`; CI 4/4 green; verified
  live on prod with spot gold.
  - ⭐ **Root cause:** `web/app.js` is a `type="module"` → **strict mode**, and it
    referred to its elements by **bare name** (`status`, `go`, `sym`, `iv`,
    `out`, `title`, `tagline`, `footline`, `disc`) leaning on **window named
    access**. **`status` is a RESERVED window property** (the status-bar string)
    → a bare `status` was a *string* → `status.textContent = t.load` threw
    `TypeError` **BEFORE the try block** → empty panel + **button stuck
    disabled** + **no error shown anywhere**. `sym`/`iv` did not resolve at all.
  - **Fix:** bind every element explicitly, `const $ = id =>
    document.getElementById(id)`. **Never** use bare element names in a module.
  - **Why it shipped:** the node UI tests only exercised **pure helpers in
    lib.js** — *no DOM test existed*. ⭐ **`web/dom.test.mjs`** now runs `app.js`
    for real in a **`node:vm` sandbox with a minimal DOM stub** (no browser, no
    jsdom dep) and asserts a click renders a card + re-enables the button + a
    failed read shows the error. Wired into CI's `ui` job (11 node tests now).
  - **Proven:** reverting the fix fails 3/3; with it 3/3 pass. It also asserts
    every id `app.js` binds exists in `index.html`.
  - ⭐ **Lesson (same as always): a failure reported as nothing at all.** A dead
    button and a healthy button look identical — the bug was invisible precisely
    because it produced no error. The panel being *empty* was the only signal.
  - ⚠ The named-access trap is silent and browser-specific; `title`, `go`,
    `out`, `disc` happen to resolve (not reserved) so only `status` broke — a
    *partial* failure that looked like a total one.

- **Second defect pass (2026-10-30)** — 7 more real defects, all invisible to
  the suite: (1) rate limit bypassable (read the FIRST `X-Forwarded-For` hop,
  which the caller sends; now the LAST, our edge's), (2) the rate map grew
  without bound (now pruned), (3) **spot gold never cached** — the cache sat
  *below* the spot branch, so XAUUSD re-fetched on every read, (4) the
  `deterministic` fallback still named a model it never used, (5) `r.statusCode`
  is undefined on the browser `Response` (it is `.status`), (6) the card's
  `fmt()` rounded differently from the server, (7) an undefined CSS token
  (`--fg`; the real one is `--txt`) and `drawChart` threw on an error body.
  Also added HSTS + Permissions-Policy. **Lesson: a green suite says nothing
  about a path no test exercises — read the request/response path end to end.**

- **Eighth pass (2026-10-29/30, "خليه يعطيني ٦ اهداف بالصفقة") — six-target
  scale-out ladder.** Version **1.1.0**, commit `0009dac`; CI 4/4 green; live on
  prod. 197 python + 15 node tests, coverage 94.7%.
  - ⭐ **The design rule: every target carries its PROVENANCE.** Two kinds, kept
    distinct: **`level`** = a pivot the market actually printed
    (`indicators.swing_ladder`), **`projection`** = one stop-distance beyond the
    last real level. Labelling both "target" would report an assumption as a
    level — the project's core sin. This is the whole feature, not the six numbers.
  - `indicators.swing_ladder(df, lookback=60, wing=3) -> (supports, resistances)`
    from a shared `_pivots()`; `snapshot` now emits `supports`/`resistances`.
    `signals._ladder(...)` builds TP1..TP6 (`TARGET_COUNT=6`); **`take_profit`
    stays TP1** so nothing that read it changed meaning. Neutral read ⇒
    `targets: []` (no plan), never six invented numbers.
  - The ladder reaches the AI fact sheet (`target_1`..`target_6`) **and** the
    guard's allow-list, so a rung on the card is quotable and a **seventh number
    is still rejected as an invention**.
  - ⚠ **Honesty check that looked like a bug:** live gold sat at a 200-bar low
    (0 supports beneath) → **all six targets were projections**. That is the
    honest output, not a failure; EURUSD/BTC did snap real levels. Don't "fix"
    a ladder that finds no levels — the market may genuinely have none there.
  - **Failure proof:** removing the ladder build fails 5 tests (the neutral one
    doesn't, since it is already `[]`).
  - ⚠ **ruff `PLR0913`** (>5 args) fired on `_ladder`; fixed by packing
    `(price, is_level)` into one tuple arg. **`B905`** wants `zip(..., strict=)`.
  - ⚠ Env reset **twice** this session (pip libs vanish) — reinstall line below.

- **Ninth pass (2026-10-29→11-01, "the four audit findings") — three merges, one
  shape: an unknown reported as a known.** `main` = `11e1de9`; CI 4/4 green each;
  all verified live on prod. **271 → 275 python + 17 node tests.**
  - **#1 Groq key dead** (`246f34c`): `/api/analyze` 500'd; the key was fine, the
    **model name** was retired. Prod `model=openai/gpt-oss-120b`.
  - **#2 `Sec-Fetch-Site` bypass** (PR #13, `047b3ac`): the key gate exempted any
    request carrying `Sec-Fetch-Site: same-origin`, "because only the bundled page
    sends it" — **true of a browser, false of a script**, which sets it freely.
    **A header the caller controls is not a credential.** Exemption removed; the
    key (when set) is required from *every* caller. Prod leaves it unset → page
    unaffected.
  - **#3 `X-Forwarded-For` bypass** (PR #14, `5cac740`): `_client_ip` read the
    **LAST** XFF hop, claiming "our edge wrote it". True behind a proxy, **false
    with nothing in front** (locally the last hop is the caller's value) → one
    script minted a fresh bucket per request. Proven live: limit=2, 4 requests
    with distinct forged XFF → **4×200**; no header → 3rd = 429. **Render APPENDS
    the real IP as the last hop, so prod was safe today** — but the code trusted a
    caller-set header by default. Fix: **do NOT trust XFF** unless
    `DESK_TRUST_PROXY=1` (set only when a controlled proxy appends the last hop;
    the blueprint sets it). ⚠ **Second discovery:** uvicorn rewrites
    `scope["client"]` from XFF when the peer is trusted (127.0.0.1) → locally even
    `request.client.host` was forged; the run command now passes
    `--no-proxy-headers` (render.yaml, Dockerfile, Makefile).
  - **#4 `chat_id` was the only credential for the alerts API** (PR #15,
    `11e1de9`). **Proven against prod: `GET /api/alerts?chat_id=123456789` → 200
    with no credential** — anyone guessing a Telegram chat id could read, arm,
    and delete another user's alerts. ⭐ **The shape differs from #2/#3: not a
    header to distrust, but a wrong *model*** — a chat id is an **identifier, not
    a secret** (it leaks through groups and forwarded messages), yet it was doing
    a password's job. The endpoints are **service-to-service** (the bot is the
    only caller; `web/` never touches them), so the credential is a **shared
    service token** `ALERTS_TOKEN` + `x-alerts-token`, `hmac.compare_digest`.
    **Unset token → 503 (closed), never open** — failing open would be the bypass
    under a new name. `chat_id` still scopes the rows. **Why a separate token,
    not `DESK_API_KEY`:** the latter is unset in prod so the public page stays
    open; coupling would force a choice between closing the page and leaving
    alerts open. `/api/status` gains `alerts_configured` (tell "closed" from
    "wrong token"). **No action needed to be safe: the blueprint leaves the token
    unset, so prod is closed** — verified live: unset → 503; set → 401 missing /
    401 wrong / 200 right.
  - ⚠ **CodeQL `py/side-effect-in-assert`** (a real finding, caught on the PR):
    `assert c.delete(...).status_code == 404` — under `python -O` the assert is
    stripped and the call never runs. Bind the response first, then assert. CodeQL
    flags **POST/DELETE** (non-idempotent), not GET. The check-run annotations are
    the only readable detail (`gh api .../check-runs/<id>/annotations`); the
    code-scanning alerts API is **403** for this token.
  - ⚠ **ruff `S106`** false-positives on a kwarg literally named `token=` —
    renamed the test helper's param to `configured`. **`ruff check` is what CI
    enforces, NOT `ruff format --check`** (11 files unformatted on main, by
    design; keep the diff small).

### Environment facts
- **The sandbox recycled 4× this session** — `~/.local` pip installs vanish,
  `/workspace` files survive; `scripts/start.sh` reinstalls + relaunches in one step.
  This is the argument for Render: the sandbox is a workbench, not a host.
- **GitHub App installs do NOT see repos created after install** — the API says
  `push: true` yet Contents API returns "Resource not accessible by integration".
  Fix = owner adds the repo to the app's access. Probe with `PUT contents` first.
- Before any `--force` push, **list the remote's commits** and confirm they are only
  yours. Here the remote held just my own `.access-check` marker, so it was safe.
- Free-tier Groq rate limits are the common cause of `narrative.source=deterministic`
  in production — that is the documented fallback working, not a bug. Check the
  `note` field to tell them apart.
- Render requires account sign-in: **I cannot deploy for the owner** and will not
  authenticate as them. The owner clicks "Create web service"; I verify after.

### Watching the deployment — the /robots.txt trick (zero-cost, non-waking)
`scripts/check_deploy.py` reads sleep state **without waking the service**.
Render answers `/robots.txt` **itself** while a Free service is spun down
(disallow-all), and that reply never reaches the app. FastAPI has no such route,
so **awake → 404 with a JSON `{"detail":...}` body**; anything else → `unknown`,
and **unknown is not a pass**. Probing `/api/health` on a schedule would wake the
service every run and eat the 750-hour allowance — *the check would destroy the
thing it measures* (GoldBot lesson #1 in a new shape). Sleeping is normal and is
**not** alerted; only unknown or awake-but-unhealthy is.
- **Render's API exposes NO instance-hour figure** (no usage/billing/quota
  endpoint — verified). `scripts/hours_audit.py` says so and labels its number a
  **projection**, never a fake precise one. **750 h is per workspace, not per
  service**; a second always-on Free service burns 720 alone. This is the real
  risk if GoldBot and AnalysisDesk share the account.
- `.github/workflows/deploy-watch.yml` runs every 30 min, reuses one issue,
  auto-closes on recovery. Alert path exercised for real (issue #1 opened, then
  closed). Telegram needs `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` as repo secrets.
- GitHub Actions `schedule` is **best-effort** — a notifier, not a guarantee.

- **Pitch published at `/pitch`** (PR #8, main `cc6f492`) — live:
  `https://analysisdesk.onrender.com/pitch`. Bilingual (ar/en), self-contained.
  ⚠ Two rules learned: (1) **the Dockerfile copies only `app/` + `web/`** — a
  repo-root file never ships; put publishables in `web/`. (2) The desk's CSP is
  `script-src 'self'` (no `'unsafe-inline'`, guarded by a test), so the language
  switch is **pure CSS** (`<input type=radio>` + `:checked ~ .wrap .en{display:none}`),
  not JS — an inline script would be silently blocked. GitHub Pages can't be
  enabled by this token (403). Detail → `2026-10-29.md`. Linked from the desk header (PR #9, `6214385`).

## CodeQL `py/partial-ssrf` (this repo's one real finding)
A symbol that becomes a URL path segment **is** a security boundary — guard it
where the URL is built, not only at the API edge (`fetch_series` has other
callers). CodeQL's only modelled sanitizer here is an **inline**
`re.compile(...).match(v)` in the sink's control flow; a helper call or an
aliased `quote` import hides it, and `urllib.parse.quote` alone is **not** a
sanitizer. Also: the `CodeQL` check-run ≠ the `codeql` job — read the check-run
annotations (`gh api .../check-runs/<id>/annotations`) to see the finding. Full
note → `2026-10-29.md`.

## biquote OHLC — the candle pull (2026-11-02)
`GET https://biquote.io/api/{symbol}/ohlc?interval={1m,5m,15m,30m,1h,4h,1d}&limit<=1000`
returns `bars[]` (newest first, `isOpen` on the live bar). **`volume` is always 0**
→ use `tickVolume`. **Depth is a retention ceiling, not `limit`** — `from`/`to`
only trim, never page deeper. Measured max: M1 ~5h · M5 ~4d · M15/M30 ~6d ·
H1 ~11d · H4 ~6w · D1 ~19mo. **Licence: biquote's terms forbid exposing OHLC /
bulk / a pass-through endpoint** (the spot quote is the allowed case) — use it
for a one-off read, never wire it into the desk API; candles stay Kraken/Coinbase.
Detail + the XAUUSD read → `2026-11-02.md`.

