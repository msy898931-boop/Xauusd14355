"""Build the XAUUSD levels map (interactive HTML) from live desk data.

Every level here is computed by the desk itself -- `map.build` for the zones and
liquidity, `scenarios.build` for the conditional setups -- from the closed frames
in shared memory. Nothing is typed in by hand: a hardcoded level rots the moment
the price moves, which is exactly what the first version of this file did (it
still showed the 4,052-4,083 shelf from when price was 4,055, long after price
had run to 4,081).

Run `python3 -m desk.cli gather` first so shared memory holds fresh closed bars.
"""
import argparse
import json
import sys

ROOT = __import__("pathlib").Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "marketdesk"))

from desk import map as MP          # noqa: E402
from desk import scenarios as SC    # noqa: E402
from desk.cli import _settings      # noqa: E402
from desk.orchestrator import Orchestrator  # noqa: E402

OUT_JSON = str(ROOT / "chart_data.json")
FRAMES = tuple(dict.fromkeys(MP.MAP_FRAMES + SC.FRAMES))


def main() -> int:
    desk = Orchestrator(_settings(argparse.Namespace()))
    quote = desk.shared.load_quote("XAUUSD")
    if not quote:
        print("no quote in shared memory -- run `desk gather` first")
        return 1
    live = float(quote["mid"])
    raw = {tf: desk.shared.load_candles("XAUUSD", tf) for tf in FRAMES}
    # `map.build` reads the shared-memory shape; `scenarios.build` reads the
    # agent shape (t/o/h/l/c). Give each the shape it expects rather than
    # reshaping inside the desk, which would hide the difference.
    frames = raw
    sc_frames = {tf: [{"t": r["open_time"], "o": r["open"], "h": r["high"],
                       "l": r["low"], "c": r["close"],
                       "open": bool(r["is_open"])} for r in rows]
                 for tf, rows in raw.items()}

    h1 = [b for b in (raw.get("1h") or []) if not b.get("is_open")][-72:]
    bars = [{"t": b["open_time"][:16], "o": round(b["open"], 2),
             "h": round(b["high"], 2), "l": round(b["low"], 2),
             "c": round(b["close"], 2)} for b in h1]
    piv = []
    for i in range(2, len(bars) - 2):
        w = bars[i - 2:i + 3]
        if bars[i]["h"] == max(x["h"] for x in w):
            piv.append([i, "H", bars[i]["h"]])
        if bars[i]["l"] == min(x["l"] for x in w):
            piv.append([i, "L", bars[i]["l"]])

    m = MP.build(frames, live, symbol="XAUUSD")
    above, below = m._split()

    def zone(z):
        return {"lo": round(z.low, 2), "hi": round(z.high, 2), "n": z.touches,
                "tf": "،".join(z.frames), "note": ""}

    supply = [zone(z) for z in above[:3]]
    demand = [zone(z) for z in below[:3]]
    if supply:
        supply[0]["note"] = "الأقرب فوق السعر"
    if demand:
        demand[0]["note"] = "الأقرب تحت السعر"

    cal = desk.shared.load_calendar()
    sc = SC.build(sc_frames, live, cal, symbol="XAUUSD")
    setups = [{"k": ("🟢 شراء" if s["side"] == "long" else "🔴 بيع")
               + (" عند الاختراق" if s["side"] == "long" else " عند الكسر"),
               "on": s["trigger"], "stop": s["stop"], "tgt": s["targets"][0],
               "rr": s.get("rr"), "inv": s.get("invalidation"), "cls": s["side"],
               "frame": s.get("frame")} for s in sc.get("scenarios", [])]

    res = [{"p": z["hi"], "l": f"سقف منطقة ({z['n']} لمسة)"} for z in supply[:1]]
    sup = [{"p": z["lo"], "l": f"أرض منطقة ({z['n']} لمسة)"} for z in demand[:1]]

    data_at = quote.get("fetched_at")
    payload = {"built_at": __import__("datetime").datetime.now(
                   __import__("datetime").UTC).isoformat(timespec="seconds"),
               "data_at": (__import__("datetime").datetime.fromtimestamp(
                   float(data_at), __import__("datetime").UTC
               ).isoformat(timespec="seconds") if data_at else None),
               "bars": bars, "piv": piv, "live": round(live, 2),
               "supply": supply, "demand": demand, "res": res, "sup": sup,
               "sc": setups,
               "liq": [{"p": round(l.price, 2), "side": l.side, "tf": l.frame}
                       for l in m.liquidity],
               "rounds": [round(r, 2) for r in m.rounds],
               "atr": {tf: round(a, 2) for tf, a in m.atr.items()},
               "trend": m.trend, "event": sc.get("event")}

    with open(OUT_JSON, "w") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    print("bars", len(bars), "pivots", len(piv), "live", payload["live"],
          "| supply", len(supply), "demand", len(demand), "setups", len(setups))
    # Build the page in the same command: a payload without a page is a stale
    # page, which is the failure this whole exercise was about.
    import subprocess
    subprocess.run([sys.executable, str(ROOT / "make_html.py")], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
