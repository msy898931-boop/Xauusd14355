"""Arabic — one place for every term the desk prints to a person.

The chat is Arabic-only. Terms arrive from three directions: the desk's own
enums (`stance`, `verdict`, `kind`), the feed's vocabulary (`marketState`,
regime names), and the model's prose. The first two are fixed sets, so they are
translated here by lookup rather than by the model -- a model that renames
"bear" to something of its own makes the card inconsistent between runs, and a
missing term would reach the reader as an English word.

`t()` is total: an unknown key is returned unchanged, never raised. A new enum
value must show up in the chat (as English) rather than crash the run that
prints it.
"""

from __future__ import annotations

# ---- the desk's own enums ---------------------------------------------
STANCE = {"bull": "صاعد", "bear": "هابط", "neutral": "محايد"}
VERDICT = {"long": "شراء", "short": "بيع", "no-trade": "لا صفقة"}
DIRECTION = {"up": "صاعد", "down": "هابط", "flat": "محايد"}

# A level's role in the plan, as the orchestrator labels it.
KIND = {
    "support": "دعم",
    "resistance": "مقاومة",
    "fib": "فيبوناتشي",
    "projection": "هدف تقديري",
    "round": "رقم مستدير",
    "day_high": "قمة اليوم",
    "day_low": "قاع اليوم",
    "prior_high": "قمة سابقة",
    "prior_low": "قاع سابق",
    "breakout": "اختراق",
    "level": "مستوى",
}

# ---- the feed's vocabulary --------------------------------------------
MARKET_STATE = {
    "open": "مفتوح",
    "closed": "مغلق",
    "pre": "قبل الافتتاح",
    "premarket": "قبل الافتتاح",
    "post": "بعد الإغلاق",
    "postmarket": "بعد الإغلاق",
    "regular": "عادي",
    "halted": "موقوف",
}

REGIME = {
    "normal": "عادي",
    "low": "منخفض",
    "high": "مرتفع",
    "calm": "هادئ",
    "volatile": "متذبذب",
    "trending": "متجه",
    "ranging": "عرضي",
    "quiet": "هادئ",
    "expansion": "توسّع",
    "contraction": "انكماش",
    # The volatility agent falls back to this when ATR is unreadable (a short
    # history). Without the key the word "unknown" reached the chat.
    "unknown": "غير معروف",
}

# ---- the specialist roster --------------------------------------------
# The agent name is a key, so it is also shown in Arabic in the card. The Latin
# name is kept in the payload for the logs, never for the reader.
AGENT = {
    "structure": "البنية",
    "trend": "الاتجاه",
    "momentum": "الزخم",
    "levels": "المستويات",
    "volatility": "التذبذب",
    "news": "الأخبار",
    "surprise": "المفاجآت",
    "correlation": "الارتباط",
    "risk": "المخاطر",
    "regime": "النظام",
}

# ---- structure / market-structure words -------------------------------
STRUCTURE = {
    "up": "صاعد",
    "down": "هابط",
    "ranging": "عرضي",
    "range": "عرضي",
    "unknown": "غير معروف",
    "bullish": "صاعد",
    "bearish": "هابط",
    "none": "لا شيء",
    "discount": "خصم",
    "premium": "علاوة",
    "equilibrium": "توازن",
}

# The structure agent labels the break with the word "BOS" in the value
# ("bearish BOS"), so the table keys on that exact string, not on the bare
# direction. Keying on the wrong form is how "bearish BOS" reached the chat.
BOS = {
    "bullish BOS": "اختراق صاعد",
    "bearish BOS": "كسر هابط",
    "bullish": "اختراق صاعد",
    "bearish": "كسر هابط",
    "none": "لا يوجد",
}

# What the correlation agent calls the macro driver, and the board's tone.
DRIVER = {
    "dollar": "الدولار",
    "usd": "الدولار",
    "unclear": "غير واضح",
    "gold-led (weak dollar)": "الذهب يقود (دولار ضعيف)",
    "dollar-led (gold pressured)": "الدولار يقود (ضغط على الذهب)",
    "gold-led": "الذهب يقود",
    "dollar-led": "الدولار يقود",
    "real_yields": "العوائد الحقيقية",
    "yields": "العوائد",
    "risk": "المخاطر",
    "risk_on": "إقبال على المخاطرة",
    "risk_off": "عزوف عن المخاطرة",
    "rates": "الفوائد",
    "mixed": "مختلط",
    "none": "لا محرّك واضح",
    "unknown": "غير معروف",
}

