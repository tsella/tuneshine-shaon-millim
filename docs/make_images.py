#!/usr/bin/env python3
"""Regenerate the images in docs/.

    python3 docs/make_images.py

Every image here is a panel simulation: the 64x64 frame drawn the way the LED
matrix shows it, one circular die per pixel with a halo standing in for the
diffuser. The frames are what actually gets pushed; the simulation is only for
looking at.

Requires Pillow. Uses the font in fonts/.
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import shaon_millim as sm  # noqa: E402

BG = (247, 247, 249)
INK = (23, 25, 31)
MUTED = (110, 114, 124)


def label_font(size=15):
    for path in ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def panel(hour, minute, config, scale=5):
    img, _ = sm.render(hour, minute, config)
    return sm.simulate_panel(img, scale=scale)


def sheet(cells, cols, cell_w, cell_h, top=34, pad=14, caption_h=0):
    """Lay out pre-rendered panels with a caption under each."""
    rows = (len(cells) + cols - 1) // cols
    W = cols * cell_w + (cols + 1) * pad
    H = top + rows * (cell_h + caption_h + pad) + pad
    out = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(out)
    f = label_font()
    for i, (img, caption) in enumerate(cells):
        r, c = divmod(i, cols)
        x = pad + c * (cell_w + pad)
        y = top + r * (cell_h + caption_h + pad)
        out.paste(img, (x, y))
        if caption:
            d.text((x, y + cell_h + 8), caption, font=f, fill=INK)
    return out, d


def make_example():
    img = panel(9, 41, {"colors": {"preset": "oled-warm"}}, scale=8)
    img.save(os.path.join(HERE, "example.png"))
    print("example.png", img.size)


def make_colours():
    cells = []
    for name in sm.PRESET_NAMES:
        if name in ("dynamic", "custom"):
            continue
        hour = 9
        cells.append((panel(hour, 41, {"colors": {"preset": name}}, scale=4), name))
    out, d = sheet(cells, 3, 4 * 64, 4 * 64, top=42, caption_h=24)
    d.text((14, 12), "Colour presets, 09:41", font=label_font(17), fill=INK)
    out.save(os.path.join(HERE, "colors.png"))
    print("colors.png", out.size)


def make_sizing():
    cells = []
    for hour, minute in ((9, 41), (21, 47), (3, 5)):
        fit = panel(hour, minute, {"render": {"sizing": "fit"}}, scale=4)
        static = panel(hour, minute, {"render": {"sizing": "static",
                                                 "size": 12, "line_height": 16}}, scale=4)
        stamp = "%02d:%02d" % (hour, minute)
        cells.append((fit, stamp + "  fit"))
        cells.append((static, stamp + "  static 12/16"))
    out, d = sheet(cells, 2, 4 * 64, 4 * 64, top=42, caption_h=24)
    d.text((14, 12), "Sizing: fit picks the largest size per phrase, static holds 12 px",
           font=label_font(17), fill=INK)
    out.save(os.path.join(HERE, "sizing.png"))
    print("sizing.png", out.size)


if __name__ == "__main__":
    make_example()
    make_colours()
    make_sizing()
