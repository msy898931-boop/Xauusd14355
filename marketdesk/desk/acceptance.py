"""The acceptance criteria of the recommendation-engine plan, checked in code.

The plan document ends with a table of eight criteria ("when do we call this
strong?"). A table in a document is a promise; this module turns each row into a
computation against the shipped history, so the desk can say which it meets and
which it does not -- in the same run, without a human reading a card and
guessing.

Two of the eight are not data checks:

* "one machine" is a structural claim -- `/plan` and `/recommend` must read one
  measurement. It is checked by building the plan and the recommendation's
  evidence line and requiring their numbers to be identical.
* "quality" is the test/lint/type gate. It is checked by running the gate as a
  subprocess, and degrades to "not run" when the tools are absent rather than
  claiming a pass it did not see.

Nothing here is a target the desk can move. The criterion is in the plan; the
number is measured. When a criterion fails, this says so -- the walk-forward
criterion is expected to fail on this market, and the plan already says the desk
will admit it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from . import bias as bias_mod
from . import calibration as cal_mod
from . import evidence as ev
from . import history
from . import scenarios as sc_mod
from . import walkforward as wf_mod
from .config import NEUTRAL_BAND


@dataclass(frozen=True)
class Check:
    """One acceptance row: what was asked, what was measured, and the verdict.

    `gate` separates a *structural* criterion -- something the code must get
    right, so a failure is a defect -- from a *market-outcome* criterion, which
    the desk reports honestly and may legitimately fail. The edge and calibration
    rows are outcomes: this market has no timing edge and the desk is not yet
    calibrated, and saying so is the criterion, not a bug. Only gate rows and
    unmeasured (`None`) rows can make the command exit nonzero."""

    key: str
    label: str
    ok: bool | None          # None = could not be measured (not a failure)
    detail: str
    target: str
    gate: bool = True


def _one_machine(C: list[dict], live: float) -> Check:
    """`/plan` and `/recommend` must read the SAME measurement.

    The plan path loads the shipped file through `history.shaped`; the
    recommendation's evidence line loads it through `backtest.load_daily` and
    drops the open bar. Those are two loaders, and they have drifted before --
    that is exactly how a card can claim a record the other card does not have.
    This builds the plan from the real bars and requires its evidence to equal a
    fresh `measure` on `C`, the bars `history` hands out. A second loader that
    returned a different bar set would fail here."""
    from .backtest import load_daily
    from .plan import build_plan

    csv = history.locate_daily("XAUUSD")
    if csv is None:
        return Check("one_machine", "الآلة واحدة — /plan و/recommend نفس المحرك",
                     None, "ملف التاريخ غير موجود", "نفس القياس على نفس الأشرطة")
    rows = load_daily(csv)
    closed_rows = [b for b in rows if not b.get("isOpen")]
    # The recommendation's loader must yield the same closed bars as `history`.
    from_bt = [{"o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"]}
               for b in closed_rows]
    same_bars = from_bt == C
    plan = build_plan(rows, live)
    m = ev.measure(C)
    same_evidence = (plan["long"].evidence == m.long.as_dict()
                     and plan["short"].evidence == m.short.as_dict())
    ok = same_bars and same_evidence
    detail = ("الخطة والتوصية على نفس الأشرطة ونفس القياس" if ok
              else ("مصدران مختلفان للأشرطة" if not same_bars
                    else "دليل الخطة ≠ القياس"))
    return Check(
        "one_machine", "الآلة واحدة — /plan و/recommend نفس المحرك", ok,
        detail, "نفس القياس على نفس الأشرطة")


def _no_stored_numbers(C: list[dict]) -> Check:
    """No hardcoded number may be printed as a fact.

    The plan used to print a fixed `RUNG_REACH` and a fixed `SIDE_EVIDENCE`. Both
    are gone. This checks that the reach the plan prints is the reach the history
    measures -- so a regression that re-introduces a constant fails here."""
    from .plan import build_plan

    rows = [{"openTime": f"d{i}", "open": b["o"], "high": b["h"],
             "low": b["l"], "close": b["c"], "tickVolume": 1, "isOpen": False}
            for i, b in enumerate(C)]
    plan = build_plan(rows, rows[-1]["close"])
    measured = ev.rung_reach(C)
    ok = (plan["long"].reach == measured["long"]["reach"]
          and plan["short"].reach == measured["short"]["reach"])
    return Check(
        "no_stored_numbers", "صفر رقم مكتوب بالكود يُعرض كمعلومة", ok,
        "وصول الأهداف مقاس لكل جهة" if ok else "في رقم مطبوع ما طابق القياس",
        "كل رقم معروض = قياس")


def _balance() -> Check:
    """On ten years, longs and shorts within the plan's own balance rule."""
    b = bias_mod.audit_symbol("XAUUSD")
    return Check(
        "balance", "الانحياز — شراء/بيع متوازنين على 10 سنين", b.balanced,
        b.summary_ar(), "شراء ≥ 30% · بيع ≤ 45%")


