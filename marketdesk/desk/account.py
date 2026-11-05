"""Position sizing: turn a plan's price levels into a lot size and a dollar risk.

The desk states levels in points. A trader with a real balance needs the other
half: how many lots, and what a stop-out costs in money. Those two are not the
same question, and getting the second wrong is how a small account dies.

The arithmetic is standard and small:

    risk_budget = balance * risk_pct
    lots        = risk_budget / (stop_distance * contract_size)

where `contract_size` is the ounces one lot controls. For XAUUSD that is 100, so
a $1 move is $100 per lot -- the figure every broker specification agrees on.

Two things this module refuses to do, both because a $200 account is where they
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
    feasible: bool             # False when even the minimum lot over-risks
    min_lots: float
    min_risk_money: float      # the loss at the minimum lot
    cost_money: float          # round-trip spread cost at `lots`
    note: str

    @property
    def risk_pct_actual(self) -> float:
        return self.risk_money / self.balance * 100 if self.balance else 0.0

    @property
    def min_risk_pct(self) -> float:
        return self.min_risk_money / self.balance * 100 if self.balance else 0.0

    @property
    def notional(self) -> float:
        """The face value of the position, for stating leverage, not for sizing."""
        return self.lots * DEFAULT_CONTRACT_SIZE * self.entry


def size_plan(*, balance: float, risk_pct: float, entry: float, stop: float,
              target: float | None = None, contract_size: float = DEFAULT_CONTRACT_SIZE,
              lot_step: float = DEFAULT_LOT_STEP, spread: float = DEFAULT_SPREAD,
              max_lots: float | None = None,
              max_stop_points: float = 0.0) -> Sizing:
    """The lot size for `stop` at `risk_pct` of `balance`, floored to `lot_step`.

    `max_stop_points` is a hard tolerance on the stop distance (0 = no cap). A
    stop wider than the owner allows is refused whatever the money says: a setup
    whose stop the trader will not honour is not a setup.
    """
    stop_dist = abs(entry - stop)
    budget = balance * risk_pct / 100.0
    min_lots = lot_step

    if stop_dist <= 0 or budget <= 0:
        return Sizing(balance, risk_pct, entry, stop, target, 0.0, 0.0, None,
                      False, min_lots, 0.0, 0.0,
                      "الوقف مساوٍ للدخول — ما في مسافة نسعّر عليها.")
    if max_stop_points and stop_dist > max_stop_points:
        return Sizing(balance, risk_pct, entry, stop, target, 0.0, 0.0, None,
                      False, min_lots, min_lots * stop_dist * contract_size, 0.0,
                      f"الوقف {stop_dist:.0f} نقطة أوسع من سقفك "
                      f"({max_stop_points:g} نقطة) — المكتب بيرفضه مهما كان الحجم.")

    # lots that would spend exactly the budget, then floored to the broker step.
    raw_lots = budget / (stop_dist * contract_size)
    lots = (int(raw_lots / lot_step + 1e-9)) * lot_step
    if max_lots is not None:
        lots = min(lots, max_lots)

    min_risk_money = min_lots * stop_dist * contract_size
    if lots < min_lots:
        return Sizing(balance, risk_pct, entry, stop, target, 0.0, 0.0, None,
                      False, min_lots, min_risk_money, 0.0,
                      f"أصغر حجم ({min_lots:g} لوت) يخاطر ${min_risk_money:,.2f} "
                      f"= {min_risk_money / balance * 100:.1f}% من الحساب — "
                      f"أكثر من ميزانية {risk_pct:.1f}%. الوقف واسع كتير لهالحساب.")

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
             f"({abs(s.entry - s.stop):.2f}$ = {abs(s.entry - s.stop):.0f} نقطة)")
    if not s.feasible:
        if "سقفك" in s.note:
            L.append(f"🚫 <b>ما بتنفع.</b> {s.note}")
            L.append(f"   يعني حتى بأصغر حجم ({s.min_lots:g}) الوقف بعيد كتير عن "
                     f"الدخول. ننطر سيتاب وقفه أضيق.")
            return "\n".join(L)
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
    if s.reward_money is not None:
        rr = (abs(s.target - s.entry) / abs(s.entry - s.stop)
              if s.target is not None and abs(s.entry - s.stop) else None)
        L.append(f"ربح لو وصل الهدف: <b>${s.reward_money:,.2f}</b>"
                 + (f" · R:R {rr:.2f}" if rr else ""))
    L.append(f"تكلفة السبريد (تقديرية): ${s.cost_money:,.2f}")
    if s.lots == s.min_lots:
        L.append("<i>ملاحظة: هاد أصغر حجم متاح — المخاطرة الفعلية "
                 f"{s.risk_pct_actual:.1f}% مو {s.risk_pct:.1f}%.</i>")
    return "\n".join(L)
