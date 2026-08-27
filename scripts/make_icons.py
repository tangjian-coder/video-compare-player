"""Render assets/icon.svg design into multi-size PNGs + .ico with Pillow.

Redraws the icon geometry natively (no SVG rasterizer dependency): a 1024px
master canvas keeps edges crisp when downsampling to 16px.

Usage:  python scripts/make_icons.py
Outputs: assets/icon-256.png ... icon-16.png, assets/icon.ico
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parents[1] / "assets"
MASTER = 1024  # high-res master; everything else is downscaled from it

# macOS dark palette (same hex values as assets/icon.svg).
BG_TOP = (58, 58, 60)  # #3a3a3c
BG_BOTTOM = (28, 28, 30)  # #1c1c1e
BLUE = (10, 132, 255)  # #0a84ff
GREEN = (48, 209, 88)  # #30d158
YELLOW = (255, 214, 10)  # #ffd60a
WHITE = (255, 255, 255)
DARK = (28, 28, 30)  # #1c1c1e


def _vertical_gradient(
    size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]
) -> Image.Image:
    """A square vertical gradient image."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        f = y / (size - 1)
        color = tuple(round(top[i] + (bottom[i] - top[i]) * f) for i in range(3))
        for x in range(size):
            px[x, y] = color  # type: ignore[index]
    return img


def draw_master() -> Image.Image:
    """Draw the icon at MASTER resolution with rounded-rect clipping."""
    s = MASTER
    img = _vertical_gradient(s, BG_TOP, BG_BOTTOM).convert("RGBA")

    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [int(s * 4 / 128), int(s * 4 / 128), int(s * 124 / 128), int(s * 124 / 128)],
        radius=int(s * 28 / 128),
        fill=255,
    )
    img.putalpha(mask)

    d = ImageDraw.Draw(img)
    k = s / 128.0  # svg viewBox units -> pixels

    def rr(
        x0: float, y0: float, x1: float, y1: float, r: float, fill: tuple[int, int, int, int]
    ) -> None:
        d.rounded_rectangle([x0 * k, y0 * k, x1 * k, y1 * k], radius=r * k, fill=fill)

    # Panes A (blue, upper-left) and B (green, lower-right).
    rr(18, 30, 64, 70, 8, (*BLUE, 255))
    rr(64, 44, 110, 84, 8, (*GREEN, int(255 * 0.92)))

    # Step arrows pointing toward each other (stroke ~3 svg units).
    lw = int(3 * k)

    def arrow(x: float, y: float, direction: int) -> None:
        """A bidirectional-step cross: horizontal bar + vertical bar."""
        cx, cy = x * k, y * k
        arm = 9 * k
        d.line([(cx - arm, cy), (cx + arm, cy)], fill=(*WHITE, 255), width=lw)
        d.line([(cx, cy - arm), (cx, cy + arm)], fill=(*WHITE, 255), width=lw)

    arrow(46, 62, -1)  # pane A center
    arrow(78, 76, 1)  # pane B center

    # Sync anchor: yellow ring with dark core (diamond at 16px is mushy).
    cx, cy = 64 * k, 60 * k
    outer = 10 * k
    inner = 4.5 * k
    d.ellipse([cx - outer, cy - outer, cx + outer, cy + outer], fill=(*YELLOW, 255))
    d.ellipse([cx - inner, cy - inner, cx + inner, cy + inner], fill=(*DARK, 255))
    return img


def main() -> None:
    """Render master and emit all sizes."""
    ASSETS.mkdir(exist_ok=True)
    master = draw_master()
    sizes = [256, 128, 64, 48, 32, 16]
    pngs = []
    for size in sizes:
        img = master.resize((size, size), Image.LANCZOS)
        path = ASSETS / f"icon-{size}.png"
        img.save(path)
        pngs.append(path)
        logger.info("wrote %s", path)

    ico_path = ASSETS / "icon.ico"
    master.resize((256, 256), Image.LANCZOS).save(
        ico_path, format="ICO", sizes=[(s, s) for s in (256, 64, 48, 32, 16)]
    )
    logger.info("wrote %s", ico_path)


if __name__ == "__main__":
    main()
