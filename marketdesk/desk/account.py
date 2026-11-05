"""Position sizing: turn a plan's price levels into a lot size and a dollar risk.

The desk states levels in points. A trader with a real balance needs the other
half: how many lots, and what a stop-out costs in money. Those two are not the
same question, and getting the second wrong is how a small account dies.

The arithmetic is standard and small:

    risk_budget = balance * risk_pct
    lots        = risk_budget / (stop_distance * contract_size)

where `contract_size` is the ounces one lot controls. For XAUUSD that is 100, so
a $1 move is $100 per lot -- the figure every broker specification agrees on.

Three things this module refuses to do, all because a $200 account is where they
bite hardest:

* **It never rounds a size UP.** Rounding 0.006 lots to 0.01 silently risks more
  than the budget. The size is floored to the broker's lot step, and the actual
  risk is reported from the size that will really be traded, not from the budget
  that was wanted.
* **It says when the plan cannot be sized at all.** On a small account the
  minimum lot is often wider than the whole risk budget: a 42-point gold stop on
  a $200 balance at 2% wants 0.0095 lots, and the smallest the broker will take
  (0.01) risks 21% of the account. The honest answer is "this stop is too wide
  for this account", not a size that looks fine and is not.
* **It refuses a stop outside the trader's own tolerance** -- wider than the cap
  they set, or tighter than the floor (a stop inside the noise is not a stop).
  A stop the trader will not honour is not a stop.

It also answers the question a risk percentage hides: **how many losses in a row
before the account is unusable?** `ruin_risk` is the probability of that run
under the desk's own measured hit rate. It is deliberately a *sequence* model,
not a closed-form gambler's ruin -- see the function for why.
"""

from __future__ import annotations

from dataclasses import dataclass

# One lot of XAUUSD controls 100 troy ounces, so a $1 move is $100 per lot.
DEFAULT_CONTRACT_SIZE = 100.0
# The smallest volume most retail brokers accept, and the step between sizes.
DEFAULT_LOT_STEP = 0.01
# A typical gold spread, in price points. Used only to state the cost of a trade;
# never to size it. An estimate, and labelled as one.
DEFAULT_SPREAD = 0.25


