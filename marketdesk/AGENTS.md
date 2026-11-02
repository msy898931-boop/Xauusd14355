# AGENTS.md — instructions for any agent working in marketdesk/

## What this is
A multi-agent market & news analysis desk. One central manager (the
orchestrator) gathers data, wakes 8 specialist agents, collects their studies
from a shared memory, and issues the final recommendation. Pure Python 3.13
stdlib — no runtime dependencies.

## Commands
```bash
python3 -m desk.cli run --lang ar   # full run (the thing that matters)
python3 -m desk.cli run --json      # + machine-readable payload
python3 -m desk.cli gather          # only pull data into shared memory
python3 -m desk.cli memory          # inspect the shared memory
python3 -m desk.cli agents          # list the roster
python3 -m desk.cli bot             # answer the chat's button presses
python3 -m desk.cli menu            # publish the button menu to the chat
python3 -m pytest tests -q          # 67 tests, keyless and offline
```

## Rules that must not be broken
1. **Every number carries its provenance.** Levels are tagged `pivot` (printed
   by the market), `fib` (a measured leg) or `projection` (one stop beyond).
   Never print a bare level.
2. **A metric that cannot be computed is `None`, never `0`.** `indicators.py`
   returns `None` when the sample is too short; the report shows `—`.
3. **The risk gate is fail-closed.** Closed market / stale quote / imminent
   high-impact event → no live entry. Never soften this to "probably fine".
   **The news agent must run BEFORE the rest**, because the gate reads the news
   agent's `event_risk_24h` from the shared board. Run them together and the
   gate reads an empty row and passes — a race that silently disarms the gate.
4. **The model narrates, it never decides.** Compute first, then phrase. The
   narration prompt forbids inventing numbers.
5. **A source failure is empty, not an all-clear.** `SourceError` is caught and
   recorded in `errors`; it is never replaced with a made-up value.
6. **The scoreboard must stay honest.** Grade a horizon only once a candle exists
   past it; never score a call against the current price. Exclude flat bias and
   the deadband. A hit-rate printed over a handful of runs is a running tally —
   say so, never dress it up as a validated edge.
7. **The model labels news; it does not silently replace the baseline.** The
   keyword map is computed first; the model's labels are compared against it and
   disagreements are recorded in the payload. A model failure leaves the
   computed baseline standing.
8. **Alert once per signal.** The watcher must not re-send a signal it already
   sent — a notifier that cries wolf is worse than none. With no credentials the
   desk stays silent rather than half-alerting.
9. **A plan must pay for its stop.** Enforce `min_rr` by walking the levels
   outward, not by taking the nearest three and rejecting them — that refuses
   every setup. If nothing real clears the bar, state no plan; a projection is a
   last resort, used only when there is no structure on that side at all.
10. **Never retry a spent quota, and never hide a fallback.** A `429`/`402`/`403`
    cools that *model* (honouring the provider's retry hint) and rotates to the
    next in the chain — retrying the same model only made the run slow. When the
    whole chain is spent the report says the prose is deterministic; a computed
    line must never read as if the model wrote it.
11. **Groq meters a separate daily quota per model.** That is why `config.models`
    is a chain, not a single name. A single model silently degraded the whole desk
    to computed prose once its quota ran out.
12. **Read closed bars only; never price off a partial bar.** The newest bar is
    still forming, so an indicator read off it is not a fact (7 RSI points on the
    daily, measured). The live price is the broker quote.
13. **One definition of neutral.** `NEUTRAL_BAND` is shared by every agent's
    stance and the orchestrator's verdict. Duplicating it is how they drift.
14. **Both news labellers get the same text.** Comparing a title-only model call
    against a title+description keyword map measures the inputs, not the judgement.
15. **A budget you cannot enforce is a claim.** Use `cf.wait`; report overruns as
    timed-out. `as_completed(timeout=None)` blocks on the first slow agent.
16. **Measure a relationship, do not award one.** The correlation agent computes
    Pearson correlation of returns and scales by it; a fixed score for "opposite
    directions" fired on noise.
17. **Grade the plan, not just the direction.** A short that reaches its target
    only after first taking out the stop is a loss. `_walk_plan` walks the bars
    and takes the *first* touch; a bar spanning both counts as the stop. The
    scoreboard reports expectancy in R, because a high win rate at <1R loses.
18. **A locked database is retried, not surfaced.** All shared-memory writes go
    through `_do_write` → `_write` (busy_timeout 30s + bounded retry).
19. **mypy must stay clean** (`python3 -m mypy desk --ignore-missing-imports`).
    It caught a real shadowing bug in `correlation.py` (`summary` used as both a
    dict and a string) that no test covered.

## Environment quirks
- **Groq sits behind Cloudflare and rejects urllib's default User-Agent with
  403 (error 1010).** The browser-like UA in `llm.py` is required, not cosmetic.
- **biquote's `volume` is always 0** (broker CFD) — the activity field is
  `tickVolume`.
- **biquote candle depth is a retention ceiling, not `limit`.** `from`/`to`
  trim, they do not page deeper. Don't try to fetch more than it keeps.
- **biquote's licence forbids re-exposing OHLC through a public API.** Local
  analysis only; never wire the candles into a public endpoint.

20. **A button on a serverless desk is polled, not pushed.** A webhook needs a
    long-lived HTTPS endpoint and this repository has none, so the chat is polled
    on a schedule. The cursor is confirmed to Telegram (`getUpdates` offset) and
    never kept locally: a runner has no disk, so a local pointer would be lost
    and every press would be replayed.
21. **Confirm an update only once it has been answered.** Advancing the cursor
    past a press the pass never reached tells Telegram to forget it — the press
    is then lost with no reply and no retry. The budget stops the cursor *at*
    the first unanswered press.
22. **Escape for HTML at the send boundary, exactly once.** A model summary can
    contain `&` or `<`, and Telegram rejects the whole message for an unknown
    entity. `analysis`/`report`/`hitrate` return plain text and are escaped as a
    block; `recommend`/`market` build their own markup and escape only the
    dynamic fields. Escaping twice prints `&amp;amp;`.
23. **Only the configured chat may press a button.** An unauthenticated callback
    spends a model quota; a stranger who finds the bot must not be able to drain
    it. Every other chat gets "غير مسموح" and nothing else.
24. **A stored run's `payload` is a JSON string.** `save_run` serialises it and
    only `load_studies` decodes its own column, so a caller reading the run row
    must decode it too (`_payload`). Reading it as a dict crashed the button on
    its first live press.

## Adding an agent
Subclass `Agent` in `desk/agents/`, set `name`/`kind`/`weight`/`frames`,
implement `study(run_id) -> Study`, and add the class to `ROSTER`. It gets a
private memory and a seat on the blackboard automatically.
