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


def cmd_crossmarket(args) -> int:
    """Does the trend signal generalise across markets, or was it one drift?"""
    from pathlib import Path

    from .crossmarket import run_crossmarket

    print(run_crossmarket(Path(args.dir), markets=args.markets))
    return 0


def cmd_longdata(args) -> int:
    """Download long daily history for several markets into the desk's CSV shape."""
    from pathlib import Path

    from .longdata import MARKETS, fetch, write_csv

    outdir = Path(args.out)
    rng = args.range
    print(f"fetching {len(MARKETS)} markets, range={rng} -> {outdir}/")
    for name, sym in MARKETS.items():
        try:
            bars = fetch(sym, rng)
            if not bars:
                print(f"  {name:7} {sym:10} -> no bars")
                continue
            write_csv(bars, outdir / f"{name}_1d.csv")
            print(f"  {name:7} {sym:10} -> {len(bars):5} bars  "
                  f"{bars[0]['openTime'][:10]} .. {bars[-1]['openTime'][:10]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:7} {sym:10} -> FAILED {exc}")
    return 0


def cmd_levels(args) -> int:
    """One pass: alert once for each watched level the price has crossed.

    Run from a schedule. A level fires once (the ledger keys on level+side), so
    a 15-minute cron does not repeat itself while the price sits past a level.
    """
    from . import sources
    from .levels import DEFAULT_LEVELS, fingerprint, message, triggered
    from .notify import Notifier, SignalLedger

    s = _settings(args)
    symbol = args.symbol or "XAUUSD"
    live = float(sources.fetch_quote(symbol)["mid"])
    hits = triggered(live, DEFAULT_LEVELS)
    ledger = SignalLedger(s.signal_ledger)
    n = Notifier(s)
    sent = []
    for lv in hits:
        fp = fingerprint(lv)
        if ledger.seen(fp):
            continue
        if n.send(message(lv, live)):
            ledger.record(fp, f"{lv.price:g}")
            sent.append(lv.label)
    print(f"live {live:,.2f} · crossed {len(hits)} of {len(DEFAULT_LEVELS)} "
          f"levels · alerted {len(sent)}")
    for lv in hits:
        print(f"  {'▲' if lv.side == 'above' else '▼'} {lv.price:,.2f}  {lv.label}")
    return 0


def cmd_plan(args) -> int:
    """The live two-sided plan: where to act and where to get out, with evidence.

    Network by default (a plan without a live quote is stale), but `--offline`
    reads the shipped CSV so the command still works in a sandbox.
    """
    from pathlib import Path

    from .plan import build_plan, format_plan

    symbol = args.symbol or "XAUUSD"
    if args.offline:
        from .backtest import load_daily
        bars = load_daily(Path(args.csv))
        # No live feed: the reference price is the last CLOSED close, never the
        # still-forming bar's moving close.
        closed = [b for b in bars if not b.get("isOpen")]
        live = closed[-1]["close"]
    else:
        from . import sources
        bars = sources.fetch_candles(symbol, "1d", limit=1000)
        live = float(sources.fetch_quote(symbol)["mid"])
    print(format_plan(build_plan(bars, live), symbol=symbol))
    return 0


def cmd_scan(args) -> int:
    """Ask the prior question: is there any edge in the data at all?

    Tests five standard signal families with no selection and no tuning, prints
    the constant-direction controls beside them, and splits the sample so a
    signal that only lives in one half is visible. A command, not an argument.
    """
    from pathlib import Path

    from .scan import run_scan

    print(run_scan(Path(args.csv)))
    return 0


def cmd_backtest(args) -> int:
    """Walk the stored daily bars and grade every plan the desk would have made.

    No network: the bars are read from the CSV the repo already ships, and the
    news / cross-market feeds are neutral by design (there is no history for
    them). The result is a floor on the desk -- the price-only subset on one
    timeframe -- and it is reported as such, never as the whole desk's record.
    """
    from pathlib import Path

    from .backtest import buy_and_hold, format_report, load_daily, run_backtest

    path = Path(args.csv) if args.csv else _default_csv()
    if not path.exists():
        print(f"no bars at {path} — pass --csv")
        return 2
    rep = run_backtest(path, symbol=args.symbol or "XAUUSD", warmup=args.warmup,
                       horizons=tuple(args.horizons))
    hold = buy_and_hold(load_daily(path), args.warmup)
    print(format_report(rep, buy_hold_pct=hold))
    return 0


def _default_csv():
    from pathlib import Path
    # The repo ships the daily bars beside the package; fall back to cwd.
    for cand in (Path(__file__).resolve().parent.parent.parent / "biquote" / "XAUUSD_1d.csv",
                 Path("biquote/XAUUSD_1d.csv")):
        if cand.exists():
            return cand
    return Path("XAUUSD_1d.csv")


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


