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
    Level(3931.0, "كسر 4 ساعات صعود", "above",
          "الصعود تأكد — الطريق لـ3,946 ثم 3,964"),
    Level(3912.0, "وقف البيع", "above",
          "إغلاق فوقه = إلغاء البيع فوراً — اطلع"),
    Level(3897.0, "قاع اليوم", "below",
          "كسر القاع — سيناريو الهبوط يتفعّل، الطريق لـ3,870"),
    Level(3870.0, "القاع الكبير", "below", "هبوط أعمق — الطريق لـ3,850 ثم 3,820"),
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


def approaching(live: float, levels=DEFAULT_LEVELS, *,
                within: float = 12.0) -> list[tuple[Level, float]]:
    """Levels the price is near but has NOT broken yet, nearest first.

    `triggered` answers "which side is decided". This answers the earlier
    question -- "which one is about to be decided" -- which is the one a trader
    actually wants a warning about. A level already crossed is excluded: that is
    `triggered`'s job, and reporting it here would double up.

    `within` is in price units, so it must be set to the instrument's noise. On
    gold a 12-point band is roughly a fifth of a daily ATR: close enough to be
    worth a look, far enough not to fire on every tick.
    """
    out: list[tuple[Level, float]] = []
    for lv in levels:
        dist = abs(live - lv.price)
        if dist > within:
            continue
        crossed = (lv.side == "above" and live >= lv.price) or \
                  (lv.side == "below" and live <= lv.price)
        if not crossed:
            out.append((lv, dist))
    return sorted(out, key=lambda t: t[1])


def fingerprint(lv: Level) -> str:
    return f"level:{lv.price:g}:{lv.side}"


def near_fingerprint(lv: Level) -> str:
    """A separate ledger key, so "near" and "broken" are two signals, not one.

    The level fires twice in a real move: once when price comes close and once
    when it breaks. Keying them apart is what lets both be sent once, and stops
    a re-approach from re-firing.
    """
    return f"near:{lv.price:g}:{lv.side}"


def message(lv: Level, live: float) -> str:
    arrow = "▲" if lv.side == "above" else "▼"
    verb = "اخترق" if lv.side == "above" else "كسر"
    return (f"{arrow} <b>تنبيه مستوى</b>\n"
            f"السعر {live:,.2f} {verb} <b>{lv.price:,.2f}</b> ({lv.label})\n"
            f"{lv.note}")


def near_message(lv: Level, live: float, dist: float) -> str:
    """The warning, phrased as a warning -- it is not a break yet."""
    arrow = "▲" if lv.side == "above" else "▼"
    side = "فوقه" if lv.side == "above" else "تحته"
    return (f"🔔 <b>اقتراب من مستوى</b>\n"
            f"السعر {live:,.2f} يقترب من {arrow} <b>{lv.price:,.2f}</b> ({lv.label})\n"
            f"باقي {dist:,.1f} نقطة، والكسر يكون {side}.\n"
            f"<i>لسا ما انكسر — هذا تحذير مبكر.</i>")
