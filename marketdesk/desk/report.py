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


def _scenarios_plain(sc: dict, *, show_study: bool = False) -> str:
    """The scenario set as plain text, for the main card.

    `scenarios.render` emits Telegram HTML; the card is read in a terminal, so
    the tags would show. Same content, same numbers, no markup.

    `show_study` gates the probability line. The probability is the ten-year
    study's number for this state, and the owner's rule keeps the study off the
    live card; the actionable part of a scenario -- trigger, stop, target, R:R,
    invalidation, size -- is live and stays.
    """
    L = [f"— السيناريوهات — (السعر {_n(sc.get('live'), 2)})"]
    if sc.get("probability_state"):
        L.append(f"  حالة السوق المقاسة: {sc['probability_state']}")
    if sc.get("event"):
        e = sc["event"]
        L.append(f"  ⚠ حدث خلال {e['hours']:g} ساعة: {e['name']} [{e['currency']}]")
    if not sc.get("scenarios"):
        L.append(f"  {sc.get('note') or 'لا مستويات كافية لبناء سيناريو.'}")
        return "\n".join(L)
    heads = {"breakout": "اختراق صعود", "breakdown": "كسر هبوط", "deeper": "هبوط أعمق"}
    for s in sc["scenarios"]:
        arrow = "🟢" if s["side"] == "long" else "🔴"
        rr = f" · R:R {s['rr']}" if s.get("rr") else ""
        L.append(f"  {arrow} {heads.get(s['kind'], s['kind'])} — يتفعّل عند "
                 f"{_n(s['trigger'])} (تأكيد {_n(s['confirm'])}) [{s['frame']}]")
        L.append(f"      الوقف {_n(s['stop'])} ({s['risk']:,.0f} نقطة){rr} · الهدف "
                 + "، ".join(_n(t) for t in s["targets"]))
        prob = s.get("probability")
        if prob and show_study:
            if prob.get("sufficient"):
                L.append(f"      🎲 احتمال مقاس: {prob['p']:.0%} من {prob['n']} حالة · "
                         f"توقع {prob['mean_r']:+.2f}R")
            else:
                L.append(f"      🎲 عيّنة الجانب صغيرة: n={prob['n']} — بلا احتمال.")
        if s.get("invalidation") is not None:
            L.append(f"      ❌ يُبطَل عند {_n(s['invalidation'])}")
        sz = s.get("sizing")
        if sz is not None:
            if sz.feasible:
                money = (f"💵 {sz.lots:g} لوت · تخاطر ${sz.risk_money:,.2f} "
                         f"({sz.risk_pct_actual:.1f}%)")
                if sz.reward_money is not None:
                    money += f" · تربح ${sz.reward_money:,.2f}"
                L.append(f"      {money}")
            else:
                L.append(f"      🚫 ما بتنفع لهالحساب — أصغر حجم ({sz.min_lots:g}) "
                         f"يخاطر ${sz.min_risk_money:,.2f} = {sz.min_risk_pct:.1f}%")
    L.append("  (سيناريوهات مشروطة على مستويات مطبوعة — مو توصية.)")
    return "\n".join(L)


def _study_summary(s, *, show_study: bool) -> str:
    """An agent's summary, with the ten-year study kept off the live card.

    Only the analog agent's summary is written from the study: it is a frequency
    over ten years ("this state occurred 321 times; long expectancy +0.00R"). Its
    verdict is a decision and stays; the live part -- the state label, computed
    from the live bars -- stays too. The historical counts and expectancies do
    not, per the owner's standing rule. `DESK_SHOW_STUDY=1` restores the text.
    """
    if show_study or s.agent != "analog":
        return s.summary
    state = (s.payload or {}).get("state")
    if not state:
        return "قراءة حالة السوق (بلا إحصاءات الدراسة)."
    return (f"حالة السوق الآن: {state} — "
            f"(إحصاءات الدراسة خارج القراءة الحيّة؛ تظهر في أمر desk acceptance).")


def render(run: Run, lang: str = "ar", *, show_study: bool = False) -> str:
    """The card, ordered for a decision: what to do first, why after.

    A trader reads the top two lines and acts; the specialist detail is evidence
    for that decision, so it sits below. The live call is built from live output
    only -- the ten-year study's statistics stay off this card unless
    `show_study` is set (owner's standing rule).
    """
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
    llm = r.get("llm") or {}
    if llm and not llm.get("available", True):
        L.append("⚠ النموذج غير متاح — النصوص أدناه حتمية، محسوبة لا مكتوبة.")
    elif llm.get("rotations"):
        L.append(f"↻ النموذج: {llm.get('model')} "
                 f"(دار {llm['rotations']} مرة بعد نفاد حصة موديل).")

    # ---- 1) the decision ---------------------------------------------------
    L.append("")
    L.append("— القرار —")
    L.append(f"  الاتجاه: {ar.verdict(run.verdict)}  (نتيجة مرجّحة {run.score:+.2f})")
    L.append(f"  مسموح بالتداول: {'نعم' if r.get('allowed') else 'لا'}")
    bias = r.get("bias") or {}
    if bias.get("direction"):
        L.append(f"  الميل الضمني: {ar.direction(bias['direction'])} ({bias.get('score'):+.3f})")
    pfb = r.get("per_frame_bias") or {}
    if pfb:
        order = ["1d", "4h", "1h", "30m", "15m", "5m", "1m"]
        cells = [f"{tf}:{pfb[tf]:+.1f}" for tf in order if tf in pfb]
        L.append(f"  الميل حسب الفريم: {' · '.join(cells)}")
    # The score is a lean, not a probability. Say what it actually delivered:
    # measured over ten years a nominal 92% was right 55.6% of the time, and a
    # trader who sizes on the nominal number is mis-sizing.
    if show_study:
        try:
            from . import calibration as cal
            rel = cal.reliability(run.symbol, run.score)
        except Exception:
            rel = None
        if rel:
            real, n = rel
            nominal = 0.5 + 0.5 * min(1.0, abs(run.score))
            L.append(f"  ثقة مُعايَرة: {real:.0%} فعلياً (على {n} حالة مشابهة) — "
                     f"بينما الثقة الاسمية {nominal:.0%}")

    # ---- 2) the plan, when there is one ------------------------------------
    if r.get("entry") is not None:
        L.append("")
        L.append("— الخطة —")
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

    # ---- 3) the scenarios: the other half of the machine -------------------
    # Not "where is it going" but "what has to happen, and what proves it wrong".
    # Rendered from the same object `/scenarios` prints, so card and button agree.
    if r.get("scenarios"):
        L.append("")
        L.append(_scenarios_plain(r["scenarios"], show_study=show_study))

    # ---- 4) the specialists, as evidence for the decision above -----------
    L.append("")
    L.append("— دراسات الوكلاء —")
    for s in sorted(run.studies, key=lambda x: x.agent):
        L.append(f"  [{ar.agent(s.agent)}] {ar.stance(s.stance)} "
                 f"ثقة {s.confidence:.2f} · نتيجة {s.score:+.2f}")
        L.append(f"               {_study_summary(s, show_study=show_study)}")

    if r.get("warnings"):
        L.append(f"  ⚠ تحذيرات: {'; '.join(r['warnings'])}")
    if r.get("errors"):
        L.append(f"  ⚠ أخطاء المصدر: {'; '.join(r['errors'])}")
    active_line = r.get("active_plan_line")
    if active_line:
        L.append(active_line)
    L.append(f"  (المدة {run.duration_s:.1f} ثانية · المعرّف {run.run_id})")
    return "\n".join(L)
