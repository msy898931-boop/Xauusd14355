"""Chart — a price picture, drawn to a PNG with no dependencies.

The desk is stdlib-only, so there is no matplotlib here and no Pillow. A PNG is
written by hand instead: a real image encoder is ~60 lines once zlib and struct
are allowed, and zlib is stdlib. The result is a clean line chart with the
watched levels drawn across it, which is what the chat actually needs -- a
600x300 picture of where price is against the levels, not a charting library.

Two rules the picture keeps, because the rest of the desk keeps them:
  * it plots CLOSED bars only, so the last point does not move under the tick;
  * a level is drawn where it is watched, labelled in Arabic, so the picture and
    the text agree.
"""

from __future__ import annotations

import struct
import zlib

# A calm, readable palette. (R, G, B) tuples.
BG = (17, 24, 39)          # slate-900
GRID = (51, 65, 85)        # slate-700
LINE = (250, 204, 21)      # amber-400, the price
UP = (34, 197, 94)         # green-500, an "above" level
DOWN = (239, 68, 68)       # red-500, a "below" level
TEXT = (226, 232, 240)     # slate-200


class Canvas:
    """A tiny RGB raster with the few drawing calls a line chart needs."""

    def __init__(self, width: int, height: int, bg: tuple = BG):
        self.w, self.h = width, height
        self.px = bytearray(bg * (width * height))

    def _set(self, x: int, y: int, color: tuple) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.px[i:i + 3] = bytes(color)

    def dot(self, x: int, y: int, color: tuple, r: int = 1) -> None:
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                self._set(x + dx, y + dy, color)

    def line(self, x0: int, y0: int, x1: int, y1: int, color: tuple,
             width: int = 1) -> None:
        """Bresenham, thickened by drawing a small dot at each step."""
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        r = max(0, width // 2)
        while True:
            self.dot(x0, y0, color, r)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def rect(self, x0: int, y0: int, x1: int, y1: int, color: tuple) -> None:
        for y in range(max(0, y0), min(self.h, y1 + 1)):
            for x in range(max(0, x0), min(self.w, x1 + 1)):
                self._set(x, y, color)

    def png(self) -> bytes:
        """Encode the raster as a PNG (RGB, 8-bit, no interlace)."""
        raw = bytearray()
        stride = self.w * 3
        for y in range(self.h):
            raw.append(0)                      # filter type 0: none
            raw += self.px[y * stride:(y + 1) * stride]

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        ihdr = struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
                + chunk(b"IEND", b""))


# A 5x7 bitmap font for the digits and letters the chart labels need. Kept tiny
# on purpose: the chart's text is a handful of numbers and Latin labels, and a
# full font is not worth the lines. Each glyph is 7 rows of 5 bits.
_GLYPHS = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ",": ["00000", "00000", "00000", "00000", "01100", "01100", "00100"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
}


def text(c: Canvas, x: int, y: int, s: str, color: tuple = TEXT,
         scale: int = 1) -> int:
    """Draw `s` at (x, y); return the width used. Unknown chars are skipped."""
    start = x
    for ch in s:
        g = _GLYPHS.get(ch)
        if g:
            for ry, row in enumerate(g):
                for rx, bit in enumerate(row):
                    if bit == "1":
                        c.rect(x + rx * scale, y + ry * scale,
                               x + rx * scale + scale - 1,
                               y + ry * scale + scale - 1, color)
        x += 6 * scale
    return x - start


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi <= lo:
        return [lo]
    step = (hi - lo) / (n - 1)
    return [lo + i * step for i in range(n)]


def price_chart(bars: list[dict], *, levels: tuple = (), live: float | None = None,
                width: int = 640, height: int = 340) -> bytes:
    """A close-price line chart with the watched levels drawn across it.

    `bars` are daily bars (dicts with `close`), oldest first, and must be closed
    bars -- the caller drops the forming one. `levels` are `Level` objects with
    `.price`, `.label` and `.side`. Returns PNG bytes.
    """
    c = Canvas(width, height)
    pad_l, pad_r, pad_t, pad_b = 8, 74, 10, 22
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    closes = [float(b["close"]) for b in bars if b.get("close") is not None]
    if len(closes) < 2:
        return c.png()

    vals = list(closes)
    for lv in levels:
        vals.append(float(lv.price))
    if live:
        vals.append(float(live))
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    lo -= span * 0.05
    hi += span * 0.05
    span = hi - lo

    def y_of(v: float) -> int:
        return pad_t + int((hi - v) / span * plot_h)

    def x_of(i: int) -> int:
        return pad_l + int(i / (len(closes) - 1) * plot_w)

    # horizontal grid + price labels on the right
    for v in _ticks(lo, hi, 5):
        y = y_of(v)
        c.line(pad_l, y, pad_l + plot_w, y, GRID)
        text(c, pad_l + plot_w + 4, y - 3, f"{v:,.0f}", TEXT)

    # the watched levels, coloured by the side that counts as a break
    for lv in levels:
        y = y_of(float(lv.price))
        col = UP if lv.side == "above" else DOWN
        c.line(pad_l, y, pad_l + plot_w, y, col)
        text(c, pad_l + plot_w + 4, y - 3, f"{float(lv.price):,.0f}", col)

    # the price line
    for i in range(1, len(closes)):
        c.line(x_of(i - 1), y_of(closes[i - 1]),
               x_of(i), y_of(closes[i]), LINE, width=2)

    # the live price, if the caller has one
    if live:
        y = y_of(float(live))
        c.line(pad_l, y, pad_l + plot_w, y, LINE)
        c.dot(pad_l + plot_w, y, LINE, r=2)

    # date labels: first and last, kept short
    first = str(bars[0].get("openTime", ""))[:10]
    last = str(bars[-1].get("openTime", ""))[:10]
    text(c, pad_l, height - 14, first)
    text(c, width - pad_r - 60, height - 14, last)
    return c.png()
