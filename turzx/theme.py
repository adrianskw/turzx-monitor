"""Colors, fonts and small drawing primitives shared by widgets."""

from __future__ import annotations

import math
import subprocess
import tomllib
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


# Palette (Tokyo Night defaults); load() overwrites these from an Omarchy theme.
BG = (14, 14, 20)
CARD = (26, 27, 38)
TEXT = (192, 202, 245)
MUTED = (86, 95, 137)
TRACK = (41, 46, 66)
ACCENT = (122, 162, 247)
GREEN = (158, 206, 106)
YELLOW = (224, 175, 104)
ORANGE = (255, 158, 100)
RED = (247, 118, 142)
CYAN = (13, 185, 215)
MAGENTA = (187, 154, 247)

OMARCHY_THEMES = [Path.home() / ".config/omarchy/themes", Path("/usr/share/omarchy/themes")]
CURRENT_THEME = Path.home() / ".local/state/omarchy/current/theme/colors.toml"
# our role -> Omarchy colors.toml key
ROLES = {
    "BG": "darker_background", "CARD": "background", "TEXT": "bright_foreground",
    "MUTED": "dark_foreground", "TRACK": "selection", "ACCENT": "accent", "GREEN": "green",
    "YELLOW": "yellow", "ORANGE": "bright_yellow", "RED": "red", "CYAN": "bright_cyan",
    "MAGENTA": "bright_magenta",
}


def _hex(c: str) -> tuple[int, int, int]:
    if not isinstance(c, str):
        raise ValueError(f"theme color must be a hex string, got {c!r}")
    c = c.removeprefix("#")
    if len(c) != 6:
        raise ValueError(f"theme color must have six hex digits, got {c!r}")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def theme_path(name: str) -> Path:
    """colors.toml for an Omarchy theme name, or the active theme for "current"."""
    if name == "current":
        return CURRENT_THEME
    for base in OMARCHY_THEMES:
        if (path := base / name / "colors.toml").exists():
            return path
    raise SystemExit(f"Omarchy theme {name!r} not found in {', '.join(map(str, OMARCHY_THEMES))}")


muted_lift = 0.0  # 0-1: how far secondary text is lifted from MUTED toward TEXT


def load(name: str, lift: float | None = None) -> None:
    """Adopt the palette of an Omarchy theme, e.g. "tokyo-night" or "current". `lift`
    (kept for later reloads) brightens MUTED toward TEXT: the theme's dark foreground is
    dimmer on the panel than on a monitor."""
    global muted_lift
    if lift is not None:
        if not 0 <= lift <= 1:
            raise ValueError("muted_lift must be between 0 and 1")
        muted_lift = float(lift)
    colors = tomllib.loads(theme_path(name).read_text())
    # Parse every role before publishing any colors, so a broken edit leaves the
    # previous palette intact while the theme file is being rewritten.
    palette = {role: _hex(colors[key]) for role, key in ROLES.items() if key in colors}
    if muted_lift and "MUTED" in palette:
        palette["MUTED"] = blend(palette["MUTED"], palette.get("TEXT", TEXT), muted_lift)
    globals().update(palette)


def px(v: float) -> int:
    """Nearest pixel, halves up: the one rounding rule for shape coordinates. Left to
    themselves, Pillow truncates rectangles, lines and ellipses, rounds rounded rectangles
    half-to-even (as round() does), so one fractional edge could land on different pixels.
    Text is the exception: Pillow hands its fractional origin to FreeType, which places and
    grid-fits each glyph itself, so snapping it first would round twice."""
    return math.floor(v + 0.5)


def _snap(xy):
    """px() every coordinate of a Pillow xy: (x0, y0, x1, y1), [x, y, ...] or [(x, y), ...]."""
    if xy and isinstance(xy[0], (tuple, list)):
        return [tuple(px(v) for v in point) for point in xy]
    return [px(v) for v in xy]


