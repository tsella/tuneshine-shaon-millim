#!/usr/bin/env python3
"""Shaon millim - a Hebrew word clock for a 64x64 Tuneshine panel.

Instead of digits, the time is written out as a Hebrew sentence, with optional
vowel points (nikkud), laid out right to left and sized by measurement so that
no line ever runs off the panel.

Colour is configurable. Seven named bases ship, plus `custom` for supplying your
own four colours. Any base can be overridden per layer with a hex value.

    from shaon_millim import render
    img, info = render(9, 41, {"colors": {"preset": "shabbat"}})

    python3 shaon_millim.py --time 09:41 --preset shabbat --out frame.png
    python3 shaon_millim.py --push                 # current time, to the panel
    python3 shaon_millim.py --swatches swatches.png
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from PIL import Image, ImageDraw, ImageFont, features

SIZE = 64
RIGHT_MARGIN = 5
GAP = 2
MAX_W = SIZE - RIGHT_MARGIN
HAS_RAQM = features.check("raqm")
HERE = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(HERE, "fonts", "FrankRuhlLibre-VF.ttf")
FONT_WEIGHT = "Bold"

NIKKUD_RE = re.compile(r"[\u0591-\u05C7]")


# --------------------------------------------------------------------------
# Colour bases
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Palette:
    """The four layers the panel can colour independently."""

    background: Tuple[int, int, int]
    letter: Tuple[int, int, int]
    nikkud: Tuple[int, int, int]
    period: Tuple[int, int, int]
    period_nikkud: Optional[Tuple[int, int, int]] = None

    @property
    def on_pixels(self) -> int:
        """Pixels lit by the background alone. A bright background lights the panel."""
        return SIZE * SIZE if sum(self.background) > 90 else 0


def _rgb(value: str) -> Tuple[int, int, int]:
    if not isinstance(value, str):
        raise ValueError("colour must be a string like #RRGGBB, got %r" % (value,))
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", text):
        raise ValueError("colour must be #RRGGBB or #RGB, got %r" % (value,))
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


PRESETS: Dict[str, Dict[str, str]] = {
    "oled-warm": {"background": "#050608", "letter": "#FFF8EB",
                  "nikkud": "#F59E0B", "period": "#94A3B8"},
    "eink": {"background": "#D6D7D2", "letter": "#18191B",
             "nikkud": "#475569", "period": "#334155"},
    "cyber": {"background": "#040814", "letter": "#38BDF8",
              "nikkud": "#FB7185", "period": "#818CF8"},
    "shabbat": {"background": "#090D1E", "letter": "#FDE68A",
                "nikkud": "#F59E0B", "period": "#60A5FA"},
    "emerald": {"background": "#040D08", "letter": "#ECFDF5",
                "nikkud": "#10B981", "period": "#6EE7B7"},
    "crimson-night": {"background": "#090204", "letter": "#FECDD3",
                      "nikkud": "#E11D48", "period": "#9F1239"},
}

# Colour that drifts with the hour rather than staying fixed.
DYNAMIC: List[Tuple[int, int, Dict[str, str]]] = [
    (5, 11, {"background": "#04080E", "letter": "#F0F9FF",
             "nikkud": "#38BDF8", "period": "#FBBF24"}),   # dawn
    (11, 17, {"background": "#050608", "letter": "#FFF8EB",
              "nikkud": "#F59E0B", "period": "#94A3B8"}),  # midday
    (17, 22, {"background": "#0A0508", "letter": "#FFEDD5",
              "nikkud": "#FB7185", "period": "#F97316"}),  # evening
]

PRESET_NAMES = list(PRESETS) + ["dynamic", "custom"]

DEFAULT_COLORS: Dict[str, Any] = {
    "preset": "oled-warm",
    "background": None,
    "letter": None,
    "nikkud": None,
    "period": None,
    "period_luminance": True,
}

# Used only when `preset: custom` leaves a layer unset, so a partial custom block
# still renders instead of raising. White on black.
FALLBACK_LAYERS: Dict[str, str] = {
    "background": "#000000", "letter": "#FFFFFF",
    "nikkud": "#9CA3AF", "period": "#9CA3AF",
}


def _dynamic(hour: int) -> Dict[str, str]:
    for start, end, spec in DYNAMIC:
        if start <= hour < end:
            return spec
    return {"background": "#050203", "letter": "#FCA5A5",
            "nikkud": "#DC2626", "period": "#7F1D1D"}          # night


def _scale(rgb: Tuple[int, int, int], factor: float) -> Tuple[int, int, int]:
    return tuple(max(0, min(255, int(c * factor))) for c in rgb)


def palette_for(colors: Optional[Dict[str, Any]], hour: int) -> Palette:
    """Resolve the config block into four concrete colours for this hour.

    Order of precedence: a named preset (or `custom`), then any per-layer hex
    override, then the period-luminance adjustment for the time of day.
    """
    cfg = dict(DEFAULT_COLORS)
    for key, value in (colors or {}).items():
        if key in cfg:
            cfg[key] = value

    name = (cfg.get("preset") or "oled-warm").lower()
    if name == "dynamic":
        spec = _dynamic(hour)
    elif name == "custom":
        spec = {}
    elif name in PRESETS:
        spec = PRESETS[name]
    else:
        raise ValueError(
            "unknown colour preset %r; choose from %s" % (name, ", ".join(PRESET_NAMES)))

    layers = {}
    for layer in ("background", "letter", "nikkud", "period"):
        override = cfg.get(layer)
        if override:
            layers[layer] = _rgb(override)
        elif spec.get(layer):
            layers[layer] = _rgb(spec[layer])
        else:
            layers[layer] = _rgb(FALLBACK_LAYERS[layer])

    pal = Palette(**layers)

    # None means "not specified", not "off". The default is on.
    lum = cfg.get("period_luminance")
    if lum is None:
        lum = True
    if lum:
        if 5 <= hour < 12:
            factor, nfactor = 1.40, 1.25
        elif 12 <= hour < 17:
            factor, nfactor = 1.15, 1.00
        elif 17 <= hour < 21:
            factor, nfactor = 0.52, 0.58
        else:
            factor, nfactor = 0.38, 0.45
        # Mirrors the reference: the letters and their points keep their base
        # colours, and only the period line is brightened or dimmed.
        pal = Palette(pal.background, pal.letter, pal.nikkud,
                      _scale(pal.period, factor), _scale(pal.nikkud, nfactor))
    return pal


def colors_from_config(config: Optional[Dict[str, Any]], hour: int) -> Palette:
    """Read the `colors` block out of a full config dict."""
    if not config:
        return palette_for(None, hour)
    return palette_for(config.get("colors"), hour)


# --------------------------------------------------------------------------
# Hebrew phrasing
# --------------------------------------------------------------------------
HOURS = {
    1: "אַחַת", 2: "שְׁתַּיִם", 3: "שָׁלוֹשׁ", 4: "אַרְבַּע", 5: "חָמֵשׁ",
    6: "שֵׁשׁ", 7: "שֶׁבַע", 8: "שְׁמוֹנֶה", 9: "תֵּשַׁע", 10: "עֶשֶׂר",
    11: "אַחַת עֶשְׂרֵה", 12: "שְׁתֵּים עֶשְׂרֵה",
}
ONES_AND = {
    1: "וְאַחַת", 2: "וּשְׁתַּיִם", 3: "וְשָׁלוֹשׁ", 4: "וְאַרְבַּע",
    5: "וְחָמֵשׁ", 6: "וָשֵׁשׁ", 7: "וָשֶׁבַע", 8: "וּשְׁמוֹנֶה", 9: "וָתֵשַׁע",
}
TEENS_AND = {
    11: "וְאַחַת עֶשְׂרֵה", 12: "וּשְׁתֵּים עֶשְׂרֵה", 13: "וּשְׁלוֹשׁ עֶשְׂרֵה",
    14: "וְאַרְבַּע עֶשְׂרֵה", 15: "וָרֶבַע", 16: "וְשֵׁשׁ עֶשְׂרֵה",
    17: "וּשְׁבַע עֶשְׂרֵה", 18: "וּשְׁמוֹנֶה עֶשְׂרֵה", 19: "וּתְשַׁע עֶשְׂרֵה",
}
TENS_AND = {10: "וְעֶשֶׂר", 20: "וְעֶשְׂרִים", 30: "וָחֵצִי",
            40: "וְאַרְבָּעִים", 50: "וַחֲמִשִּׁים"}
TENS_PLAIN = {20: "וְעֶשְׂרִים", 30: "וּשְׁלוֹשִׁים",
              40: "וְאַרְבָּעִים", 50: "וַחֲמִשִּׁים"}


def strip_nikkud(text: str) -> str:
    return NIKKUD_RE.sub("", text)


def day_period(hour: int) -> str:
    if hour == 0:
        return ""
    if 1 <= hour < 5:
        return "בַּלַּיְלָה"
    if 5 <= hour < 12:
        return "בַּבּוֹקֶר"
    if hour == 12:
        return "בַּצָּהֳרַיִם"
    if 13 <= hour < 17:
        return "אַחַר הַצָּהֳרַיִם"
    if 17 <= hour < 21:
        return "בָּעֶרֶב"
    return "בַּלַּיְלָה"


@dataclass
class Token:
    text: str
    role: str            # "hour" | "min" | "period"


def phrase(hour: int, minute: int) -> List[Token]:
    hour12 = 12 if hour % 12 == 0 else hour % 12
    hour_word = "חֲצוֹת" if hour == 0 else HOURS[hour12]
    period = day_period(hour)

    if minute == 0:
        mins: List[str] = []
    elif minute == 1:
        mins = ["וְדַקָּה"]
    elif minute == 2:
        mins = ["וּשְׁתֵּי", "דַּקּוֹת"]
    elif 3 <= minute <= 9:
        mins = [ONES_AND[minute], "דַּקּוֹת"]
    elif minute == 10:
        mins = ["וְעֶשֶׂר", "דַּקּוֹת"]
    elif 11 <= minute <= 19:
        mins = TEENS_AND[minute].split(" ")
    elif minute in (20, 30, 40, 50):
        mins = [TENS_AND[minute]]
    else:
        ten, one = (minute // 10) * 10, minute % 10
        mins = [TENS_PLAIN[ten], ONES_AND[one]]

    tokens = [Token(w, "period" if hour == 0 else "hour") for w in hour_word.split(" ")]
    tokens += [Token(w, "min") for w in mins]
    if period:
        if period == "אַחַר הַצָּהֳרַיִם" and len(tokens) >= 3:
            tokens.append(Token("אחה״צ", "period"))
        else:
            tokens += [Token(w, "period") for w in period.split(" ")]
    return tokens


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def _font(size: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(FONT_PATH, size)
    if FONT_WEIGHT:
        try:
            f.set_variation_by_name(FONT_WEIGHT)
        except Exception:
            pass
    return f


def set_font(path: Optional[str] = None, weight: Optional[str] = None) -> None:
    """Point the renderer at another font file.

    The face is module state, so this affects every later render in the process.
    A config `fonts` block sets it automatically at the start of each render.
    """
    global FONT_PATH, FONT_WEIGHT
    if path:
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            raise FileNotFoundError("font not found: %s" % path)
        FONT_PATH = path
    if weight is not None:
        FONT_WEIGHT = weight or None


def _kw() -> Dict[str, str]:
    return {"direction": "rtl", "language": "he"} if HAS_RAQM else {}


def _ink_width(text: str, font: ImageFont.FreeTypeFont) -> int:
    core = font.getmask(text, mode="L", **_kw())
    if core.size == (0, 0):
        return 0
    im = Image.frombytes("L", core.size, bytes(core))
    box = im.getbbox()
    return 0 if box is None else box[2] - box[0]


def wrap(tokens: List[Token], font: ImageFont.FreeTypeFont) -> List[List[Token]]:
    """Break on measured ink. The period always starts its own line."""
    lines: List[List[Token]] = []
    current: List[Token] = []
    for tok in tokens:
        trial = current + [tok]
        role_break = bool(current and (current[0].role == "period") != (tok.role == "period"))
        if current and (role_break or _ink_width(" ".join(t.text for t in trial), font) > MAX_W):
            lines.append(current)
            current = [tok]
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


RENDER_DEFAULTS: Dict[str, Any] = {
    "sizing": "fit",        # fit | static
    "size": None,           # static only; None means 12
    "line_height": None,    # static only; None means size + 4
    "line_gap": GAP,        # fit only
    "nikkud": "color",      # color | full | none
    "align": "right",       # right | center
    "antialias": True,      # False snaps edge pixels back to the background
}
NIKKUD_MODES = ("color", "full", "none")
SIZING_MODES = ("fit", "static")
STATIC_SIZE_DEFAULT = 12


def resolve_render(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge the `render` block over the defaults, rejecting nonsense early.

    Naming a size without naming a mode is taken to mean static, because that is
    the only mode a size has any effect in.
    """
    raw = dict((config or {}).get("render") or {})
    opts = dict(RENDER_DEFAULTS)
    for key, value in raw.items():
        if key in opts and value is not None:
            opts[key] = value

    if raw.get("size") is not None and raw.get("sizing") is None:
        opts["sizing"] = "static"

    if opts["sizing"] not in SIZING_MODES:
        raise ValueError("render.sizing must be fit or static, got %r" % (opts["sizing"],))
    if opts["nikkud"] not in NIKKUD_MODES:
        raise ValueError("render.nikkud must be one of %s, got %r"
                         % (", ".join(NIKKUD_MODES), opts["nikkud"]))
    if opts["align"] not in ("right", "center"):
        raise ValueError("render.align must be right or center, got %r" % (opts["align"],))

    if opts["size"] is not None:
        opts["size"] = int(opts["size"])
        if not 6 <= opts["size"] <= 40:
            raise ValueError("render.size must be 6-40, got %d" % opts["size"])
    if opts["line_height"] is not None:
        opts["line_height"] = int(opts["line_height"])
        if not 6 <= opts["line_height"] <= 48:
            raise ValueError("render.line_height must be 6-48, got %d" % opts["line_height"])

    opts["line_gap"] = int(opts["line_gap"])
    if not 0 <= opts["line_gap"] <= 12:
        raise ValueError("render.line_gap must be 0-12, got %d" % opts["line_gap"])
    return opts


