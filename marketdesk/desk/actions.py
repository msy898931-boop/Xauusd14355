"""Actions — what each button does, and how it phrases the answer.

The expensive action is `analysis`: it runs the whole desk, which calls the data
source and the model. The rest are instant, because they read what an earlier
run already stored. That split is what keeps a free, five-minute poll affordable
-- a chat full of "what's the market doing" must not trigger eight agents and a
model call every time it is pressed.

Every answer is built from numbers the desk computed. Nothing here invents a
value, and a missing value is stated as missing.
"""

from __future__ import annotations

import json
import time

from . import arabic as ar
from . import sources
from .agents.base import Study
from .config import Settings
from .memory import SharedMemory
from .notify import escape
from .orchestrator import Run
from .report import VERDICT_AR, render


def _e(value) -> str:
    """Escape a value that did not come from the desk's own constants — a model
    summary, a source error, a stored note. Telegram rejects a message with an
    unknown HTML entity, so one stray `&` would drop the whole reply."""
    return escape(str(value))


def _age(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} ثانية"
    if seconds < 5400:
        return f"{seconds // 60} دقيقة"
    return f"{seconds // 3600} ساعة"


def _payload(row: dict) -> dict:
    """`runs.payload` is stored as a JSON string (only `load_studies` decodes its
    own column), so a caller that reads the run row must decode it too. Reading
    it as a dict is how the button crashed on its first live press."""
    raw = row.get("payload")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        out = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return out if isinstance(out, dict) else {}


def _run_from_row(shared: SharedMemory, row: dict) -> Run:
    """Rebuild a Run from what a previous pass stored, so a past card can be
    rendered again without re-running the desk."""
    studies = [Study(agent=s["agent"], kind=s["kind"], stance=s["stance"],
                     confidence=s["confidence"], score=s["score"],
                     summary=s["summary"], payload=s["payload"])
               for s in shared.load_studies(row["run_id"])]
    rec = _payload(row)
    return Run(row["run_id"], row["symbol"], row["started_at"], studies,
               row["verdict"], row["score"], rec,
               rec.get("errors") or [], (row["finished_at"] or 0) - row["started_at"])


