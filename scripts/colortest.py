"""Color-fidelity test: the same swatches sent via four different pixel encodings.

Row A is sent as a full frame (4-byte BGRA, the known-good path). Rows B-D are
partial updates using different encodings; whichever matches row A is the
correct partial-update format for this screen's ROM.

Stop the dashboard first (pkill -x turzx), then: .venv/bin/python scripts/colortest.py
"""

import numpy as np
from PIL import Image, ImageDraw

from turzx import driver as D
from turzx import theme as t

t.load("tokyo-night")
ROLES = ["BG", "CARD", "TRACK", "MUTED", "TEXT", "ACCENT", "GREEN", "YELLOW", "ORANGE", "RED", "CYAN", "MAGENTA"]
ROWS = [
    ("A", "full frame (reference)"),
    ("B", "partial, plain BGR (current)"),
    ("C", "partial, BGR + opaque alpha bits"),
    ("D", "partial, BGRA 4-byte"),
]
ROW_H, LABEL_W = 120, 50
SW = (D.WIDTH - LABEL_W) // len(ROLES)


def row_image(letter: str, caption: str) -> Image.Image:
    img = Image.new("RGB", (D.WIDTH, ROW_H), (0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((12, 36), letter, font=t.font(40, "bold"), fill=(255, 255, 255))
    for i, role in enumerate(ROLES):
        x = LABEL_W + i * SW
        d.rectangle((x, 0, x + SW - 4, ROW_H - 30), fill=getattr(t, role))
    d.text((LABEL_W, ROW_H - 26), caption, font=t.font(18), fill=(255, 255, 255))
    return img


def bgr_plain(image, bgra):
    rgba = np.asarray(image.convert("RGBA"))
    return np.take(rgba, (2, 1, 0), axis=-1).tobytes(), 3


def bgr_opaque(image, bgra):
    """[6-bit B + 2 alpha bits, 6-bit G + 2 alpha bits, 8-bit R], alpha forced opaque."""
    rgba = np.asarray(image.convert("RGBA"))
    out = np.take(rgba, (2, 1, 0), axis=-1).copy()
    out[..., 0] = (out[..., 0] & 0xFC) | 0x03
    out[..., 1] = (out[..., 1] & 0xFC) | 0x02
    return out.tobytes(), 3


def bgra4(image, bgra):
    rgba = np.asarray(image.convert("RGBA"))
    return np.take(rgba, (2, 1, 0, 3), axis=-1).tobytes(), 4


def main():
    disp = D.TurzxDisplay(brightness=60)
    full = Image.new("RGB", disp.size, (0, 0, 0))
    full.paste(row_image(*ROWS[0]), (0, 0))
    disp.show_full(full)
    orig = D._pixels
    try:
        for (letter, caption), encoder in zip(ROWS[1:], (bgr_plain, bgr_opaque, bgra4)):
            D._pixels = encoder
            y = ROW_H * ("ABCD".index(letter))
            disp.show_region(row_image(letter, caption), 0, y)
            print(f"row {letter}: needs_full={disp.needs_full}")
            disp.needs_full = False
    finally:
        D._pixels = orig
    disp.close()


if __name__ == "__main__":
    main()