class Draw(ImageDraw.ImageDraw):
    """ImageDraw whose shapes snap coordinates with px(), so widgets can place them at
    fractional positions (centres, column pitches) and every shape agrees on the pixel.
    Text keeps its fractional origin (see px); measuring is untouched."""

    def rectangle(self, xy, *args, **kwargs):
        return super().rectangle(_snap(xy), *args, **kwargs)

    def rounded_rectangle(self, xy, *args, **kwargs):
        return super().rounded_rectangle(_snap(xy), *args, **kwargs)

    def line(self, xy, *args, **kwargs):
        return super().line(_snap(xy), *args, **kwargs)

    def ellipse(self, xy, *args, **kwargs):
        return super().ellipse(_snap(xy), *args, **kwargs)

    def polygon(self, xy, *args, **kwargs):
        return super().polygon(_snap(xy), *args, **kwargs)


FONT_DIR = "/usr/share/fonts/TTF"
FONTS = {
    "regular": f"{FONT_DIR}/JetBrainsMonoNerdFont-Regular.ttf",
    "bold": f"{FONT_DIR}/JetBrainsMonoNerdFont-Bold.ttf",
}
DEFAULT_FONTS = dict(FONTS)


# Layout sizes are tuned to JetBrains Mono, whose characters are 0.6 em wide. Another font
# is scaled so its characters are that wide too: every fixed-width fit (digit budgets,
# right-aligned columns, reserved widths) then holds unchanged.
REFERENCE_ADVANCE = 0.6
font_scale = 1.0


def _fc_file(family: str, style: str) -> str:
    """Font file for a family and style via fontconfig; error if it would fall back to another."""
    out = subprocess.run(["fc-match", "-f", "%{family}\\t%{file}", f"{family}:style={style}"],
                         capture_output=True, text=True, timeout=10).stdout
    families, _, path = out.partition("\t")
    if family.lower() not in (f.strip().lower() for f in families.split(",")) or not path:
        raise ValueError(f"font {family!r} ({style}) is not installed")
    return path


def use_font(family: str | None = None, scale: float | str = "match") -> None:
    """Draw with a monospaced font family found by fontconfig (e.g. "UbuntuMono Nerd Font";
    a Nerd Font, so the icons are there). scale="match" sizes it to JetBrains Mono's
    character width; a number scales it by that. None keeps JetBrains Mono."""
    global font_scale
    if family:
        paths = {"regular": _fc_file(family, "Regular"), "bold": _fc_file(family, "Bold")}
    else:
        paths = dict(DEFAULT_FONTS)
    if scale == "match":
        probe = ImageFont.truetype(paths["regular"], 1000)
        value = REFERENCE_ADVANCE * 1000 / probe.getlength("0")
    else:
        try:
            value = float(scale)
        except (TypeError, ValueError) as exc:
            raise ValueError('font_scale must be "match" or a positive number') from exc
        if isinstance(scale, bool) or not math.isfinite(value) or value <= 0:
            raise ValueError('font_scale must be "match" or a positive number')
    FONTS.update(paths)
    font_scale = value
    font.cache_clear()


@lru_cache(maxsize=None)
def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    """The UI font at a layout size (scaled by font_scale to render)."""
    f = ImageFont.truetype(FONTS[weight], max(1, px(size * font_scale)))
    f.layout_size = size
    return f


def size_of(f) -> float:
    """A font's layout size: what to derive related sizes from (f.size is the rendered size)."""
    return getattr(f, "layout_size", f.size)


ASSETS = Path(__file__).parent / "assets"


@lru_cache(maxsize=None)
def _icon(name: str, size: int) -> Image.Image:
    return Image.open(ASSETS / f"{name}.png").convert("RGBA").resize((size, size), Image.LANCZOS)


def icon(d: ImageDraw.ImageDraw, x: int, y: int, name: str, size: int) -> None:
    """Paste turzx/assets/<name>.png onto the widget image, alpha-blended."""
    img = _icon(name, size)
    d._image.paste(img, (px(x), px(y)), img)  # ImageDraw keeps a reference to its target image


def level_color(pct: float) -> tuple[int, int, int]:
    if pct >= 80:
        return RED
    if pct >= 50:
        return YELLOW
    return GREEN


# Type scale shared by all widgets.
TITLE = 20
BODY = 20
BIG = 60  # headline numbers
PAD = 14  # inner horizontal padding of a card
RADIUS = 4  # card corner radius
BAR_RADIUS = 3


