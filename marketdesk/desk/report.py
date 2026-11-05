"""Report — turn a Run into a readable card (Arabic by default).

The card keeps the three things the desk insists on: every number has a source,
a missing value is an em dash and never a zero, and the risk gate is stated even
when it says "no trade".
"""

from __future__ import annotations

from . import arabic as ar
from .orchestrator import Run

# Kept as names for older callers and tests; both are the central tables now.
VERDICT_AR = ar.VERDICT
STANCE_AR = ar.STANCE


def _n(v, nd=2, suffix=""):
    if v is None:
        return "—"
    try:
        return f"{v:,.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return str(v)


def render(run: Run, lang: str = "ar") -> str:
    r = run.recommendation
    L: list[str] = []
    head = "قراءة السوق والتوصية" if lang == "ar" else "Market read & recommendation"
    L.append(f"=== {head} — {run.symbol} ===")
    # A stand-down is not a reading: the desk never looked. Say so in one line
    # rather than print a card of em dashes that reads like a data failure.
    sess = r.get("session") or {}
    if sess and not sess.get("open", True):
        L.append(f"⏸ {sess.get('reason') or 'السوق مقفول'}")
        if sess.get("opens_at"):
            L.append(f"بيفتح: {sess['opens_at'][:16].replace('T', ' ')} UTC")
        L.append("لا قراءة ولا توصية — المكتب بيستنّى افتتاح السوق.")
        return "\n".join(L)
    q = (r.get("gather") or {}).get("quote") or {}
    L.append(f"السعر {_n(q.get('mid'), 3)} · السوق {ar.market_state(q.get('market_state'))} · "
             f"عمر التسعيرة {q.get('quote_age_s', '—')} ثانية · تغيّر اليوم {_n(q.get('day_diff_pct'), 2, '%')}")
    L.append(f"المدة {run.duration_s:.1f} ثانية · المعرّف {run.run_id}")
    llm = r.get("llm") or {}
    if llm and not llm.get("available", True):
        L.append("⚠ النموذج غير متاح — النصوص أدناه حتمية، محسوبة لا مكتوبة.")
    elif llm.get("rotations"):
        L.append(f"↻ النموذج: {llm.get('model')} "
                 f"(دار {llm['rotations']} مرة بعد نفاد حصة موديل).")
    L.append("")
    L.append("— دراسات الوكلاء —")
    for s in sorted(run.studies, key=lambda x: x.agent):
        L.append(f"  [{ar.agent(s.agent)}] {ar.stance(s.stance)} "
                 f"ثقة {s.confidence:.2f} · نتيجة {s.score:+.2f}")
        L.append(f"               {s.summary}")
    L.append("")
    L.append("— التوصية النهائية —")
    L.append(f"  الاتجاه: {ar.verdict(run.verdict)}  (نتيجة مرجّحة {run.score:+.2f})")
    bias = r.get("bias") or {}
    if bias.get("direction"):
        L.append(f"  الميل الضمني: {ar.direction(bias['direction'])} ({bias.get('score'):+.3f})")
    pfb = r.get("per_frame_bias") or {}
    if pfb:
        order = ["1d", "4h", "1h", "30m", "15m", "5m", "1m"]
        cells = [f"{tf}:{pfb[tf]:+.1f}" for tf in order if tf in pfb]
        L.append(f"  الميل حسب الفريم: {' · '.join(cells)}")
    L.append(f"  مسموح بالتداول: {'نعم' if r.get('allowed') else 'لا'}")
    if r.get("entry") is not None:
        L.append(f"  الدخول {_n(r.get('entry'))} · الوقف {_n(r.get('stop'))} · "
                 f"المخاطرة/المكافأة {_n(r.get('rr'), 2)}")
        tg = " · ".join(f"{_n(t['level'])} ({ar.kind(t['kind'])})" for t in r.get("targets", []))
        L.append(f"  الأهداف: {tg}")
    if r.get("vol_ratio") is not None:
        mult = r.get("vol_mult")
        note = "" if mult in (None, 1.0) else f" → وسّعنا الوقف {mult:.2f}×"
        L.append(f"  نشاط آخر شمعة ساعة: {r['vol_ratio']:.2f}× المتوسط{note}")
    if r.get("levels_note"):
        L.append(f"  ملاحظة: {r['levels_note']}")
    if r.get("warnings"):
        L.append(f"  ⚠ تحذيرات: {'; '.join(r['warnings'])}")
    if r.get("errors"):
        L.append(f"  ⚠ أخطاء المصدر: {'; '.join(r['errors'])}")
    active_line = r.get("active_plan_line")
    if active_line:
        L.append(active_line)
    return "\n".join(L)