def _decision_rate() -> Check:
    """No-trade under 40% of the days (the daily-bar proxy for "active hours").

    The plan's target was < 40%, down from 71% on the old engine. The shipped
    file has no session clock, so the honest available measure is the daily-bar
    share; the label says so rather than pretending it is intraday."""
    b = bias_mod.audit_symbol("XAUUSD")
    ok = b.no_trade_share < 0.40
    return Check(
        "decision_rate", "القرار — أقل من 40% «لا صفقة»", ok,
        f"لا صفقة {b.no_trade_share:.0%} على {b.bars} شمعة يومية (كان 71%)",
        "< 40%")


def _calibration() -> Check:
    """The confidence must mean something: ECE under 10 points."""
    cal = cal_mod.from_symbol("XAUUSD")
    ece = cal.ece
    if ece is None:
        return Check("calibration", "الثقة — معايرة (خطأ < 10 نقاط)", None,
                     "ما في نتائج مُقيَّمة", "خطأ < 10 نقاط")
    return Check(
        "calibration", "الثقة — معايرة (خطأ < 10 نقاط)", cal.calibrated,
        f"خطأ المعايرة {ece * 100:.1f} نقطة على {cal.n} حالة "
        f"({'مُعايَر' if cal.calibrated else 'غير مُعايَر'})",
        "خطأ < 10 نقاط", gate=False)


def _scenarios(C: list[dict], live: float) -> Check:
    """At least three scenarios, each with a probability field and an
    invalidation price.

    Built from the real shipped bars in the frame shape. The shipped file is one
    frame, so the count is what one frame prints; the *contract* -- every
    scenario falsifiable, every scenario carrying the probability object (with
    its sufficiency flag) -- is what must hold whatever the count."""
    frames = {"1d": [{"t": f"d{i}", "o": b["o"], "h": b["h"], "l": b["l"],
                      "c": b["c"], "open": False} for i, b in enumerate(C)]}
    import datetime as dt
    out = sc_mod.build(frames, live, calendar=[],
                       now=dt.datetime(2026, 11, 3, tzinfo=dt.UTC))
    scs = out["scenarios"]
    complete = bool(scs) and all(
        s.get("invalidation") is not None and s.get("probability") is not None
        for s in scs)
    ok = len(scs) >= 3 and complete
    return Check(
        "scenarios", "السيناريو — 3+ سيناريوهات باحتمال + إبطال", ok,
        f"{len(scs)} سيناريو · كل واحد فيه إبطال واحتمال (أو «عيّنة قليلة»)"
        if complete else "سيناريو بلا إبطال أو بلا احتمال",
        "3+ · احتمال + إبطال")