_frameless = False


@contextmanager
def frameless():
    """Inside this block card() skips its background: the widget sits in a shared [[card]]."""
    global _frameless
    _frameless = True
    try:
        yield
    finally:
        _frameless = False


def card_rect(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int) -> None:
    d.rounded_rectangle((x + 2, y + 2, x + w - 3, y + h - 3), radius=RADIUS, fill=CARD)


def card(d: ImageDraw.ImageDraw, w: int, h: int, title: str | None = None) -> int:
    """Draw the card background; returns the y where content should start."""
    if not _frameless:
        card_rect(d, 0, 0, w, h)
    if title:
        d.text((PAD, 10), title, font=font(TITLE, "bold"), fill=MUTED)
        return 36
    return 10


def _box(x: float, y: float, w: float, h: float) -> tuple[int, int, int, int]:
    """Snap a box by its edges: (x, y, w, h) in whole pixels, each edge px()-rounded."""
    x0, y0 = px(x), px(y)
    return x0, y0, px(x + w) - x0, px(y + h) - y0


def bar(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, pct: float, color=None,
        step: float = 1.0) -> None:
    """Bar whose fill is exact to the pixel, quantized to `step` percent. Its edges are
    snapped (not x and w apart), so a bar ends on the pixel its right edge rounds to."""
    x, y, w, h = _box(x, y, w, h)
    if w <= 0 or h <= 0:
        return
    pct = max(0.0, min(100.0, round(pct / step) * step))
    r = min(BAR_RADIUS, h // 2)
    # PIL rectangles include their end pixel, so (x, x + w - 1) is exactly w pixels wide
    d.rounded_rectangle((x, y, x + w - 1, y + h - 1), radius=r, fill=TRACK)
    fill_w = px(w * pct / 100)
    if fill_w <= 0:
        return
    # Clip the fill to the bar outline so small values keep their true width
    # instead of being inflated to a full rounded cap.
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
    mask.paste(0, (fill_w, 0, w, h))
    d._image.paste(color or level_color(pct), (x, y), mask)


def vbar(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, pct: float, color, step: float = 1.0) -> None:
    """Vertical bar filling bottom-up, exact to the pixel, quantized to `step` percent."""
    x, y, w, h = _box(x, y, w, h)
    if w <= 0 or h <= 0:
        return
    pct = max(0.0, min(100.0, round(pct / step) * step))
    r = min(BAR_RADIUS, w // 2)
    d.rounded_rectangle((x, y, x + w - 1, y + h - 1), radius=r, fill=TRACK)
    fill_h = px(h * pct / 100)
    if fill_h <= 0:
        return
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
    mask.paste(0, (0, 0, w, h - fill_h))
    d._image.paste(color, (x, y), mask)


def blend(a, b, k: float) -> tuple[int, int, int]:
    """Mix color a toward b by fraction k."""
    return tuple(round(x + (y - x) * k) for x, y in zip(a, b))


def sparkline(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, values, vmax: float = 100.0,
              color=None, dim: bool = False) -> None:
    """Area graph. dim=True draws it faint, for use as a background behind text."""
    color = color or ACCENT
    values = list(values)
    if len(values) < 2:
        return
    x, y, w, h = _box(x, y, w, h)
    vmax = max(vmax, max(values), 1e-9)
    line = blend(CARD, color, 0.5 if dim else 1.0)
    fill = blend(CARD, color, 0.15 if dim else 0.33)
    # PIL doesn't anti-alias lines/polygons: draw at SS× over the existing pixels, then box-downsample.
    SS = 4
    target = d._image
    layer = target.crop((x, y - 2, x + w, y + h + 2)).resize((w * SS, (h + 4) * SS), Image.NEAREST)
    ld = ImageDraw.Draw(layer)
    step = w * SS / (len(values) - 1)
    base = (h + 2) * SS
    half = SS  # half the 2 px line: keep the whole line inside the graph, so at 0 % its
    # bottom edge sits on the fill's baseline instead of hanging 1 px below it
    pts = [(i * step, base - half - (v / vmax) * (h * SS - 2 * half)) for i, v in enumerate(values)]
    ld.polygon([(0, base), *pts, (w * SS, base)], fill=fill)
    ld.line(pts, fill=line, width=2 * SS, joint="curve")
    target.paste(layer.resize((w, h + 4), Image.BOX), (x, y - 2))


def ring(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, width: float, pct: float | None,
         color, track=None) -> None:
    """Annulus gauge filled clockwise from 12 o'clock (anti-aliased by supersampling).
    pct=None draws only the track (no data)."""
    SS = 4
    box = (px(cx - r - 1), px(cy - r - 1), px(cx + r + 1), px(cy + r + 1))
    target = d._image
    size = (box[2] - box[0], box[3] - box[1])
    layer = target.crop(box).resize((size[0] * SS, size[1] * SS), Image.NEAREST)
    ld = ImageDraw.Draw(layer)
    o = SS  # 1 px margin in layer coordinates
    bounds = (o, o, layer.width - o - 1, layer.height - o - 1)
    ld.arc(bounds, 0, 360, fill=track or TRACK, width=px(width * SS))
    if pct:
        ld.arc(bounds, -90, -90 + 360 * max(0.0, min(100.0, pct)) / 100, fill=color, width=px(width * SS))
    target.paste(layer.resize(size, Image.BOX), box[:2])


def cache_color(pct: float | None):
    """Cache hit health: green >= 90 %, yellow >= 70 %, red below; muted when unknown."""
    if pct is None:
        return MUTED
    return GREEN if pct >= 90 else YELLOW if pct >= 70 else RED


def pct_text(v: float) -> str:
    """Percent label capped at 99 so it never needs a third digit."""
    return capped(v, 2) + "%"


# Fixed-width number labels: values are capped to a digit budget and space-padded
# (the font is monospace), so neighbouring text never shifts when a digit appears.
class Rolling:
    """Rolling mean over the last n samples, so readouts don't jitter every refresh."""

    def __init__(self, n: int):
        from collections import deque
        self.values = deque(maxlen=max(1, int(n)))

    def add(self, v: float) -> float:
        self.values.append(v)
        return sum(self.values) / len(self.values)


def smooth_samples(options: dict, interval: float, default_seconds: float = 2.0) -> int:
    """Window size in samples for a widget's `smooth` option (seconds; 0 disables)."""
    value = options.get("smooth", default_seconds)
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("smooth must be a finite number >= 0 seconds") from exc
    if isinstance(value, bool) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError("smooth must be a finite number >= 0 seconds")
    return max(1, round(seconds / interval))


def capped(v: float, digits: int) -> str:
    return f"{max(0, min(10 ** digits - 1, round(v))):>{digits}}"


def temp_text(v: float, unit: str = "°C") -> str:
    return capped(v, 2) + unit


def watts_text(v: float, digits: int = 2) -> str:
    return capped(v, digits) + "W"


def big_pct(d: ImageDraw.ImageDraw, x: int, y: int, v: float, size: int = BIG, color=None) -> None:
    """Headline percentage, right-aligned to a fixed "99%" width so the % sign never moves."""
    f = font(size, "bold")
    text_right(d, x + d.textlength("99%", font=f), y, pct_text(v), f, color or level_color(v))


def role(name: str):
    """Palette color by role name ("accent", "cyan", ...), resolved at draw time so theme switches apply."""
    return globals()[name.upper()]


def ramp(value: float, warn: float, crit: float, base=None, start: float | None = None):
    """Gradual warning color: base until `start` (default warn/2), fading to yellow at warn,
    then to red at crit."""
    base = base or TEXT
    start = warn / 2 if start is None else start
    if value <= start:
        return base
    if value <= warn:
        return blend(base, YELLOW, (value - start) / max(warn - start, 1e-9))
    if value <= crit:
        return blend(YELLOW, RED, (value - warn) / max(crit - warn, 1e-9))
    return RED


def threshold_color(v: float | None, warn: float, crit: float):
    if v is None or v < warn:
        return TEXT
    return RED if v >= crit else YELLOW


def usage_bar(d: ImageDraw.ImageDraw, w: int, h: int, pct: float, used: int, total: int, color,
              numbers: bool = True) -> None:
    """Bottom row of a card: bar with "used/total" right of it, or, when that would leave the
    bar under half the card, "used/total" just above a full-width bar. numbers=False: bar only."""
    if not numbers:
        bar(d, PAD, h - 24, w - 2 * PAD, 14, pct, color)
        return
    f = font(18, "bold")
    label = f"{human_bytes(used)}/{human_bytes(total)}"
    widest = d.textlength(f"999.9M/{human_bytes(total)}", font=f)  # reserved so the bar never changes length
    if w - 2 * PAD - widest - 10 >= (w - 2 * PAD) / 2:
        text_right(d, w - PAD, h - 28, label, f, TEXT)
        bar(d, PAD, h - 24, w - 2 * PAD - widest - 10, 14, pct, color)
    else:
        text_right(d, w - PAD, h - 50, label, f, TEXT)
        bar(d, PAD, h - 24, w - 2 * PAD, 14, pct, color)


def fields_right(d: ImageDraw.ImageDraw, right: float, y: int, fields, fnt, gap: str = "  ") -> None:
    """Draw [(text, color), ...] right-aligned as one line, each field in its own color."""
    for text, color in reversed(fields):
        text_right(d, right, y, text, fnt, color)
        right -= d.textlength(text + gap, font=fnt)


ICON_BOX = 26  # longest side, in px, of a card's icon label


def is_glyph(label: str) -> bool:
    """A single private-use character: a Nerd Font icon rather than a word."""
    return len(label) == 1 and (0xE000 <= ord(label) <= 0xF8FF or ord(label) >= 0xF0000)


def glyph_icon(d: ImageDraw.ImageDraw, x: float, cy: float, glyph: str, box: int = ICON_BOX,
               fill=None, centre_x: bool = False) -> None:
    """Draw an icon glyph scaled so its ink's longest side is `box` px, whatever icon set it
    comes from, with the ink vertically centred on cy and starting at x (or centred on x)."""
    x0, y0, x1, y1 = d.textbbox((0, 0), glyph, font=font(100))
    f = font(max(6, px(100 * box / max(x1 - x0, y1 - y0, 1))))
    x0, y0, x1, y1 = d.textbbox((0, 0), glyph, font=f)
    left = x - (x0 + x1) / 2 if centre_x else x - x0
    d.text((left, cy - (y0 + y1) / 2), glyph, font=f, fill=fill or MUTED)


def headline_centre(y: int, size: int = 30) -> float:
    """Vertical centre of the digits of a stat_line drawn at y."""
    _, y0, _, y1 = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, y), "0", font=font(size, "bold"))
    return (y0 + y1) / 2


