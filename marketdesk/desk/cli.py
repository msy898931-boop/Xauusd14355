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
    text = render(run, lang=args.lang)
    print(text)
    if args.json:
        print(json.dumps({"run_id": run.run_id, "verdict": run.verdict,
                          "score": run.score, "recommendation": run.recommendation,
                          "studies": [s.to_dict() for s in run.studies]},
                         ensure_ascii=False, indent=2))
    if getattr(args, "send", False):
        from .notify import Notifier

        ok = Notifier(_settings(args)).send(text)
        print(f"analysis sent: {ok}")
        return 0 if ok else 1
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


def cmd_gold(args) -> int:
    """The 10-year gold study: when gold looked like this, what happened next?"""
    from pathlib import Path

    from .gold import run_gold_study

    live = None
    if not args.offline:
        try:
            from . import sources
            live = float(sources.fetch_quote(args.symbol or "XAUUSD")["mid"])
        except Exception:  # noqa: BLE001 - fall back to the last close
            live = None
    print(run_gold_study(Path(args.csv), live=live, symbol=args.symbol or "XAUUSD",
                         stop_atr=args.stop_atr, tgt_atr=args.tgt_atr))
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
    """One pass: alert once for each watched level the price has crossed, and
    once for each it is about to cross.

    Run from a schedule. A level fires once (the ledger keys on level+side), so
    a 15-minute cron does not repeat itself while the price sits past a level.
    The proximity warning has its own key, so a level can warn and then fire.

    `--hold` turns the pass into a long watch instead: it re-checks every
    `--every` seconds until `--hold` elapses. That is what makes the alert robust
    against the scheduler rather than dependent on it. GitHub's `schedule` is
    best-effort and, on this repository, has fired a handful of times in a day
    against a 15-minute cron -- so an alert that needs every slot to fire is an
    alert that mostly does not arrive. One long run covers the same hours with
    one job, and the ledger still sends each signal once.
    """
    import time

    from .levels import watch_once

    s = _settings(args)
    symbol = args.symbol or s.symbol
    within = getattr(args, "within", 12.0)
    hold = float(getattr(args, "hold", 0) or 0)
    every = max(10.0, float(getattr(args, "every", 60) or 60))

    def report(out: dict) -> None:
        print(f"live {out['live']:,.2f} · crossed {len(out['crossed'])} of {out['total']} "
              f"levels · alerted {len(out['alerted'])} · near {len(out['near'])} · "
              f"warned {len(out['warned'])}", flush=True)
        for label in out["crossed"]:
            print(f"  ▲ {label}", flush=True)
        for label, dist in out["near"]:
            print(f"  ~ {label}  ({dist:,.1f} away)", flush=True)

    if hold <= 0:
        report(watch_once(s, symbol=symbol, within=within))
        return 0

    started = time.time()
    checks = 0
    sent = 0
    while time.time() - started < hold:
        try:
            out = watch_once(s, symbol=symbol, within=within)
            checks += 1
            sent += len(out["alerted"]) + len(out["warned"])
            report(out)
        except Exception as exc:  # noqa: BLE001 - one bad tick must not end the watch
            print(f"  ! check failed: {exc.__class__.__name__}", flush=True)
        # Sleep in small steps so the job stops near its deadline, not `every`
        # seconds past it.
        deadline = min(time.time() + every, started + hold)
        while time.time() < deadline:
            time.sleep(min(5.0, deadline - time.time()))
    print(f"held {time.time() - started:,.0f}s · {checks} checks · {sent} signals sent",
          flush=True)
    return 0


def cmd_scenarios(args) -> int:
    """Send the conditional scenarios to the chat (or print them).

    Reads what a run already stored — the closed frames and the calendar — and
    computes the levels itself. It starts no agent and calls no model, so it is
    cheap enough to run on a schedule.
    """
    from .actions import Actions

    s = _settings(args)
    text = Actions(s).scenarios()
    if getattr(args, "send", False):
        from .notify import Notifier

        n = Notifier(s)
        ok = n.send(text)
        print(f"scenarios sent: {ok}")
        return 0 if ok else 1
    print(text)
    return 0