def cmd_check_alerts(args) -> int:
    """Verify the alert pipeline in one call.

    The desk is silent by design, so an unconfigured or dead notifier looks
    exactly like 'no signal yet'. This separates the three cases: not
    configured, token rejected, and chat unreachable. Secrets are never printed.
    """
    import urllib.error
    import urllib.request

    from .notify import Notifier

    s = _settings(args)
    n = Notifier(s)
    print(f"token set:   {bool(s.telegram_token)}")
    print(f"chat_id set: {bool(s.telegram_chat_id)}")
    if not s.telegram_token:
        print("result: NOT CONFIGURED — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return 2
    try:
        with urllib.request.urlopen(
                f"https://api.telegram.org/bot{s.telegram_token}/getMe", timeout=15) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"getMe: FAILED {e.code} — the token is not valid")
        return 1
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        print(f"getMe: FAILED ({type(e).__name__}) — cannot reach Telegram")
        return 1
    if not d.get("ok"):
        print("getMe: FAILED — token rejected")
        return 1
    print(f"getMe: ok · bot @{(d.get('result') or {}).get('username')}")
    if not s.telegram_chat_id:
        print("result: token is valid, but chat_id is missing")
        return 2
    if args.send:
        ok = n.send("MarketDesk — اختبار التنبيه: الأنبوب يعمل.")
        print(f"test send: {'delivered' if ok else 'FAILED — the bot cannot reach this chat'}")
        return 0 if ok else 1
    print("result: ready (add --send to deliver a test message)")
    return 0


def _menu_text(settings) -> str:
    from .actions import Actions
    return Actions(settings).menu_text()


def cmd_bot(args) -> int:
    """One poll pass: read the button presses since the last pass and answer.

    Run from a schedule, not in a loop. Telegram confirms the updates when the
    offset is sent back at the end of the pass, so a fresh runner with no disk
    still never replays a press.
    """
    import json

    from .bot import TelegramBot

    s = _settings(args)
    bot = TelegramBot(s)
    if not bot.available:
        print(json.dumps({"ok": False, "reason": "TELEGRAM_BOT_TOKEN not set"}))
        return 2
    if args.greet:
        # Publish the menu once, so the buttons exist in the chat.
        bot.send(_menu_text(s), keyboard=True)
    out = bot.poll(offset=args.offset, budget_s=args.budget)
    print(json.dumps(out, ensure_ascii=False))
    # A failed pass must be a red run, not a green one that answered nothing.
    return 0 if out.get("ok") else 1


def cmd_menu(args) -> int:
    """Publish the menu to the chat (once), so the buttons exist."""
    from .bot import TelegramBot

    s = _settings(args)
    bot = TelegramBot(s)
    if not bot.available:
        print("result: NOT CONFIGURED — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return 2
    ok = bot.send(_menu_text(s), keyboard=True)
    print(f"menu published: {ok}")
    return 0 if ok else 1


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

    cm = sub.add_parser("crossmarket",
                        help="test whether the trend signal generalises")
    cm.add_argument("--dir", default="../longdata")
    cm.add_argument("--markets", nargs="*", default=None)
    cm.set_defaults(func=cmd_crossmarket)

    ld = sub.add_parser("longdata", help="download 10y daily history, several markets")
    ld.add_argument("--out", default="../longdata")
    ld.add_argument("--range", default="10y")
    ld.set_defaults(func=cmd_longdata)

    lv = sub.add_parser("levels", help="alert once when price crosses a watched level")
    lv.set_defaults(func=cmd_levels)

    pl = sub.add_parser("plan", help="live two-sided plan with stop and target ladder")
    pl.add_argument("--csv", default="../biquote/XAUUSD_1d.csv")
    pl.add_argument("--offline", action="store_true",
                    help="read the shipped CSV instead of the live feed")
    pl.set_defaults(func=cmd_plan)

    sc = sub.add_parser("scan", help="test standard signals for any edge at all")
    sc.add_argument("--csv", default="../biquote/XAUUSD_1d.csv",
                    help="daily bar CSV to scan")
    sc.set_defaults(func=cmd_scan)

    bt = sub.add_parser("backtest", help="grade the method on stored daily bars")
    bt.add_argument("--symbol", default=None)
    bt.add_argument("--csv", default=None, help="daily bars CSV (default: the repo's)")
    bt.add_argument("--warmup", type=int, default=120,
                    help="bars the agents must see before deciding")
    bt.add_argument("--horizons", type=int, nargs="+", default=[5, 10, 20],
                    help="grading windows in bars; the longest also caps a plan")
    bt.set_defaults(func=cmd_backtest)

    w = sub.add_parser("watch", help="one scheduled pass; alert only if actionable")
    w.add_argument("--symbol", default=None)
    w.set_defaults(func=cmd_watch)

    sv = sub.add_parser("serve", help="run the watch loop on an interval")
    sv.add_argument("--symbol", default=None)
    sv.add_argument("--interval", type=int, default=15, help="minutes between passes")
    sv.set_defaults(func=cmd_serve)

    ca = sub.add_parser("check-alerts", help="verify the alert pipeline end to end")
    ca.add_argument("--symbol", default=None)
    ca.add_argument("--send", action="store_true",
                    help="also deliver a test message to the chat")
    ca.set_defaults(func=cmd_check_alerts)

    b = sub.add_parser("bot", help="one poll pass: answer button presses")
    b.add_argument("--symbol", default=None)
    b.add_argument("--offset", type=int, default=None,
                   help="update_id to start from (default: Telegram's cursor)")
    b.add_argument("--budget", type=float, default=120.0,
                   help="seconds to spend answering before deferring the rest")
    b.add_argument("--greet", action="store_true",
                   help="also publish the menu before polling")
    b.set_defaults(func=cmd_bot)

    mn = sub.add_parser("menu", help="publish the button menu to the chat")
    mn.add_argument("--symbol", default=None)
    mn.set_defaults(func=cmd_menu)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