def graph_with_bar(d: ImageDraw.ImageDraw, w: int, h: int, size: int, history, color,
                   pct: float, bar_color, fields, y: int = 6, gap: str = " ") -> None:
    """Body of a card under its stat_line at y: a memory bar in line with the headline,
    from just right of its icon to just left of its readings (`fields`, fixed width), then
    the full-width history graph from just under the digits (100 % touches their bottom)
    to the card's bottom inset."""
    f = font(size, "bold")
    _, _, _, digits = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, y), "0", font=f)
    readings = d.textlength(gap.join(text for text, _ in fields), font=f)
    left = PAD + ICON_BOX + 10
    bar(d, left, px(headline_centre(y, size) - 7), w - PAD - readings - 14 - left, 14, pct, bar_color)
    top, bottom = px(digits) + 4, h - 10
    sparkline(d, PAD, top, w - 2 * PAD, bottom - top, history, color=color, dim=True)


def stat_line(d: ImageDraw.ImageDraw, w: int, y: int, label: str, fields, size: int = 30, gap: str = " ") -> None:
    """Card headline: label on the left, fixed-width colored readings right-aligned. An icon
    label is drawn at a common size (ICON_BOX), centred on the digits."""
    f = font(size, "bold")
    if is_glyph(label):
        glyph_icon(d, PAD, headline_centre(y, size), label)
    else:
        d.text((PAD, y), label, font=f, fill=MUTED)
    fields_right(d, w - PAD, y, fields, f, gap=gap)