def _line_colors(line: List[Token], pal: Palette) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
    """The period line carries its own colour, including its own nikkud colour."""
    if all(t.role == "period" for t in line):
        return pal.period, (pal.period_nikkud or pal.nikkud)
    return pal.letter, pal.nikkud


def _line_tile(line: List[Token], size: int, letter: Tuple[int, int, int],
               nikkud: Tuple[int, int, int], mode: str = "color"
               ) -> Optional[Tuple[Image.Image, Image.Image]]:
    """Render one line to an ink-cropped tile.

    In "color" mode the pointed string is drawn in the nikkud colour and the bare
    consonants are stamped over it. The points sit below the baseline, so they
    survive the second pass in their own colour. "full" draws everything in the
    letter colour, "none" drops the points.
    """
    text = " ".join(t.text for t in line)
    bare = strip_nikkud(text)
    font = _font(size)
    scratch = Image.new("RGB", (192, 64), (0, 0, 0))
    d = ImageDraw.Draw(scratch)
    if mode == "none":
        d.text((188, 32), bare, font=font, fill=letter, anchor="rm", **_kw())
    elif mode == "full":
        d.text((188, 32), text, font=font, fill=letter, anchor="rm", **_kw())
    else:
        d.text((188, 32), text, font=font, fill=nikkud, anchor="rm", **_kw())
        d.text((188, 32), bare, font=font, fill=letter, anchor="rm", **_kw())

    box = scratch.convert("L").point(lambda v: 255 if v > 12 else 0).getbbox()
    if box is None:
        return None
    tile = scratch.crop(box)
    return tile, tile.convert("L").point(lambda v: 255 if v > 12 else 0)