def _edge(C: list[dict]) -> Check:
    """Beats buy-and-hold with t > 2, or admits it does not.

    This one is *allowed to fail* and is expected to: the desk's own ten-year
    study says it has no timing edge. The check is honest about that -- a failure
    here is the desk telling the truth, which is the point of the row ("وإلا
    نعترف بصراحة")."""
    wf = wf_mod.walk_forward(C)
    windows = [f.test for f in wf.folds]
    n, mean, t = wf_mod.paired_vs_long(C, windows)
    ok = t > 2.0
    return Check(
        "edge", "Edge — يغلب «اشتري ومسك» بـ t>2 وإلا نعترف", ok,
        f"مقابل الشراء الدائم {mean:+.3f}R (t={t:+.2f}, n={n}) → "
        + ("تفوّق توقيت معنوي" if ok else "ما في تفوّق — المكتب بيقولها بصراحة"),
        "t > 2", gate=False)


def _quality() -> Check:
    """The test/lint/type gate, run as a subprocess. `None` when tools absent."""
    import sys

    def run(cmd: list[str], timeout: int = 900) -> tuple[bool | None, str]:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None, "not run"
        out = (p.stdout + p.stderr).strip().splitlines()
        return p.returncode == 0, (out[-1] if out else "")

    checks = [
        ("ruff", run([sys.executable, "-m", "ruff", "check", "desk/", "tests/"])),
        ("mypy", run([sys.executable, "-m", "mypy", "desk/"])),
        ("pytest", run([sys.executable, "-m", "pytest", "-q", "tests/"])),
    ]
    ok: bool | None = True
    parts = []
    for name, (good, tail) in checks:
        if good is False:
            ok = False
        elif good is None and ok is True:
            ok = None
        parts.append(f"{name}: {'ok' if good else ('FAIL' if good is False else 'n/a')}"
                     + (f" ({tail})" if good is False else ""))
    return Check("quality", "الجودة — اختبارات + mypy + ruff خضراء", ok,
                 " · ".join(parts), "كلها خضراء")


def run_checks(symbol: str = "XAUUSD", *, quality: bool = True) -> list[Check]:
    """Every acceptance row, measured. `quality=False` skips the subprocess gate
    so a caller that is already inside the test suite does not recurse."""
    C = history.shaped(symbol)
    if len(C) < 200:
        raise ValueError(f"need the shipped long history for {symbol}; run "
                         f"`desk longdata` first")
    live = C[-1]["c"]
    out = [
        _one_machine(C, live),
        _no_stored_numbers(C),
        _balance(),
        _decision_rate(),
        _calibration(),
        _scenarios(C, live),
        _edge(C),
    ]
    if quality:
        out.append(_quality())
    return out


def render(checks: list[Check], symbol: str = "XAUUSD") -> str:
    """The scoreboard. A `None` verdict is printed as «—» (not measured), never
    as a pass. A failed *outcome* row (edge, calibration) is marked «🟠» and
    labelled as an honest market finding rather than a defect."""
    L = [f"معايير القبول — {symbol}", "=" * 60]
    for c in checks:
        if c.ok:
            mark = "✅"
        elif c.ok is None:
            mark = "⚠️"
        elif c.gate:
            mark = "❌"
        else:
            mark = "🟠"
        L.append(f"{mark} {c.label}")
        L.append(f"     المطلوب: {c.target}")
        L.append(f"     المقاس:  {c.detail}")
    met = sum(1 for c in checks if c.ok)
    total = sum(1 for c in checks if c.ok is not None)
    L.append("=" * 60)
    L.append(f"المحقَّق: {met}/{total}"
             + ("" if met == total else
                " — الباقي مكتب بيقولها بصراحة، مو بيخبّيها."))
    failed_gate = [c.key for c in checks if c.gate and c.ok is False]
    if failed_gate:
        L.append(f"❌ بند بنيوي فشل: {', '.join(failed_gate)}")
    unmeasured = [c.key for c in checks if c.ok is None]
    if unmeasured:
        L.append(f"⚠️ ما انقاس: {', '.join(unmeasured)}")
    L.append(f"(النطاق المحايد الحالي {NEUTRAL_BAND:g} · "
             f"{len(history.shaped(symbol))} شمعة يومية)")
    return "\n".join(L)