ONE_SQUEEZE = 0.62  # width of a condensed leading "1" in clock times


def time_right(d: ImageDraw.ImageDraw, right: float, y: float, text: str, fnt, fill) -> None:
    """Right-aligned clock time whose leading "1" (10-12 o'clock) is drawn condensed, so the
    space reserved for a two-digit hour is only as wide as a narrow "1", not a full digit."""
    if not (len(text) == 5 and text[0] == "1"):
        text_right(d, right, y, text, fnt, fill)
        return
    rest = text[1:]
    rest_w = d.textlength(rest, font=fnt)
    d.text((right - rest_w, y), rest, font=fnt, fill=fill)
    adv = fnt.getlength("1")
    glyph = Image.new("RGBA", (math.ceil(adv), math.ceil(fnt.size * 1.3)), (0, 0, 0, 0))
    ImageDraw.Draw(glyph).text((0, 0), "1", font=fnt, fill=fill)
    glyph = glyph.resize((px(glyph.width * ONE_SQUEEZE), glyph.height), Image.LANCZOS)
    d._image.paste(glyph, (px(right - rest_w - glyph.width - 2), px(y)), glyph)


def time_width(d: ImageDraw.ImageDraw, fnt) -> float:
    """Widest time_right() output ("12:59" with its condensed 1)."""
    return d.textlength("2:59", font=fnt) + fnt.getlength("1") * ONE_SQUEEZE + 2


