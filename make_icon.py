#!/usr/bin/env python3
"""Generate the application icon for PdfCompare (icon.ico).

Two overlapping pages split by a wipe divider, with one changed area boxed in
magenta -- the same idea the viewer shows. Run this only when the icon needs to
change; icon.ico is committed.

  python make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZES = (16, 24, 32, 48, 64, 128, 256)
BG = (32, 40, 48, 255)
PAGE_BEFORE = (238, 240, 243, 255)
PAGE_AFTER = (255, 255, 255, 255)
INK_BEFORE = (150, 160, 172, 255)
INK_AFTER = (96, 106, 120, 255)
DIVIDER = (255, 255, 255, 255)
ACCENT = (255, 0, 220, 255)
ADDED = (60, 235, 120, 255)


def draw_icon(size: int = 1024) -> Image.Image:
    s = size / 1024.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def px(value: float) -> float:
        return value * s

    # rounded dark plate
    d.rounded_rectangle([px(40), px(40), px(984), px(984)], radius=px(180), fill=BG)

    # back page (before), offset up-left
    d.rounded_rectangle([px(180), px(150), px(700), px(830)], radius=px(28),
                        fill=PAGE_BEFORE, outline=BG, width=int(px(10)))
    # front page (after), offset down-right, overlapping; the dark outline keeps
    # the two sheets readable even at 16x16
    d.rounded_rectangle([px(324), px(194), px(844), px(874)], radius=px(28),
                        fill=PAGE_AFTER, outline=BG, width=int(px(18)))

    # text lines: grey on the left page, darker on the right one
    for i in range(7):
        y = px(268 + i * 78)
        d.rounded_rectangle([px(214), y, px(560 - (i % 3) * 60), y + px(30)],
                            radius=px(15), fill=INK_BEFORE)
    for i in range(7):
        y = px(312 + i * 78)
        d.rounded_rectangle([px(384), y, px(730 - (i % 3) * 60), y + px(30)],
                            radius=px(15), fill=INK_AFTER)

    # one added line, in green, and the magenta box around the changed area
    d.rounded_rectangle([px(384), px(624), px(700), px(654)], radius=px(15), fill=ADDED)
    d.rectangle([px(352), px(592), px(736), px(686)], outline=ACCENT, width=int(px(22)))

    # wipe divider through the front page
    x = px(584)
    d.line([x, px(150), x, px(918)], fill=DIVIDER, width=int(px(26)))
    r = px(96)
    cy = px(534)
    d.ellipse([x - r, cy - r, x + r, cy + r], fill=DIVIDER)
    for sign in (-1, 1):
        tip, base = x + sign * px(58), x + sign * px(18)
        d.polygon([(tip, cy), (base, cy - px(38)), (base, cy + px(38))], fill=BG)
    return img


def main() -> int:
    here = Path(__file__).resolve().parent
    master = draw_icon(1024)
    master.resize((256, 256), Image.LANCZOS).save(here / "icon.png")
    master.save(here / "icon.ico", sizes=[(n, n) for n in SIZES])
    print(f"written: {here / 'icon.ico'} ({', '.join(f'{n}x{n}' for n in SIZES)})")
    print(f"written: {here / 'icon.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
