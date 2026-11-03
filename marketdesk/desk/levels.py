"""Watch specific price levels and alert once when each is crossed.

The desk alerts on its own verdict. This alerts on the levels a plan is built
around, which is a different question: the orchestrator can say "no trade" for
weeks while the price sits between a trigger and a stop, and the trader still
wants to know the moment one side is decided.

A level fires once. Crossing back and forth is noise, not two signals, so the
ledger keys on the level and the side it broke -- the same discipline the desk
uses for its own alerts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Level:
    price: float
    label: str
    side: str          # "above" or "below": the side that counts as a break
    note: str


# The levels the live plan is built around. Edit this list as the plan changes;
# nothing else needs to move.
DEFAULT_LEVELS = (
    Level(3970.0, "قمة اليوم", "above", "سيناريو الصعود يتأكد — الطريق لـ4,025"),
    Level(3930.0, "استرجاع متوسط الساعة", "above",
          "الصعود بدأ — إلغاء فكرة البيع"),
    Level(3896.0, "قاع الساعة", "below", "سيناريو الهبوط يتفعّل — الطريق لـ3,870"),
    Level(3870.0, "القاع الكبير", "below", "هبوط أعمق — الطريق لـ3,820"),
)


def triggered(live: float, levels=DEFAULT_LEVELS) -> list[Level]:
    """The levels the live price is currently on the breaking side of."""
    out = []
    for lv in levels:
        if lv.side == "above" and live >= lv.price:
            out.append(lv)
        elif lv.side == "below" and live <= lv.price:
            out.append(lv)
    return out


def fingerprint(lv: Level) -> str:
    return f"level:{lv.price:g}:{lv.side}"


def message(lv: Level, live: float) -> str:
    arrow = "▲" if lv.side == "above" else "▼"
    verb = "اخترق" if lv.side == "above" else "كسر"
    return (f"{arrow} <b>تنبيه مستوى</b>\n"
            f"السعر {live:,.2f} {verb} <b>{lv.price:,.2f}</b> ({lv.label})\n"
            f"{lv.note}")
