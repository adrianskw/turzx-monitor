"""Colors, fonts and small drawing primitives shared by widgets."""

from __future__ import annotations

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
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def theme_path(name: str) -> Path:
    """colors.toml for an Omarchy theme name, or the active theme for "current"."""
    if name == "current":
        return CURRENT_THEME
    for base in OMARCHY_THEMES:
        if (path := base / name / "colors.toml").exists():
            return path
    raise SystemExit(f"Omarchy theme {name!r} not found in {', '.join(map(str, OMARCHY_THEMES))}")


def load(name: str) -> None:
    """Adopt the palette of an Omarchy theme, e.g. "tokyo-night" or "current"."""
    colors = tomllib.loads(theme_path(name).read_text())
    g = globals()
    for role, key in ROLES.items():
        if key in colors:
            g[role] = _hex(colors[key])


FONT_DIR = "/usr/share/fonts/TTF"
FONTS = {
    "regular": f"{FONT_DIR}/JetBrainsMonoNerdFont-Regular.ttf",
    "bold": f"{FONT_DIR}/JetBrainsMonoNerdFont-Bold.ttf",
}


@lru_cache(maxsize=None)
def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONTS[weight], size)


ASSETS = Path(__file__).parent / "assets"


@lru_cache(maxsize=None)
def _icon(name: str, size: int) -> Image.Image:
    return Image.open(ASSETS / f"{name}.png").convert("RGBA").resize((size, size), Image.LANCZOS)


def icon(d: ImageDraw.ImageDraw, x: int, y: int, name: str, size: int) -> None:
    """Paste turzx/assets/<name>.png onto the widget image, alpha-blended."""
    img = _icon(name, size)
    d._image.paste(img, (x, y), img)  # ImageDraw keeps a reference to its target image


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


def bar(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, pct: float, color=None,
        step: float = 1.0) -> None:
    """Bar whose fill is exact to the pixel, quantized to `step` percent."""
    x, y, w, h = round(x), round(y), round(w), round(h)
    pct = max(0.0, min(100.0, round(pct / step) * step))
    r = min(BAR_RADIUS, h // 2)
    d.rounded_rectangle((x, y, x + w, y + h), radius=r, fill=TRACK)
    fill_w = round(w * pct / 100)
    if fill_w <= 0:
        return
    # Clip the fill to the bar outline so small values keep their true width
    # instead of being inflated to a full rounded cap.
    mask = Image.new("L", (w + 1, h + 1), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w, h), radius=r, fill=255)
    mask.paste(0, (fill_w, 0, w + 1, h + 1))
    d._image.paste(color or level_color(pct), (x, y), mask)


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
    vmax = max(vmax, max(values), 1e-9)
    step = w / (len(values) - 1)
    pts = [(x + i * step, y + h - (v / vmax) * h) for i, v in enumerate(values)]
    line = blend(CARD, color, 0.5 if dim else 1.0)
    fill = blend(CARD, color, 0.15 if dim else 0.33)
    d.polygon([(x, y + h), *pts, (x + w, y + h)], fill=fill)
    d.line(pts, fill=line, width=2)


def pct_text(v: float) -> str:
    """Percent label capped at 99 so it never needs a third digit."""
    return capped(v, 2) + "%"


# Fixed-width number labels: values are capped to a digit budget and space-padded
# (the font is monospace), so neighbouring text never shifts when a digit appears.
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


def threshold_color(v: float | None, warn: float, crit: float):
    if v is None or v < warn:
        return TEXT
    return RED if v >= crit else YELLOW


def fields_right(d: ImageDraw.ImageDraw, right: float, y: int, fields, fnt, gap: str = "  ") -> None:
    """Draw [(text, color), ...] right-aligned as one line, each field in its own color."""
    for text, color in reversed(fields):
        text_right(d, right, y, text, fnt, color)
        right -= d.textlength(text + gap, font=fnt)


def stat_line(d: ImageDraw.ImageDraw, w: int, y: int, label: str, fields, size: int = 30, gap: str = " ") -> None:
    """Card headline: label on the left, fixed-width colored readings right-aligned."""
    f = font(size, "bold")
    d.text((PAD, y), label, font=f, fill=MUTED)
    fields_right(d, w - PAD, y, fields, f, gap=gap)


def text_right(d: ImageDraw.ImageDraw, right: int, y: int, text: str, fnt, fill=TEXT) -> None:
    d.text((right - d.textlength(text, font=fnt), y), text, font=fnt, fill=fill)


def human_count(n: float) -> str:
    """12345 -> '12.3K', 4.2e6 -> '4.2M'."""
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return f"{n:.0f}"


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