def text_right(d: ImageDraw.ImageDraw, right: int, y: int, text: str, fnt, fill=TEXT) -> None:
    d.text((right - d.textlength(text, font=fnt), y), text, font=fnt, fill=fill)


def human_count(n: float) -> str:
    """At most 3 significant digits and 5 chars: 3.4M, 18.5K, 185K, 123M, 870."""
    units = (("B", 1e9), ("M", 1e6), ("K", 1e3))
    for i, (unit, size) in enumerate(units):
        if n >= size:
            v = n / size
            s = f"{v:.1f}" if v < 99.95 else f"{v:.0f}"
            if s == "1000" and i > 0:  # 999.6K rounds up: promote to the next unit
                return f"1.0{units[i - 1][0]}"
            return s + unit
    return f"{n:.0f}" if n < 999.5 else "1.0K"


STALE = "\U000F0955"  # clock with "!": data is older than it should be


def stale_mark(d: ImageDraw.ImageDraw, x: float, y: float, size: int = 22) -> None:
    """Small yellow clock-alert glyph used instead of 'updated Xm ago' text."""
    d.text((x, y), STALE, font=font(size), fill=YELLOW)


def fit_font(d: ImageDraw.ImageDraw, text: str, size: int, max_width: float, weight: str = "bold"):
    """Largest font <= size whose rendering of text fits max_width (shrink-to-fit)."""
    while size > 8 and d.textlength(text, font=font(size, weight)) > max_width:
        size -= 2
    return font(size, weight)


def duration_text(seconds: float) -> str:
    """Compact age: '45s', '12m', '3h', '2d'."""
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return f"{int(seconds)}s"


def fit_text(d: ImageDraw.ImageDraw, text: str, fnt, width: float) -> str:
    """Truncate with an ellipsis so text fits in width pixels."""
    if d.textlength(text, font=fnt) <= width:
        return text
    while text and d.textlength(text + "…", font=fnt) > width:
        text = text[:-1]
    return text + "…"


def human_bytes(n: float, suffix: str = "", min_unit: str = "B") -> str:
    """1536 -> '1.5K'. min_unit="K" keeps small values in K (e.g. '0.2K') instead of dropping to bytes."""
    units = ("B", "K", "M", "G", "T")
    for unit in units:
        if (abs(n) < 1024 and units.index(unit) >= units.index(min_unit)) or unit == "T":
            return f"{n:.0f}{unit}{suffix}" if unit == "B" else f"{n:.1f}{unit}{suffix}"
        n /= 1024
    return f"{n:.1f}T{suffix}"