def cmd_map(args) -> int:
    """The daily map: buy zones, sell zones, liquidity, trend.

    Reads what a run already stored -- closed frames and a live quote -- and
    computes the zones itself. It starts no agent and calls no model, so it is
    cheap enough to run once a day on a schedule.

    `--once-a-day` guards the schedule so a 15-minute cron sends it once: the
    date is stamped in a file beside the desk state, so a run later the same day
    is a no-op. This is what lets the map go out from the GitHub cron alone,
    with no always-on host to depend on.
    """
    from .actions import Actions

    s = _settings(args)
    if getattr(args, "once_a_day", False):
        import datetime as _dt

        from .daemon import _load_map_date, _save_map_date

        today = _dt.datetime.now(_dt.UTC).date().isoformat()
        if _load_map_date(s) == today:
            print(f"map already sent today ({today})")
            return 0
        text = Actions(s).map()
        from .notify import Notifier

        ok = Notifier(s).send(text)
        print(f"map sent: {ok}")
        # Stamp only on success, so a failed send is retried by the next run.
        if ok:
            _save_map_date(s, today)
        return 0 if ok else 1
    text = Actions(s).map()
    if getattr(args, "send", False):
        from .notify import Notifier

        n = Notifier(s)
        ok = n.send(text)
        print(f"map sent: {ok}")
        return 0 if ok else 1
    print(text)
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


def cmd_bias(args) -> int:
    """Audit the desk's own decision rule for a structural lean.

    Replays the structure/trend/momentum votes over the shipped ten-year file
    and reports the long/short/no-trade split. The desk looked short-biased
    across 69 live runs; this says whether that was the market or the code.
    """
    from .bias import audit_symbol, render

    b = audit_symbol(args.symbol or "XAUUSD")
    print(render(b, args.symbol or "XAUUSD"))
    return 0


def cmd_calibration(args) -> int:
    """Show whether the desk's confidence means what it says.

    Two curves: the ten-year replay of the desk's own rule (always available),
    and the live graded runs when there are any.
    """
    from . import calibration as cal

    symbol = args.symbol or "XAUUSD"
    print(cal.render(cal.from_symbol(symbol)))
    from .memory import SharedMemory

    rows = SharedMemory(Settings().shared_db).load_outcomes()
    live = cal.from_outcomes(rows)
    if live.n:
        print()
        print(cal.render(live))
    return 0


def cmd_walkforward(args) -> int:
    """Walk the desk's rule forward through unseen windows.

    Reports the train/test decay, the controls on the SAME windows, and the
    paired difference against always-long -- the only number that says whether
    the timing added anything over the market's drift.
    """
    from .walkforward import run_walkforward

    print(run_walkforward(args.symbol or "XAUUSD", folds=args.folds))
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


def cmd_daemon(args) -> int:
    """Run the desk as one always-on process: chat, levels, and readings.

    This is the shape a real host wants and a scheduler cannot give. `serve`
    reads on an interval but never the chat; `bot --listen` reads the chat for a
    few minutes but never reads the market. Here both run, plus a level check
    between readings, until stopped.

    The cadences are read from the environment as well as the flags, so a
    container can be tuned without editing the entrypoint. A flag wins.
    """
    import os

    from .daemon import Daemon

    def pick(flag: float | None, env: str, default: float) -> float:
        if flag is not None:
            return flag
        try:
            return float(os.environ.get(env, default))
        except ValueError:
            return default

    d = Daemon(_settings(args),
               level_every=pick(args.level_every, "DESK_LEVEL_EVERY", 10.0),
               read_every=pick(args.read_every, "DESK_READ_EVERY", 900.0),
               poll_wait_s=pick(args.poll_wait, "DESK_POLL_WAIT", 50.0),
               ping_every=pick(args.ping_every, "DESK_PING_EVERY", 600.0),
               web=args.web)
    d.install_signals()
    out = d.run_forever(seconds=args.seconds)
    print(json.dumps(out, ensure_ascii=False, default=str), flush=True)
    return 0