def _tiles_for(lines: List[List[Token]], size: int, pal: Palette, mode: str):
    tiles = []
    for line in lines:
        letter, nikkud = _line_colors(line, pal)
        tile = _line_tile(line, size, letter, nikkud, mode)
        if tile is None:
            return None
        tiles.append(tile)
    return tiles


def _packed_height(tiles, gap: int) -> int:
    return sum(t[0].height for t in tiles) + gap * max(0, len(tiles) - 1)


def fit(tokens: List[Token], pal: Palette, mode: str = "color", gap: int = GAP,
        hi: int = 26, lo: int = 8):
    """Pick the largest size whose packed tiles fit the panel.

    Returns (size, lines, tiles, fits). `fits` is False only when even the
    smallest size overflows, which the caller should report rather than hide.
    """
    for size in range(hi, lo - 1, -1):
        lines = wrap(tokens, _font(size))
        tiles = _tiles_for(lines, size, pal, mode)
        if tiles is None:
            continue
        if (all(t[0].width <= MAX_W for t in tiles)
                and _packed_height(tiles, gap) <= SIZE):
            return size, lines, tiles, True

    lines = wrap(tokens, _font(lo))
    tiles = _tiles_for(lines, lo, pal, mode) or []
    return lo, lines, tiles, False


def _static_tops(tiles, line_height: int) -> List[int]:
    """Top of each tile when lines sit on a fixed pitch, as the reference does.

    The block is centred on the panel; each line is placed by its vertical centre
    so a taller line with vowel marks does not drag the whole block down.
    """
    span = (len(tiles) - 1) * line_height
    start = int(round(SIZE / 2 - span / 2))
    return [start + i * line_height - tile[0].height // 2 for i, tile in enumerate(tiles)]


def _crisp(img: Image.Image, palette: "Palette") -> Image.Image:
    """Snap every pixel onto the nearest palette colour, for hard bilevel output.

    Text is drawn antialiased whatever the setting, so the ink pixels carry a
    gradient of their own. Dropping the transparent corners of the mask alone
    would leave that gradient in place, which is not a crisp edge. Snapping to the
    palette is what actually makes the frame bilevel.
    """
    colours = [palette.background, palette.letter, palette.nikkud, palette.period]
    if palette.period_nikkud:
        colours.append(palette.period_nikkud)

    def nearest(pixel):
        return min(colours, key=lambda c: ((c[0] - pixel[0]) ** 2
                                           + (c[1] - pixel[1]) ** 2
                                           + (c[2] - pixel[2]) ** 2))

    cache: Dict[Tuple[int, int, int], Tuple[int, int, int]] = {}
    px = img.load()
    for y in range(SIZE):
        for x in range(SIZE):
            current = px[x, y][:3]
            if current not in cache:
                cache[current] = nearest(current)
            px[x, y] = cache[current]
    return img


def render(hour: int, minute: int, config: Optional[Dict[str, Any]] = None):
    """Render one frame. Returns (image, info).

    info carries the diagnostics a caller needs to judge whether the phrase was
    comfortable at this size: the size chosen, the line count, the widest line,
    and whether anything overflowed.
    """
    opts = resolve_render(config)
    fonts = (config or {}).get("fonts") or {}
    if fonts.get("path") or fonts.get("weight"):
        set_font(fonts.get("path"), fonts.get("weight"))
    pal = colors_from_config(config, hour)
    tokens = phrase(hour, minute)

    static = opts["sizing"] == "static"
    line_height = None
    if static:
        size = opts["size"] or STATIC_SIZE_DEFAULT
        line_height = opts["line_height"] or size + 4
        lines = wrap(tokens, _font(size))
        tiles = _tiles_for(lines, size, pal, opts["nikkud"]) or []
        positions = _static_tops(tiles, line_height)
        fits = (all(t[0].width <= MAX_W for t in tiles)
                and all(0 <= top and top + t[0].height <= SIZE
                        for top, t in zip(positions, tiles)))
    else:
        size, lines, tiles, fits = fit(tokens, pal, opts["nikkud"], opts["line_gap"])
        block = _packed_height(tiles, opts["line_gap"])
        positions, y = [], max(0, (SIZE - block) // 2)
        for tile, _mask in tiles:
            positions.append(y)
            y += tile.height + opts["line_gap"]

    img = Image.new("RGB", (SIZE, SIZE), pal.background)
    for (tile, mask), top in zip(tiles, positions):
        if opts["align"] == "center":
            x = (SIZE - tile.width) // 2
        else:
            x = SIZE - RIGHT_MARGIN - tile.width
        img.paste(tile, (max(0, x), top), mask)

    if not opts["antialias"]:
        img = _crisp(img, pal)

    widest = max((t[0].width for t in tiles), default=0)
    longest = max((len(strip_nikkud(" ".join(t.text for t in line))) for line in lines), default=0)
    info = {
        "sizing": opts["sizing"],
        "size": size,
        "line_height": line_height,
        "lines": len(lines),
        "widest": widest,
        "limit": MAX_W,
        "chars": longest,
        "fits": bool(fits and widest <= MAX_W),
    }
    return img, info


# --------------------------------------------------------------------------
# Panel simulation (preview only)
# --------------------------------------------------------------------------
PANEL_RGB = (6, 7, 9)
LED_FLOOR = (14, 15, 18)
LED_RADIUS = 0.42        # of one cell
HALO_RADIUS = 1.45       # multiple of the LED radius
HALO_ALPHA = 0.22
GLOW_THRESHOLD = 35      # mean channel value below this gets no halo


def simulate_panel(frame: Image.Image, scale: int = 8, glow: bool = True,
                   supersample: int = 4) -> Image.Image:
    """Preview a frame the way a physical panel looks. Never upload this.

    Each framebuffer pixel becomes a circular LED die on a near-black substrate,
    with a translucent halo standing in for the diffuser. Off pixels are floored
    rather than pure black, because a real dark LED still catches stray light.

    The device takes the frame itself, not this. Call it only to judge how a
    design will read in a dark room.
    """
    cell = scale
    size = SIZE * cell * supersample
    panel = Image.new("RGB", (size, size), PANEL_RGB)
    d = ImageDraw.Draw(panel, "RGBA")
    radius = cell * LED_RADIUS * supersample

    for y in range(SIZE):
        for x in range(SIZE):
            r, g, b = frame.getpixel((x, y))
            cx = x * cell * supersample + (cell * supersample) / 2
            cy = y * cell * supersample + (cell * supersample) / 2
            if glow and (r + g + b) / 3.0 > GLOW_THRESHOLD:
                h = radius * HALO_RADIUS
                d.ellipse([cx - h, cy - h, cx + h, cy + h],
                          fill=(r, g, b, int(HALO_ALPHA * 255)))
            d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                      fill=(max(r, LED_FLOOR[0]), max(g, LED_FLOOR[1]), max(b, LED_FLOOR[2])))

    if supersample > 1:
        panel = panel.resize((SIZE * cell, SIZE * cell), Image.LANCZOS)
    return panel


# --------------------------------------------------------------------------
# Uploading to the panel
# --------------------------------------------------------------------------
UPLOAD_TIMEOUT = 8
POLL_TIMEOUT = 4
UPLOAD_LABEL = "Hebrew word clock"


def device_base(host: str) -> str:
    """Normalise a configured host into a base URL."""
    host = str(host).strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host


def device_state(host: str, timeout: float = POLL_TIMEOUT) -> Optional[Dict[str, Any]]:
    """GET /state, or None if the panel does not answer."""
    try:
        resp = requests.get(device_base(host) + "/state", timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def music_is_playing(state: Optional[Dict[str, Any]]) -> bool:
    """True when the panel is showing remote artwork rather than its idle image."""
    remote = (state or {}).get("remoteMetadata") or {}
    return not remote.get("idle", True)


def encode_webp(img: Image.Image) -> bytes:
    """Lossless 64x64 WebP. Lossy would smear the vowel points."""
    buf = io.BytesIO()
    img.save(buf, format="WEBP", lossless=True)
    return buf.getvalue()


def upload_frame(host: str, payload: bytes, phrase: str, hhmm: str,
                 label: str = UPLOAD_LABEL, timeout: float = UPLOAD_TIMEOUT) -> str:
    """POST the frame to /image and return a one-line result."""
    metadata = json.dumps({
        "trackName": phrase,
        "artistName": label,
        "serviceName": "tuneshine-shaon-millim",
        "itemId": "clock-" + hhmm.replace(":", ""),
        "idle": True,          # a screensaver, not a track
        "overridable": True,   # let playing music take the panel back
    }, ensure_ascii=False)
    resp = requests.post(device_base(host) + "/image",
                         files={"image": ("clock.webp", payload, "image/webp")},
                         data={"metadata": metadata},
                         timeout=timeout)
    if resp.status_code != 200:
        return "HTTP %s: %s" % (resp.status_code, resp.text[:120])
    return "ok"


def push_to_devices(config: Dict[str, Any], img: Image.Image, phrase: str,
                    hhmm: str) -> int:
    """Upload one frame to every configured panel. Returns the failure count."""
    devices = config.get("devices") or []
    if not devices:
        print("no devices configured; set \"devices\" to a list of hosts", file=sys.stderr)
        return 1

    payload = encode_webp(img)
    label = config.get("label") or UPLOAD_LABEL
    respect = config.get("respect_playback", True)
    failures = 0

    for host in devices:
        if respect and music_is_playing(device_state(host)):
            print("  %-15s skipped: music is playing" % host)
            continue
        try:
            result = upload_frame(host, payload, phrase, hhmm, label)
        except Exception as exc:
            result = "%s: %s" % (type(exc).__name__, exc)
        if result == "ok":
            print("  %-15s pushed %s -> %s (%d bytes)" % (host, hhmm, phrase, len(payload)))
        else:
            failures += 1
            print("  %-15s FAILED %s" % (host, result), file=sys.stderr)
    return failures


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _swatches(path: str) -> None:
    from PIL import ImageDraw as _D
    names = PRESET_NAMES
    cases = [(6, 0), (9, 41), (19, 45)]
    cell, pad, left = 192, 12, 150
    W = left + (cell + pad) * len(cases) + pad
    H = pad + (cell + 26) * len(names) + pad
    sheet = Image.new("RGB", (W, H), (246, 246, 248))
    d = _D.Draw(sheet)
    label = ImageFont.truetype(
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 14)
    for row, name in enumerate(names):
        y = pad + row * (cell + 26)
        pal = palette_for({"preset": name}, 9)
        d.text((pad, y + 4), name, font=label, fill=(24, 26, 32))
        d.rectangle([pad, y + 24, pad + 14, y + 38], fill=pal.letter)
        d.rectangle([pad + 18, y + 24, pad + 32, y + 38], fill=pal.nikkud)
        d.rectangle([pad + 36, y + 24, pad + 50, y + 38], fill=pal.period)
        d.text((pad, y + 44), "#%02X%02X%02X" % pal.background,
               font=ImageFont.truetype(
                   "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 11),
               fill=(110, 114, 122))
        for col, (h, m) in enumerate(cases):
            img, info = render(h, m, {"colors": {"preset": name}})
            x = left + col * (cell + pad)
            sheet.paste(img.resize((cell, cell), Image.NEAREST), (x, y))
            d.text((x, y + cell + 4), "%02d:%02d · %dpx · %dl"
                   % (h, m, info["size"], info["lines"]),
                   font=label, fill=(110, 114, 122))
    sheet.save(path)
    print("wrote", path, sheet.size)


def emit_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The exact settings a frame was drawn with, defaults filled in.

    Round-trips: feeding this back to render() produces the same frame, so a
    preview can always be reproduced from the config it printed.
    """
    opts = resolve_render(config)
    colors = dict(DEFAULT_COLORS)
    for key, value in ((config or {}).get("colors") or {}).items():
        if key in colors and value is not None:
            colors[key] = value
    return {"colors": colors, "render": opts}


def main() -> int:
    ap = argparse.ArgumentParser(description="Hebrew word clock renderer for a 64x64 Tuneshine")
    ap.add_argument("--time", default=None, help="HH:MM to render")
    ap.add_argument("--preset", default="oled-warm", choices=PRESET_NAMES)
    ap.add_argument("--background", default=None, help="hex override")
    ap.add_argument("--letter", default=None, help="hex override")
    ap.add_argument("--nikkud-color", default=None, help="hex override")
    ap.add_argument("--period", default=None, help="hex override")
    ap.add_argument("--no-period-luminance", action="store_true")
    ap.add_argument("--nikkud", default=None, choices=NIKKUD_MODES,
                    help="color (separate vowel colour), full, or none")
    ap.add_argument("--align", default=None, choices=("right", "center"))
    ap.add_argument("--crisp", action="store_true",
                    help="hard bilevel output: snap every pixel to the nearest palette colour")
    ap.add_argument("--sizing", default=None, choices=SIZING_MODES,
                    help="fit: size each phrase to the panel. static: one size for every time")
    ap.add_argument("--size", type=int, default=None,
                    help="font size for static; naming it implies static")
    ap.add_argument("--line-height", type=int, default=None,
                    help="line pitch for static (default: size + 4)")
    ap.add_argument("--gap", type=int, default=None, help="pixels between lines in fit mode")
    ap.add_argument("--config", default=None, help="JSON config; its blocks win over flags")
    ap.add_argument("--out", default="frame.png", help="output PNG path")
    ap.add_argument("--scale", type=int, default=8)
    ap.add_argument("--simulate", action="store_true",
                    help="write a panel simulation instead of the raw frame (preview only)")
    ap.add_argument("--no-glow", action="store_true", help="simulation without the diffuser bloom")
    ap.add_argument("--swatches", default=None, help="render every preset to this path")
    ap.add_argument("--emit-config", action="store_true",
                    help="print the resolved config this frame was drawn with")
    ap.add_argument("--push", action="store_true",
                    help="render the current time and upload it to the configured panels")
    ap.add_argument("--device", action="append", default=None, metavar="HOST",
                    help="panel host for --push; repeatable, overrides the config list")
    args = ap.parse_args()

    if args.swatches:
        _swatches(args.swatches)
        return 0

    file_cfg: Dict[str, Any] = {}
    if args.config:
        with open(args.config, encoding="utf-8") as fh:
            file_cfg = json.load(fh)

    config: Dict[str, Any] = {
        "colors": {
            "preset": args.preset,
            "background": args.background,
            "letter": args.letter,
            "nikkud": args.nikkud_color,
            "period": args.period,
            "period_luminance": False if args.no_period_luminance else None,
        },
        "render": {
            "sizing": args.sizing,
            "size": args.size,
            "line_height": args.line_height,
            "nikkud": args.nikkud,
            "align": args.align,
            "antialias": False if args.crisp else None,
            "line_gap": args.gap,
        },
    }
    for block in ("colors", "render"):
        if file_cfg.get(block):
            config[block] = {**config[block], **file_cfg[block]}

    if args.push:
        if args.device:
            file_cfg["devices"] = args.device
        tz_name = file_cfg.get("timezone") or "local"
        try:
            tz = None if tz_name == "local" else ZoneInfo(tz_name)
        except Exception:
            print("unknown timezone %r; falling back to local" % tz_name, file=sys.stderr)
            tz = None
        now = datetime.now(tz)
        hour, minute = now.hour, now.minute
        hhmm = "%02d:%02d" % (hour, minute)
        img, info = render(hour, minute, config)
        spoken = strip_nikkud(" ".join(t.text for t in phrase(hour, minute)))
        print("%s  %s · %dpx · %d lines  %s" % (hhmm, info["sizing"], info["size"],
                                                info["lines"], spoken))
        if not info["fits"]:
            print("warning: this phrase does not fit the panel at the chosen settings",
                  file=sys.stderr)
        return 1 if push_to_devices(file_cfg, img, spoken, hhmm) else 0

    if not args.time:
        ap.error("--time HH:MM is required unless --swatches or --push is given")

    try:
        hour, minute = (int(x) for x in args.time.split(":"))
    except Exception:
        ap.error("--time must look like 09:41")
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        ap.error("--time must be within 00:00-23:59")

    img, info = render(hour, minute, config)
    pal = colors_from_config(config, hour)

    if args.simulate:
        out_img = simulate_panel(img, scale=args.scale // 2 or 1, glow=not args.no_glow)
    else:
        out_img = img.resize((SIZE * args.scale, SIZE * args.scale), Image.NEAREST)
    out_img.save(args.out)

    mode = info["sizing"]
    pitch = (" · line height %d" % info["line_height"]) if info["line_height"] else ""
    print("%s  %s · %dpx%s · %d line%s · widest %d/%dpx · %d base chars  %s"
          % (args.time, mode, info["size"], pitch, info["lines"],
             "" if info["lines"] == 1 else "s", info["widest"], info["limit"],
             info["chars"], "fits" if info["fits"] else "OVERFLOWS"))
    print("colours: bg #%02X%02X%02X  letters #%02X%02X%02X  nikkud #%02X%02X%02X  period #%02X%02X%02X"
          % (pal.background + pal.letter + pal.nikkud + pal.period))
    print("wrote", args.out)

    if args.emit_config:
        print(json.dumps(emit_config(config), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