class Actions:
    # Answers that build their own HTML escape their dynamic fields inline and
    # must NOT be escaped as a block, or the chat prints `&lt;b&gt;`. The rest
    # are plain text -- a study summary can contain `&` -- and are escaped as
    # one block, because a raw `&` makes Telegram reject the whole message.
    MARKUP_ACTIONS = ("recommend", "market", "plan", "levels", "help", "menu",
                      "chart", "scenarios")

    def __init__(self, settings: Settings, orchestrator=None):
        self.settings = settings
        self.shared = SharedMemory(settings.shared_db)
        self._orchestrator = orchestrator

    def _desk(self):
        if self._orchestrator is None:
            from .orchestrator import Orchestrator
            self._orchestrator = Orchestrator(self.settings)
        return self._orchestrator

    # ---- the menu ------------------------------------------------------
    def menu_text(self) -> str:
        return ("<b>مكتب تحليل السوق</b>\n"
                "اختر من الأزرار 👇\n"
                "\n"
                "📊 تحليل الآن · 🎯 التوصية\n"
                "🎬 السيناريوهات · 📈 الخطة الكاملة\n"
                "📉 الشارت · ⏱️ السوق\n"
                "🔔 المستويات · 📚 دراسة الذهب\n"
                "📋 آخر تقرير · 📈 معدل النجاح\n"
                "❓ مساعدة\n"
                "\n"
                "<i>كل ردّ معه زر «🔄 تحديث» و«🏠 القائمة».</i>")

    def run(self, action: str) -> str:
        handler = {
            "analysis": self.analysis,
            "recommend": self.recommend,
            "report": self.report,
            "market": self.market,
            "hitrate": self.hitrate,
            "plan": self.plan,
            "levels": self.levels,
            "scenarios": self.scenarios,
            "gold": self.gold,
            "chart": self.chart,
            "help": self.help,
            "menu": self.menu_text,
        }.get(action)
        if handler is None:
            return f"⚠️ زر غير معروف: {_e(action)}"
        text = handler()
        if action in self.MARKUP_ACTIONS:
            return text
        return escape(text)

    # ---- the long history file -----------------------------------------
    def _long_csv(self):
        """Where the 10-year CSV lives, from the package or the repo root.

        The bot runs from a fresh checkout, so the path must be found rather than
        assumed: `../longdata` is right from `marketdesk/`, but the runner's cwd
        is the repo root.
        """
        from pathlib import Path

        here = Path(__file__).resolve()
        for cand in (here.parent.parent.parent / "longdata" / f"{self.settings.symbol}_1d.csv",
                     Path("longdata") / f"{self.settings.symbol}_1d.csv",
                     Path("../longdata") / f"{self.settings.symbol}_1d.csv"):
            if cand.exists():
                return cand
        return None

    def help(self) -> str:
        return (
            "<b>مكتب تحليل السوق — المساعدة</b>\n"
            "\n"
            "<b>الأزرار:</b>\n"
            "📊 <b>تحليل الآن</b> — تشغيل المكتب كامل (كل الوكلاء + النموذج) وتوصية\n"
            "🎯 <b>التوصية</b> — آخر قراءة محفوظة، فورية\n"
            "📈 <b>الخطة الكاملة</b> — شراء وبيع مشروط مع الوقف والأهداف\n"
            "📉 <b>الشارت</b> — صورة السعر مقابل المستويات المراقَبة\n"
            "⏱️ <b>السوق</b> — السعر الحالي وعمر التسعيرة\n"
            "🔔 <b>المستويات</b> — المستويات المراقَبة وأيّها انكسر\n"
            "📚 <b>دراسة الذهب</b> — 10 سنين: شو صار لما كان الذهب بهالحالة\n"
            "📋 <b>آخر تقرير</b> — البطاقة الكاملة لكل وكيل\n"
            "📈 <b>معدل النجاح</b> — تقييم الصفقات السابقة\n"
            "\n"
            "<b>كل ردّ معه:</b> 🔄 تحديث (يعيد نفس الطلب) · 🏠 القائمة (يرجّعك للقائمة)\n"
            "\n"
            "<b>أوامر سريعة:</b> /menu · /plan · /chart · /gold · /levels\n"
            "\n"
            "<i>الرد بيوصل خلال ثوانٍ إذا في جلسة مراقبة شغّالة، وإلا لحد 5 دقايق. "
            "المكتب صامت بشكل مقصود — ما بيبعت إلا لما يكون في شي.</i>")

    def plan(self) -> str:
        """The two-sided conditional plan, from the shipped history."""
        csv = self._long_csv()
        if csv is None:
            return "📈 لا يوجد ملف تاريخ محفوظ. شغّل <code>desk longdata</code> أولاً."
        from .backtest import load_daily
        from .plan import build_plan

        bars = load_daily(csv)
        closed = [b for b in bars if not b.get("isOpen")]
        if len(closed) < 80:
            return "📈 التاريخ قصير — بدك 80 شمعة مقفلة على الأقل."
        live = closed[-1]["close"]
        quote, _ = self._quote_or_none()
        if quote:
            live = float(quote["mid"])
        p = build_plan(bars, live)
        return self._format_plan_ar(p)

    def _quote_or_none(self) -> tuple[dict | None, str | None]:
        try:
            q = sources.fetch_quote(self.settings.symbol)
            self.shared.save_quote(self.settings.symbol, q)
            return q, None
        except sources.SourceError as exc:
            return self.shared.load_quote(self.settings.symbol), str(exc)

    def _format_plan_ar(self, p: dict) -> str:
        long_s, short_s = p["long"], p["short"]
        trend = "صاعد" if p["ema20"] > p["ema50"] else "هابط"
        lines = [
            f"📈 <b>الخطة الكاملة</b> — {self.settings.symbol}",
            f"السعر <b>{p['live']:,.2f}</b> · الاتجاه العام <b>{trend}</b>",
            f"ATR {p['atr']:,.2f} · RSI {p['rsi']:.0f} · "
            f"المدى {p['prior_low']:,.2f}–{p['prior_high']:,.2f}",
        ]
        if p.get("window_start"):
            lines.append(f"<i>المدى محسوب على {p['window_bars']} يوم: "
                         f"{p['window_start']} ← {p['window_end']}</i>")
        lines += [
            "",
            "🟢 <b>شراء مشروط</b>",
            f"  يتفعّل فوق <b>{long_s.trigger:,.2f}</b>",
            f"  الوقف {long_s.stop:,.2f}  ({long_s.risk:,.0f} نقطة)",
            "  الأهداف: " + " · ".join(f"{t:,.0f}" for t in long_s.targets),
            "",
            "🔴 <b>بيع مشروط</b>",
            f"  يتفعّل تحت <b>{short_s.trigger:,.2f}</b>",
            f"  الوقف {short_s.stop:,.2f}  ({short_s.risk:,.0f} نقطة)",
            "  الأهداف: " + " · ".join(f"{t:,.0f}" for t in short_s.targets),
            "",
            "<i>السعر بين الطرفين = لا صفقة. الخطة مشروطة، مو توصية.</i>",
        ]
        return "\n".join(lines)

    def levels(self) -> str:
        """The watched levels, which side is broken, and which is about to be."""
        from .levels import DEFAULT_LEVELS, approaching, triggered

        quote, err = self._quote_or_none()
        if not quote:
            return f"🔔 تعذّر قراءة السعر: {_e(err or 'لا توجد تسعيرة')}"
        live = float(quote["mid"])
        hits = triggered(live, DEFAULT_LEVELS)
        near = {lv.price: d for lv, d in approaching(live, DEFAULT_LEVELS)}
        lines = [f"🔔 <b>المستويات المراقَبة</b> — {self.settings.symbol}",
                 f"السعر <b>{live:,.2f}</b>", ""]
        for lv in sorted(DEFAULT_LEVELS, key=lambda x: -x.price):
            hit = lv in hits
            arrow = "▲" if lv.side == "above" else "▼"
            mark = "🔴" if hit else ("🟡" if lv.price in near else "⚪")
            lines.append(f"{mark} {arrow} <b>{lv.price:,.2f}</b> — {_e(lv.label)}")
            if hit:
                lines.append(f"      <i>{_e(lv.note)}</i>")
            elif lv.price in near:
                lines.append(f"      <i>باقي {near[lv.price]:,.1f} نقطة — قريب</i>")
        lines.append("")
        if hits:
            lines.append(f"<b>{len(hits)} من {len(DEFAULT_LEVELS)} مستوى منكسر.</b>")
        elif near:
            lines.append(f"<i>{len(near)} مستوى قريب، ولسا ولا واحد منكسر.</i>")
        else:
            lines.append("<i>ولا مستوى منكسر أو قريب — السعر بين المستويات.</i>")
        return "\n".join(lines)

    def scenarios(self) -> str:
        """Conditional scenarios from printed levels, ATR, and the calendar.

        Read-only over what a run already stored: it loads the closed frames and
        the calendar from shared memory and computes the levels itself. It does
        not start the agents or call the model, so pressing it is cheap.
        """
        from . import scenarios as S

        quote, err = self._quote_or_none()
        if not quote:
            return f"🎯 تعذّر قراءة السعر: {_e(err or 'لا توجد تسعيرة')}"
        live = float(quote["mid"])

        frames: dict[str, list[dict]] = {}
        for tf in self.settings.timeframes:
            rows = self.shared.load_candles(self.settings.symbol, tf)
            frames[tf] = [{"t": r["open_time"], "o": r["open"], "h": r["high"],
                           "l": r["low"], "c": r["close"], "open": bool(r["is_open"])}
                          for r in rows]
        cal = self.shared.load_calendar()
        sc = S.build(frames, live, cal, window_h=self.settings.event_window_h,
                     symbol=self.settings.symbol, balance=self.settings.balance,
                     risk_pct=self.settings.risk_pct,
                     contract_size=self.settings.contract_size,
                     lot_step=self.settings.lot_step, spread=self.settings.spread,
                     max_stop_points=self.settings.max_stop_points)
        return S.render(sc)

    def gold(self) -> str:
        """The 10-year study: this state, and what followed it before."""
        csv = self._long_csv()
        if csv is None:
            return "📚 لا يوجد ملف تاريخ. شغّل <code>desk longdata</code> أولاً."
        from .gold import run_gold_study

        quote, _ = self._quote_or_none()
        live = float(quote["mid"]) if quote else None
        try:
            return run_gold_study(csv, live=live, symbol=self.settings.symbol)
        except ValueError as exc:
            return f"📚 تعذّرت الدراسة: {_e(exc)}"

    # ---- instant (no agents, no model) ---------------------------------
    def _last_run(self) -> dict | None:
        rows = self.shared.recent_runs(1)
        return rows[0] if rows else None

    def recommend(self) -> str:
        row = self._last_run()
        if not row:
            return ("لا توجد قراءة محفوظة بعد.\n"
                    "اضغط «📊 تحليل الآن» لتشغيل أول قراءة.")
        rec = _payload(row)
        verdict = VERDICT_AR.get(row["verdict"], row["verdict"])
        age = _age(time.time() - (row["finished_at"] or row["started_at"]))
        lines = [f"<b>التوصية الحالية</b> — {_e(row['symbol'])}",
                 f"الاتجاه: <b>{verdict}</b> · نتيجة {row['score']:+.2f}",
                 f"عمر القراءة: {age}"]
        if rec.get("entry") is not None:
            lines.append(f"الدخول {rec['entry']} · الوقف {rec['stop']} · "
                         f"R:R {rec.get('rr')}")
            tg = " · ".join(f"{t['level']} ({_e(t['kind'])})" for t in rec.get("targets", []))
            lines.append(f"الأهداف: {tg}")
        if rec.get("levels_note"):
            lines.append(f"ملاحظة: {_e(rec['levels_note'])}")
        lines.append("(للقراءة الحيّة اضغط «📊 تحليل الآن»)")
        return "\n".join(lines)

    def report(self) -> str:
        row = self._last_run()
        if not row:
            return "لا يوجد تقرير محفوظ بعد."
        return render(_run_from_row(self.shared, row), lang="ar")

    def market(self) -> str:
        s = self.settings
        quote, err = None, None
        try:
            quote = sources.fetch_quote(s.symbol)
            self.shared.save_quote(s.symbol, quote)
        except sources.SourceError as exc:
            err = str(exc)
            quote = self.shared.load_quote(s.symbol)
        if not quote:
            return f"⏱️ <b>{s.symbol}</b>\nتعذّر قراءة السعر: {err or 'لا توجد تسعيرة محفوظة'}"
        counts = self.shared.candle_counts(s.symbol)
        stale = "نعم" if quote.get("stale") else "لا"
        lines = [f"⏱️ <b>حالة السوق</b> — {s.symbol}",
                 f"السعر {quote.get('mid')} · السوق {ar.market_state(quote.get('market_state'))}",
                 f"قديم؟ {stale} · عمر التسعيرة {_age(quote.get('quote_age_s'))} · "
                 f"تغيّر اليوم {quote.get('day_diff_pct', '—')}%"]
        if err:
            lines.append(f"⚠️ تعذّر التحديث الآن ({err}) — هذه آخر تسعيرة محفوظة.")
        if counts:
            frames = " · ".join(f"{tf}:{v.get('n', 0)}" for tf, v in counts.items())
            lines.append(f"الشمعات المحفوظة: {frames}")
        return "\n".join(lines)

    def hitrate(self) -> str:
        from .scoreboard import Grader
        board = Grader(self.shared).grade()
        return board.report()

    # ---- the chart ------------------------------------------------------
    def chart_png(self) -> tuple[bytes, str] | None:
        """A price chart and its caption, or None when history is missing.

        Plots the last ~120 closed daily bars against the watched levels, so the
        picture and the `levels` action show the same lines. The forming bar is
        dropped, like every other reader of the history.
        """
        csv = self._long_csv()
        if csv is None:
            return None
        from .backtest import load_daily
        from .chart import price_chart
        from .levels import DEFAULT_LEVELS

        bars = [b for b in load_daily(csv) if not b.get("isOpen")][-120:]
        if len(bars) < 2:
            return None
        quote, _ = self._quote_or_none()
        live = float(quote["mid"]) if quote else None
        png = price_chart(bars, levels=DEFAULT_LEVELS, live=live)
        head = f"📉 <b>{self.settings.symbol}</b> — آخر {len(bars)} يوم"
        if live:
            head += f" · السعر <b>{live:,.2f}</b>"
        head += "\n<i>الخط الأصفر: الإغلاق. الأخضر: مستوى فوق. الأحمر: مستوى تحت.</i>"
        return png, head

    def chart(self) -> str:
        """The chart as a text answer, for a caller that cannot send a photo."""
        out = self.chart_png()
        if out is None:
            return "📉 لا يوجد تاريخ محفوظ للرسم."
        _, caption = out
        return caption

    # ---- the expensive one ---------------------------------------------
    def analysis(self) -> str:
        desk = self._desk()
        run = desk.run()
        return render(run, lang="ar")