TONE = {
    "risk-on": "إقبال على المخاطرة",
    "risk-off": "عزوف عن المخاطرة",
    "risk_on": "إقبال على المخاطرة",
    "risk_off": "عزوف عن المخاطرة",
    "mixed": "مختلط",
    "bullish": "صاعد",
    "bearish": "هابط",
    "neutral": "محايد",
    "flat": "محايد",
}


def t(value, table: dict[str, str]) -> str:
    """Translate `value` through `table`; return it unchanged if unknown.

    Total by design: an enum value this module has not learned yet must reach
    the reader as-is, not raise inside a render.
    """
    if value is None:
        return "—"
    key = str(value)
    return table.get(key, key)


def stance(value) -> str:
    return t(value, STANCE)


def verdict(value) -> str:
    return t(value, VERDICT)


def direction(value) -> str:
    return t(value, DIRECTION)


def kind(value) -> str:
    return t(value, KIND)


def market_state(value) -> str:
    return t(value, MARKET_STATE)


def regime(value) -> str:
    return t(value, REGIME)


def agent(value) -> str:
    return t(value, AGENT)


def structure(value) -> str:
    return t(value, STRUCTURE)


def bos(value) -> str:
    return t(value, BOS)


# Economic-calendar event names, from the feed's English. The feed is a
# third-party source, so an unknown name is left as-is (a proper noun the reader
# can look up); these are the ones common enough to be worth translating.
EVENT = {
    "JOLTS Job Openings": "فرص العمل (JOLTS)",
    "Non-Farm Payrolls": "التوظيف غير الزراعي",
    "Nonfarm Payrolls": "التوظيف غير الزراعي",
    "ADP Employment Change": "تغيّر التوظيف (ADP)",
    "Unemployment Rate": "معدل البطالة",
    "Average Hourly Earnings": "متوسط الأجر بالساعة",
    "CPI": "التضخم (CPI)",
    "Core CPI": "التضخم الأساسي (CPI)",
    "PPI": "أسعار المنتجين (PPI)",
    "Core PCE Price Index": "التضخم الأساسي (PCE)",
    "PCE Price Index": "التضخم (PCE)",
    "FOMC Statement": "بيان الفيدرالي",
    "FOMC Meeting Minutes": "محضر الفيدرالي",
    "Fed Interest Rate Decision": "قرار الفائدة الفيدرالي",
    "Federal Funds Rate": "سعر الفائدة الفيدرالي",
    "Retail Sales": "مبيعات التجزئة",
    "GDP": "الناتج المحلي (GDP)",
    "ISM Manufacturing PMI": "مؤشر ISM الصناعي",
    "ISM Services PMI": "مؤشر ISM للخدمات",
    "Prelim UoM Consumer Sentiment": "ثقة المستهلك (ميشيغان)",
    "CB Consumer Confidence": "ثقة المستهلك",
    "Initial Jobless Claims": "إعانات البطالة الأسبوعية",
    "Crude Oil Inventories": "مخزون النفط",
    "ECB Interest Rate Decision": "قرار الفائدة الأوروبي",
    "BOE Interest Rate Decision": "قرار الفائدة البريطاني",
    "RBA Interest Rate Decision": "قرار الفائدة الأسترالي",
    "BOJ Interest Rate Decision": "قرار الفائدة الياباني",
    "SNB Interest Rate Decision": "قرار الفائدة السويسري",
    "BOC Interest Rate Decision": "قرار الفائدة الكندي",
}


def event_name(name) -> str:
    """An event's Arabic name, or the feed's own name when it is not mapped."""
    if not name:
        return "حدث"
    return EVENT.get(str(name).strip(), str(name))


def listing(items, empty: str = "لا شيء", limit: int = 3) -> str:
    """An Arabic list: `أ، ب، ج`, capped at `limit`, with `empty` when bare.

    Arabic lists read with a comma; there is no Oxford "and" to place. A list
    longer than `limit` is trimmed with a count, so the line stays one line.
    """
    items = [str(i) for i in items if i not in (None, "")]
    if not items:
        return empty
    if len(items) <= limit:
        return "، ".join(items)
    return "، ".join(items[:limit]) + f" (+{len(items) - limit})"