@dataclass
class Sizing:
    """What a plan costs to hold, in money, for one account and risk setting."""

    balance: float
    risk_pct: float
    entry: float
    stop: float
    target: float | None
    lots: float
    risk_money: float          # the loss if the stop is hit, at `lots`
    reward_money: float | None  # the gain if the target is hit, at `lots`
    feasible: bool             # False when the setup cannot be taken as asked
    min_lots: float
    min_risk_money: float      # the loss at the minimum lot
    cost_money: float          # round-trip spread cost at `lots`
    note: str
    # Why it is infeasible, so a caller can say the right thing rather than
    # pattern-match the prose: "zero" | "too_wide" | "too_tight" | "money" | "".
    reason: str = ""

    @property
    def risk_pct_actual(self) -> float:
        return self.risk_money / self.balance * 100 if self.balance else 0.0

    @property
    def min_risk_pct(self) -> float:
        return self.min_risk_money / self.balance * 100 if self.balance else 0.0

    @property
    def stop_points(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def notional(self) -> float:
        """The face value of the position, for stating leverage, not for sizing."""
        return self.lots * DEFAULT_CONTRACT_SIZE * self.entry


def size_plan(*, balance: float, risk_pct: float, entry: float, stop: float,
              target: float | None = None, contract_size: float = DEFAULT_CONTRACT_SIZE,
              lot_step: float = DEFAULT_LOT_STEP, spread: float = DEFAULT_SPREAD,
              max_lots: float | None = None, min_stop_points: float = 0.0,
              max_stop_points: float = 0.0) -> Sizing:
    """The lot size for `stop` at `risk_pct` of `balance`, floored to `lot_step`.

    `min_stop_points`/`max_stop_points` are the trader's own stop tolerance
    (0 = no bound). A setup outside it is refused whatever the money says: a stop
    the trader will not honour is not a stop, and a stop inside the noise is not
    one either.
    """
    stop_dist = abs(entry - stop)
    budget = balance * risk_pct / 100.0
    min_lots = lot_step

    def _refuse(reason: str, note: str, min_risk: float) -> Sizing:
        return Sizing(balance, risk_pct, entry, stop, target, 0.0, 0.0, None,
                      False, min_lots, min_risk, 0.0, note, reason)

    if stop_dist <= 0 or budget <= 0:
        return _refuse("zero", "الوقف مساوٍ للدخول — ما في مسافة نسعّر عليها.", 0.0)
    if max_stop_points and stop_dist > max_stop_points:
        return _refuse("too_wide",
                       f"الوقف {stop_dist:.0f} نقطة أوسع من سقفك "
                       f"({max_stop_points:g} نقطة).",
                       min_lots * stop_dist * contract_size)
    if min_stop_points and stop_dist < min_stop_points:
        return _refuse("too_tight",
                       f"الوقف {stop_dist:.0f} نقطة أضيق من حدك الأدنى "
                       f"({min_stop_points:g} نقطة) — بيصير داخل ضجيج السوق.",
                       min_lots * stop_dist * contract_size)

    # lots that would spend exactly the budget, then floored to the broker step.
    raw_lots = budget / (stop_dist * contract_size)
    lots = (int(raw_lots / lot_step + 1e-9)) * lot_step
    if max_lots is not None:
        lots = min(lots, max_lots)

    min_risk_money = min_lots * stop_dist * contract_size
    if lots < min_lots:
        return _refuse("money",
                       f"أصغر حجم ({min_lots:g} لوت) يخاطر ${min_risk_money:,.2f} "
                       f"= {min_risk_money / balance * 100:.1f}% من الحساب — "
                       f"أكثر من ميزانية {risk_pct:.1f}%.",
                       min_risk_money)

    risk_money = lots * stop_dist * contract_size
    reward_money = (abs(target - entry) * lots * contract_size
                    if target is not None else None)
    cost_money = spread * lots * contract_size
    return Sizing(balance, risk_pct, entry, stop, target, round(lots, 4),
                  risk_money, reward_money, True, min_lots, min_risk_money,
                  cost_money, "")


def render(s: Sizing, symbol: str = "XAUUSD", direction: str = "") -> str:
    """The money view of a plan, Telegram-ready. Arabic, like the rest."""
    side = {"long": "شراء", "short": "بيع"}.get(direction, "")
    L = [f"💵 <b>حجم الصفقة</b> — {symbol}" + (f" · {side}" if side else "")]
    L.append(f"الحساب <b>${s.balance:,.2f}</b> · المخاطرة {s.risk_pct:.1f}% "
             f"= <b>${s.balance * s.risk_pct / 100:,.2f}</b>")
    L.append(f"الدخول {s.entry:,.2f} · الوقف {s.stop:,.2f} "
             f"({s.stop_points:.2f}$ = {s.stop_points:.0f} نقطة)")
    if not s.feasible:
        if s.reason == "too_wide":
            L.append(f"🚫 <b>ما بتنفع.</b> {s.note}")
            L.append("   يعني السيتاب تذبذبه أوسع من تحمّلك — إما تقبل وقف أوسع، "
                     "أو ننطر سيتاب أنظف.")
        elif s.reason == "too_tight":
            L.append(f"🚫 <b>ما بتنفع.</b> {s.note}")
            L.append("   وقف أضيق من هيك بينضرب بالضجيج العادي.")
        else:
            L.append(f"🚫 <b>ما بتنفع لهالحساب.</b> {s.note}")
            L.append(f"   يعني لو دخلت أصغر حجم ({s.min_lots:g}) رح تخاطر "
                     f"<b>${s.min_risk_money:,.2f}</b> = <b>{s.min_risk_pct:.1f}%</b>.")
            L.append("   الخيار: وسّع الحساب، أو قلّل المخاطرة %, أو ننطر صفقة "
                     "بوقف أضيق.")
        return "\n".join(L)
    L.append(f"الحجم: <b>{s.lots:g} لوت</b> "
             f"({s.lots * DEFAULT_CONTRACT_SIZE:.0f} أونصة · قيمة اسمية "
             f"${s.notional:,.0f})")
    L.append(f"خسارة لو ضرب الوقف: <b>${s.risk_money:,.2f}</b> "
             f"({s.risk_pct_actual:.1f}% من الحساب)")
    if s.reward_money is not None and s.target is not None:
        rr = (abs(s.target - s.entry) / s.stop_points if s.stop_points else None)
        L.append(f"ربح لو وصل الهدف: <b>${s.reward_money:,.2f}</b>"
                 + (f" · R:R {rr:.2f}" if rr else ""))
    L.append(f"تكلفة السبريد (تقديرية): ${s.cost_money:,.2f}")
    if s.lots == s.min_lots:
        L.append("<i>ملاحظة: هاد أصغر حجم متاح — المخاطرة الفعلية "
                 f"{s.risk_pct_actual:.1f}% مو {s.risk_pct:.1f}%.</i>")
    return "\n".join(L)


@dataclass
class Ruin:
    """The chance of a losing run the account cannot recover from."""

    risk_pct: float          # the per-trade loss, as a fraction of the balance
    hit_rate: float          # the desk's own measured win rate for this setup
    drawdown_frac: float     # the loss fraction that counts as "ruined" (e.g. 0.5)
    n_trades: int            # how many trades the sequence was run over
    p_ruin: float            # probability of touching the ruin level at least once
    losses_to_ruin: int      # consecutive losses that reach the level
    sample: int              # how many sequences were simulated

    @property
    def safe(self) -> bool:
        return self.p_ruin < 0.05


def ruin_risk(*, risk_pct: float, hit_rate: float, drawdown_frac: float = 0.5,
              n_trades: int = 200, sample: int = 20000,
              seed: int = 11) -> Ruin:
    """Probability that a run of trades ever cuts the account by `drawdown_frac`.

    WHY A SEQUENCE MODEL, NOT gambler's ruin. The textbook closed form assumes
    every trade risks the same fraction of the *current* balance and wins a fixed
    multiple -- a random walk in log-space. The desk's own numbers are not that:
    a stop is a fixed money loss (0.5 x ATR), the win is a fixed R multiple, and
    the desk trades a fixed size, so the loss fraction of the balance is what
    moves. Modelling that with the closed form would be a tidy number for the
    wrong process.

    So this runs the process: `sample` sequences of `n_trades`, each trade a win
    at the desk's measured hit rate, and the balance moved by a FIXED dollar
    amount -- `risk_pct` of the starting balance lost, twice that won. Fixed
    money, not a fixed fraction of a shrinking balance, because that is what the
    desk does: it trades a fixed lot size, so a loss is a constant number of
    dollars and it therefore eats a growing share of a shrinking account. That is
    the more dangerous reading, and the honest one.

    The result is a frequency, and the sample is reported beside it so nobody
    reads three significant figures off 40 paths.

    `hit_rate` is the desk's measured win rate for this bracket, not an assumed
    55%. If the desk has no measured edge, the honest input is its measured rate
    and the ruin number that follows is the honest one.
    """
    import random

    rng = random.Random(seed)
    dd = max(0.05, min(0.95, drawdown_frac))
    risk_pct = max(0.0, min(0.99, risk_pct))
    # A 2R win against a 1R loss is the desk's bracket; keep the ratio here so
    # the number matches the plans the desk actually prints.
    win_r, loss_r = 2.0, 1.0
    p = max(0.0, min(1.0, hit_rate))
    loss_frac = risk_pct * loss_r          # of the STARTING balance, fixed money
    win_frac = risk_pct * win_r
    ruined = 0
    for _ in range(sample):
        bal = 1.0
        for _t in range(n_trades):
            bal += win_frac if rng.random() < p else -loss_frac
            if bal <= 1.0 - dd:
                ruined += 1
                break
    # With a fixed dollar loss the level is reached after a plain number of
    # losses in a row: no compounding, so no logarithm.
    from math import ceil
    n_consec = (max(1, ceil(dd / risk_pct - 1e-9)) if risk_pct > 0
                else n_trades + 1)
    return Ruin(risk_pct=risk_pct, hit_rate=p, drawdown_frac=dd,
                n_trades=n_trades, p_ruin=ruined / sample,
                losses_to_ruin=n_consec, sample=sample)


def ruin_render(r: Ruin) -> str:
    """The ruin view, Arabic, honest about the sample behind it."""
    L = [f"🎲 <b>خطر الخراب</b> — مخاطرة {r.risk_pct:.1%} للصفقة · "
         f"معدل فوز مقاس {r.hit_rate:.0%}"]
    L.append(f"  احتمال ما ينزل الحساب {r.drawdown_frac:.0%} خلال "
             f"{r.n_trades} صفقة: <b>{r.p_ruin:.2%}</b>")
    L.append(f"  {r.losses_to_ruin} خسارة متتالية تكفي للوصول لهالمستوى.")
    L.append(f"  <i>محاكاة على {r.sample:,} مسار — الرقم تكرار، مو معادلة.</i>")
    if not r.safe:
        L.append("  ⚠ <b>خطر مرتفع.</b> قلّل المخاطرة %، أو اشتغل بحساب أكبر.")
    return "\n".join(L)
