# XAUUSD — market reading and recommendation desk

Two pieces, both pure stdlib (no pandas, no requests):

| Directory | What it is |
|---|---|
| `marketdesk/` | The multi-agent desk. Eight specialist agents, each with private memory plus a shared store, compute their own studies and publish a signed score; the orchestrator weights them into a recommendation and a risk gate. Runs keyless — every number is computed, the model only phrases it. |
| `biquote/` | The earlier single-file scripts that pull every timeframe biquote serves and write a report. Kept as the reference puller. |

## Start here

```bash
cd marketdesk
python3 -m desk.cli run        # one reading + recommendation
python3 -m desk.cli hit-rate   # grade past calls against what price did
python3 -m desk.cli serve --interval 15   # watch loop
python3 -m pytest tests -q
python3 -m mypy desk --ignore-missing-imports
```

`marketdesk/README.md` explains the design; `marketdesk/AGENTS.md` lists the
rules the desk holds itself to, each one a defect that was measured first.

## Notes

* The symbol is XAUUSD by default (`DESK_SYMBOL` to change it).
* `data/` is ignored: the SQLite store, agent memories and the serve log are
  runtime state, not source.
* Secrets are read from the environment only (`GROQ_API_KEY`,
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) and are never written to disk.
