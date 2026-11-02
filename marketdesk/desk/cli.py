"""CLI — run the desk, inspect memory, list the roster.

  python -m desk.cli run      [--symbol XAUUSD] [--lang ar]
  python -m desk.cli gather   # only pull data into the shared memory
  python -m desk.cli memory   # show what the shared memory holds
  python -m desk.cli agents   # list the roster
"""

from __future__ import annotations

import argparse
import json
import sys

from .agents import ROSTER
from .config import Settings
from .orchestrator import Orchestrator
from .report import render


def _settings(args) -> Settings:
    s = Settings()
    if getattr(args, "symbol", None):
        s.symbol = args.symbol
    return s


def cmd_run(args) -> int:
    desk = Orchestrator(_settings(args))
    run = desk.run()
    print(render(run, lang=args.lang))
    if args.json:
        print(json.dumps({"run_id": run.run_id, "verdict": run.verdict,
                          "score": run.score, "recommendation": run.recommendation,
                          "studies": [s.to_dict() for s in run.studies]},
                         ensure_ascii=False, indent=2))
    return 0


def cmd_gather(args) -> int:
    desk = Orchestrator(_settings(args))
    rep = desk.gather()
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


def cmd_memory(args) -> int:
    desk = Orchestrator(_settings(args))
    counts = desk.shared.candle_counts(desk.settings.symbol)
    print(f"symbol {desk.settings.symbol}")
    print("candles:", json.dumps(counts, ensure_ascii=False))
    print("quote:", json.dumps(desk.shared.load_quote(desk.settings.symbol), ensure_ascii=False))
    print("news rows:", len(desk.shared.load_news(limit=1000)))
    print("calendar rows:", len(desk.shared.load_calendar()))
    runs = desk.shared.recent_runs(5)
    print("recent runs:", [(r["run_id"], r["verdict"], round(r["score"], 2)) for r in runs])
    return 0


def cmd_agents(args) -> int:
    for cls in ROSTER:
        print(f"{cls.name:>12} | {cls.kind:<12} | weight {cls.weight:<4} | "
              f"frames {cls.frames or 'all'}")
    return 0


def cmd_hitrate(args) -> int:
    from .scoreboard import Grader
    desk = Orchestrator(_settings(args))
    board = Grader(desk.shared).grade()
    print(board.report())
    return 0


def cmd_watch(args) -> int:
    """One scheduled pass. Alert only on an actionable, not-yet-sent signal."""
    import json
    desk = Orchestrator(_settings(args))
    out = desk.watch_once()
    print(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_serve(args) -> int:
    """Run the watch loop on an interval, for a host with no cron."""
    import json
    import time
    desk = Orchestrator(_settings(args))
    interval = max(60, args.interval * 60)
    print(f"watching {desk.settings.symbol} every {args.interval} min "
          f"(alert threshold {desk.settings.alert_score})", flush=True)
    while True:
        out = desk.watch_once()
        print(json.dumps(out, ensure_ascii=False), flush=True)
        time.sleep(interval)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="desk", description="MarketDesk multi-agent analysis")
    p.add_argument("--symbol", default=None, help="instrument (default XAUUSD)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="full run: gather, dispatch, synthesise")
    r.add_argument("--symbol", default=None)
    r.add_argument("--lang", default="ar", choices=["ar", "en"])
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_run)

    g = sub.add_parser("gather", help="only gather data into shared memory")
    g.add_argument("--symbol", default=None)
    g.set_defaults(func=cmd_gather)

    m = sub.add_parser("memory", help="show shared memory contents")
    m.add_argument("--symbol", default=None)
    m.set_defaults(func=cmd_memory)

    a = sub.add_parser("agents", help="list the agent roster")
    a.set_defaults(func=cmd_agents)

    h = sub.add_parser("hit-rate", help="grade past runs against what price did")
    h.add_argument("--symbol", default=None)
    h.set_defaults(func=cmd_hitrate)

    w = sub.add_parser("watch", help="one scheduled pass; alert only if actionable")
    w.add_argument("--symbol", default=None)
    w.set_defaults(func=cmd_watch)

    sv = sub.add_parser("serve", help="run the watch loop on an interval")
    sv.add_argument("--symbol", default=None)
    sv.add_argument("--interval", type=int, default=15, help="minutes between passes")
    sv.set_defaults(func=cmd_serve)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