def cmd_check_alerts(args) -> int:
    """Verify the alert pipeline in one call.

    The desk is silent by design, so an unconfigured or dead notifier looks
    exactly like 'no signal yet'. This separates the three cases: not
    configured, token rejected, and chat unreachable. Secrets are never printed.
    """
    from .notify import Notifier

    s = _settings(args)
    n = Notifier(s)
    print(f"token set:   {bool(s.telegram_token)}")
    print(f"chat_id set: {bool(s.telegram_chat_id)}")
    if not s.telegram_token:
        print("result: NOT CONFIGURED — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return 2
    if not args.send:
        # Without --send this stays a read-only report; the probe sends a real
        # message, so it is only run when the caller asked for one.
        ok, why = n.probe()
        print(f"probe: {why}")
        fresh, dfresh = Orchestrator(s).data_fresh()
        print(f"data fresh:  {'yes' if dfresh else 'NO — ' + dfresh}"
              f"  (limit {s.max_data_age_s / 3600:.1f}h, DESK_MAX_DATA_AGE_S)")
        return 0 if ok else 1
    ok, why = n.probe()
    print(f"probe: {why}")
    return 0 if ok else 1


def _measured_hit_rate(s: Settings) -> float:
    """The desk's own measured win rate for the current market state.

    Ruin computed on an assumed 55% is a decoration. This reads the analog
    engine's measured hit rate for the state the market is in right now, and
    falls back to the long-run rate when the state is too thin to measure.
    """
    try:
        from . import history
        from . import state as state_mod
        from .agents.analog import MIN_N, measure_cached

        C = history.shaped(s.symbol)
        if len(C) < state_mod.TREND_MA + 40:
            return 0.5
        cur = state_mod.state_at(C, len(C) - 1)
        if cur is None:
            return 0.5
        m = measure_cached(C, cur.key())
        for side in ("long", "short"):
            side_s = m.get(side) or {}
            if side_s.get("n", 0) >= MIN_N:
                return float(side_s["hit"])
    except Exception:  # noqa: BLE001 - a missing file must not block sizing
        pass
    return 0.5


def cmd_account(args) -> int:
    """Size the plan in play (or a scenario) for the account, in money.

    This is the question the price levels do not answer: how many lots, and what
    a stop-out costs. On a small account the answer is often "you cannot take
    this one", and this says so with the number that proves it.
    """
    from . import active, sources
    from .account import render, ruin_render, ruin_risk, size_plan

    s = _settings(args)
    if args.balance is not None:
        s.balance = args.balance
    if args.risk is not None:
        s.risk_pct = args.risk
    if args.max_stop is not None:
        s.max_stop_points = args.max_stop
    if args.min_stop is not None:
        s.min_stop_points = args.min_stop
    if not s.balance:
        print("ما في حساب محدد. مرّر --balance 200 (أو DESK_BALANCE=200).")
        return 2

    if getattr(args, "ruin", False):
        hit = args.hit
        if hit is None:
            hit = _measured_hit_rate(s)
        r = ruin_risk(risk_pct=s.risk_pct / 100.0, hit_rate=hit,
                      drawdown_frac=args.drawdown / 100.0)
        print(ruin_render(r))
        if args.hit is None:
            print("  (معدل الفوز من قياس المكتب على 10 سنين — مرّر --hit لتجاوزه.)")
        return 0

    if args.entry is not None and args.stop is not None:
        sz = size_plan(balance=s.balance, risk_pct=s.risk_pct, entry=args.entry,
                       stop=args.stop, target=args.target,
                       contract_size=s.contract_size, lot_step=s.lot_step,
                       spread=s.spread, min_stop_points=s.min_stop_points,
                       max_stop_points=s.max_stop_points)
        print(render(sz, s.symbol, args.direction or ""))
        return 0

    plan = active.load(s)
    if plan is None:
        print("ما في خطة نشطة نسعّرها. مرّر --entry و --stop، أو خزّن خطة أولاً.")
        return 0
    sz = size_plan(balance=s.balance, risk_pct=s.risk_pct, entry=plan.entry,
                   stop=plan.stop, target=plan.target,
                   contract_size=s.contract_size, lot_step=s.lot_step,
                   spread=s.spread, min_stop_points=s.min_stop_points,
                       max_stop_points=s.max_stop_points)
    print(render(sz, s.symbol, plan.direction))
    try:
        live = float(sources.fetch_quote(s.symbol)["mid"])
        pnl = (plan.entry - live) if plan.direction == "short" else (live - plan.entry)
        if sz.feasible:
            print(f"\nالصفقة هلق: {pnl:+.2f}$ لكل أونصة → "
                  f"<b>${pnl * sz.lots * s.contract_size:+,.2f}</b> على الحساب")
    except Exception:  # noqa: BLE001 - a dead feed must not hide the sizing
        pass
    return 0


def cmd_plan_status(args) -> int:
    """Show the one plan the desk is following, and its status.

    The desk alerts on status changes; this is the same view on demand, for when
    the owner wants to know what is in play without waiting for a message.
    """
    import time

    from . import active

    s = _settings(args)
    if args.clear:
        active.clear(s)
        print("active plan cleared — the desk will issue a fresh one on the next signal")
        return 0
    plan = active.load(s)
    if plan is None:
        print("no active plan — the desk is watching for a fresh signal")
        return 0
    live = None
    try:
        from . import sources
        live = float(sources.fetch_quote(s.symbol)["mid"])
    except Exception:  # noqa: BLE001 - a dead feed must not hide the stored plan
        pass
    print(f"active plan: {plan.direction}  entry {plan.entry:,.2f}  "
          f"stop {plan.stop:,.2f}  target {plan.target:,.2f}")
    print(f"issued {time.strftime('%Y-%m-%d %H:%M', time.gmtime(plan.issued_at))} UTC "
          f"· run {plan.run_id}")
    if live is not None:
        status = plan.evaluate(live, expiry_s=s.plan_expiry_s)
        print(f"live {live:,.2f} → status {status}")
    else:
        print(f"stored status {plan.status} (live quote unavailable)")
    return 0


def _menu_text(settings) -> str:
    from .actions import Actions
    return Actions(settings).menu_text()


def cmd_bot(args) -> int:
    """One poll pass: read the button presses since the last pass and answer.

    Run from a schedule, not in a loop. Telegram confirms the updates when the
    offset is sent back at the end of the pass, so a fresh runner with no disk
    still never replays a press.

    `--listen` keeps the pass connected instead: it long-polls Telegram and
    answers the moment something is pressed, for `--seconds`. That is what turns
    a reply that lands on the next pass into one that lands in about a second.
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
    if args.listen:
        out = bot.listen(seconds=args.seconds, max_replies=args.max_replies)
    else:
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


def cmd_telegram(args) -> int:
    """Report which transport Telegram is actually serving this bot on.

    This is the question every "why does the bot not answer?" investigation
    starts with, and it has exactly one honest answer: `getWebhookInfo`. Telegram
    serves updates to a webhook OR to getUpdates, never both -- so a webhook left
    registered silently starves a poll, and the poller sees only a bare 409.

    Exit code 0 means a webhook is registered (push transport, as the daemon
    wants); 1 means none is (so a poller can work, but a sleeping host cannot be
    woken by a press). Printing it rather than asserting it keeps the command
    usable in both worlds.
    """
    import json

    from .bot import TelegramBot

    s = _settings(args)
    bot = TelegramBot(s)
    if not bot.available:
        print("result: NOT CONFIGURED — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return 2
    if getattr(args, "clear_webhook", False):
        ok = bot.delete_webhook()
        print(f"deleteWebhook: {'ok' if ok else 'FAILED'}")
        info = bot.webhook_info()
        still = (info.get("url") or "") if info else ""
        print(f"webhook now: {still or 'none (getUpdates will work)'}")
        return 0 if ok and not still else 1
    info = bot.webhook_info()
    if not info:
        print("result: UNREADABLE — the token is rejected or the API is unreachable")
        return 2
    url = info.get("url") or ""
    out = {
        "url": url or None,
        "pending_updates": info.get("pending_update_count", 0),
        "last_error": info.get("last_error_message") or None,
        "transport": "webhook" if url else "poll",
    }
    # The chat the desk will actually send to. "The bot works" and "you see the
    # messages" are different claims: a chat_id pointing at the wrong chat sends
    # fine and reaches nobody the owner is looking at. getChat names it without
    # printing the id, which is the honest way to close that gap.
    chat = bot.get_chat()
    if chat:
        out["chat"] = {
            "type": chat.get("type"),
            "title": chat.get("title") or chat.get("username")
                     or " ".join(filter(None, [chat.get("first_name"),
                                               chat.get("last_name")])),
        }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if url and info.get("last_error_message"):
        print(f"\n⚠ Telegram could not deliver to {url}: "
              f"{info['last_error_message']} — the URL or the secret is stale.")
    return 0 if url else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="desk", description="MarketDesk multi-agent analysis")
    p.add_argument("--symbol", default=None, help="instrument (default XAUUSD)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="full run: gather, dispatch, synthesise")
    r.add_argument("--symbol", default=None)
    r.add_argument("--lang", default="ar", choices=["ar", "en"])
    r.add_argument("--json", action="store_true")
    r.add_argument("--send", action="store_true",
                   help="deliver the full analysis to the chat")
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

    gd = sub.add_parser("gold", help="10-year gold study: this state, what next?")
    gd.add_argument("--csv", default="../longdata/XAUUSD_1d.csv")
    gd.add_argument("--symbol", default="XAUUSD")
    gd.add_argument("--offline", action="store_true")
    gd.add_argument("--stop-atr", type=float, default=0.32)
    gd.add_argument("--tgt-atr", type=float, default=0.60)
    gd.set_defaults(func=cmd_gold)

    cm = sub.add_parser("crossmarket",
                        help="test whether the trend signal generalises")
    cm.add_argument("--dir", default="../longdata")
    cm.add_argument("--markets", nargs="*", default=None)
    cm.set_defaults(func=cmd_crossmarket)

    ld = sub.add_parser("longdata", help="download 10y daily history, several markets")
    ld.add_argument("--out", default="../longdata")
    ld.add_argument("--range", default="10y")
    ld.set_defaults(func=cmd_longdata)

    lv = sub.add_parser("levels", help="alert once when price crosses or nears a watched level")
    lv.add_argument("--symbol", default=None)
    lv.add_argument("--within", type=float, default=12.0,
                    help="points band for the early 'approaching' warning (default 12)")
    lv.add_argument("--hold", type=float, default=0,
                    help="keep watching for this many seconds instead of one pass")
    lv.add_argument("--every", type=float, default=60,
                    help="seconds between checks while holding (default 60)")
    lv.set_defaults(func=cmd_levels)

    sn = sub.add_parser("scenarios",
                        help="conditional scenarios from printed levels + the calendar")
    sn.add_argument("--symbol", default=None)
    sn.add_argument("--send", action="store_true",
                    help="deliver to the chat instead of printing")
    sn.set_defaults(func=cmd_scenarios)

    mp = sub.add_parser("map", help="the daily map: buy/sell zones, liquidity, trend")
    mp.add_argument("--symbol", default=None)
    mp.add_argument("--send", action="store_true",
                    help="deliver to the chat instead of printing")
    mp.add_argument("--once-a-day", dest="once_a_day", action="store_true",
                    help="send at most one map per day, stamping the date")
    mp.set_defaults(func=cmd_map)

    pl = sub.add_parser("plan", help="live two-sided plan with stop and target ladder")
    pl.add_argument("--csv", default="../biquote/XAUUSD_1d.csv")
    pl.add_argument("--offline", action="store_true",
                    help="read the shipped CSV instead of the live feed")
    pl.set_defaults(func=cmd_plan)

    sc = sub.add_parser("scan", help="test standard signals for any edge at all")
    sc.add_argument("--csv", default="../biquote/XAUUSD_1d.csv",
                    help="daily bar CSV to scan")
    sc.set_defaults(func=cmd_scan)

    bs = sub.add_parser("bias", help="audit the desk's own rule for a structural lean")
    bs.add_argument("--symbol", default=None)
    bs.set_defaults(func=cmd_bias)

    cl = sub.add_parser("calibration",
                        help="is the desk's confidence calibrated against outcomes?")
    cl.add_argument("--symbol", default=None)
    cl.set_defaults(func=cmd_calibration)

    wf = sub.add_parser("walkforward",
                        help="does the desk survive a window it has never seen?")
    wf.add_argument("--symbol", default=None)
    wf.add_argument("--folds", type=int, default=4)
    wf.set_defaults(func=cmd_walkforward)

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

    dm = sub.add_parser(
        "daemon", help="always-on process: chat, levels, and readings")
    dm.add_argument("--symbol", default=None)
    dm.add_argument("--level-every", type=float, default=None,
                    help="seconds between level checks (default 10)")
    dm.add_argument("--read-every", type=float, default=None,
                    help="seconds between full readings (default 900)")
    dm.add_argument("--poll-wait", type=float, default=None,
                    help="seconds one long poll waits on Telegram (default 50)")
    dm.add_argument("--ping-every", type=float, default=None,
                    help="seconds between self-pings of our own /health, so a "
                         "free host does not sleep (default 600, 0 disables)")
    dm.add_argument("--seconds", type=float, default=None,
                    help="run for this long instead of forever (smoke test)")
    dm.add_argument("--web", action="store_true",
                    help="serve an inbound endpoint and let Telegram push updates")
    dm.set_defaults(func=cmd_daemon)

    ca = sub.add_parser("check-alerts", help="verify the alert pipeline end to end")
    ca.add_argument("--symbol", default=None)
    ca.add_argument("--send", action="store_true",
                    help="also deliver a test message to the chat")
    ca.set_defaults(func=cmd_check_alerts)

    ps = sub.add_parser("plan-status", help="show the plan currently being followed")
    ps.add_argument("--symbol", default=None)
    ps.add_argument("--clear", action="store_true", help="forget the active plan")
    ps.set_defaults(func=cmd_plan_status)

    ac = sub.add_parser("account", help="size the plan in money for an account")
    ac.add_argument("--symbol", default=None)
    ac.add_argument("--balance", type=float, default=None)
    ac.add_argument("--risk", type=float, default=None, help="percent per trade")
    ac.add_argument("--entry", type=float, default=None)
    ac.add_argument("--stop", type=float, default=None)
    ac.add_argument("--target", type=float, default=None)
    ac.add_argument("--direction", default=None)
    ac.add_argument("--min-stop", type=float, default=None,
                    help="refuse a stop tighter than this, in points")
    ac.add_argument("--max-stop", type=float, default=None,
                    help="refuse a stop wider than this, in points")
    ac.add_argument("--ruin", action="store_true",
                    help="risk-of-ruin: chance of a losing run you cannot recover")
    ac.add_argument("--hit", type=float, default=None,
                    help="win rate for the ruin model (default: the desk's measured one)")
    ac.add_argument("--drawdown", type=float, default=50.0,
                    help="the loss %% that counts as ruined (default 50)")
    ac.set_defaults(func=cmd_account)

    b = sub.add_parser("bot", help="one poll pass: answer button presses")
    b.add_argument("--symbol", default=None)
    b.add_argument("--offset", type=int, default=None,
                   help="update_id to start from (default: Telegram's cursor)")
    b.add_argument("--budget", type=float, default=120.0,
                   help="seconds to spend answering before deferring the rest")
    b.add_argument("--greet", action="store_true",
                   help="also publish the menu before polling")
    b.add_argument("--listen", action="store_true",
                   help="stay connected and answer presses as they arrive")
    b.add_argument("--seconds", type=int, default=300,
                   help="how long --listen stays connected")
    b.add_argument("--max-replies", type=int, default=8,
                   help="cap on replies per poll inside --listen")
    b.set_defaults(func=cmd_bot)

    mn = sub.add_parser("menu", help="publish the button menu to the chat")
    mn.add_argument("--symbol", default=None)
    mn.set_defaults(func=cmd_menu)

    tg = sub.add_parser(
        "telegram", help="ask Telegram what transport this bot actually uses")
    tg.add_argument("--clear-webhook", action="store_true",
                    help="delete a registered webhook so getUpdates (the poll "
                         "transport) works again; use when a dead host left one")
    tg.set_defaults(func=cmd_telegram)

    return p


def main(argv=None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
