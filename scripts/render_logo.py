"""Render Housekeeperr logo variants as PNGs from the same primitives as
static/favicon.svg (orange-on-black Swiffer-style duster). Run after
editing the SVG to refresh raster outputs:

    python scripts/render_logo.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BG = (0, 0, 0, 255)              # pure black
ACCENT = (249, 115, 22, 255)     # orange-500   #f97316
ACCENT_DEEP = (194, 65, 12, 255) # orange-700   #c2410c
TEXT = (245, 245, 245, 255)      # almost white
MUTED = (154, 154, 154, 255)     # neutral grey

ROOT = Path(__file__).resolve().parent.parent


def _thick_line(d: ImageDraw.ImageDraw, x1: float, y1: float, x2: float, y2: float,
                width: int, color: tuple[int, int, int, int]) -> None:
    """Pillow's line() doesn't do rounded caps; emulate with end-circles."""
    d.line((x1, y1, x2, y2), fill=color, width=width)
    r = width // 2
    d.ellipse((x1 - r, y1 - r, x1 + r, y1 + r), fill=color)
    d.ellipse((x2 - r, y2 - r, x2 + r, y2 + r), fill=color)


# SVG strand endpoints, in 512-unit coords. All originate at (256, 252).
_STRANDS = [
    (256, 74),
    (232, 80), (280, 80),
    (206, 94), (306, 94),
    (178, 116), (334, 116),
    (148, 146), (364, 146),
    (122, 184), (390, 184),
]


def _square_logo(size: int) -> Image.Image:
    """Rendered version of static/favicon.svg at the requested size."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 512
    radius = int(96 * s)
    d.rounded_rectangle((0, 0, size, size), radius=radius, fill=BG)

    stroke_w = max(2, int(18 * s))
    base_x, base_y = 256 * s, 252 * s
    for tx, ty in _STRANDS:
        _thick_line(d, base_x, base_y, tx * s, ty * s, stroke_w, ACCENT)

    # chunky clip head
    d.rounded_rectangle(
        (208 * s, 244 * s, (208 + 96) * s, (244 + 28) * s),
        radius=int(9 * s), fill=ACCENT,
    )
    # ferrule
    d.rounded_rectangle(
        (222 * s, 268 * s, (222 + 68) * s, (268 + 14) * s),
        radius=int(4 * s), fill=ACCENT_DEEP,
    )
    # handle
    d.rounded_rectangle(
        (246 * s, 280 * s, (246 + 20) * s, (280 + 166) * s),
        radius=int(10 * s), fill=ACCENT,
    )
    return img


def _find_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/seguisb.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/Arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _social_preview(width: int = 1280, height: int = 640) -> Image.Image:
    img = Image.new("RGBA", (width, height), BG)
    d = ImageDraw.Draw(img)

    logo_size = 320
    logo = _square_logo(logo_size)
    logo_y = (height - logo_size) // 2
    logo_x = 96
    img.paste(logo, (logo_x, logo_y), logo)

    text_x = logo_x + logo_size + 64
    title_font = _find_font(96)
    tag_font = _find_font(30)

    title = "Housekeeperr"
    d.text((text_x, logo_y + 8), title, font=title_font, fill=TEXT)

    tagline_lines = [
        "Self-hosted Radarr/Sonarr cleanup.",
        "Streaming availability, watch state,",
        "and request history at a glance.",
    ]
    line_h = 42
    start_y = logo_y + 124
    for i, line in enumerate(tagline_lines):
        d.text((text_x, start_y + i * line_h), line, font=tag_font, fill=MUTED)

    badge_font = _find_font(24)
    badge = "github.com/NickyM/Housekeeperr"
    d.text((text_x, logo_y + logo_size - 24), badge, font=badge_font, fill=ACCENT)
    return img


def main() -> None:
    static_dir = ROOT / "static"
    assets_dir = ROOT / ".github"
    static_dir.mkdir(exist_ok=True)
    assets_dir.mkdir(exist_ok=True)

    small = _square_logo(512)
    small.save(static_dir / "logo.png", optimize=True)

    social = _social_preview()
    social.save(assets_dir / "social-preview.png", optimize=True)

    print("Wrote", static_dir / "logo.png", small.size)
    print("Wrote", assets_dir / "social-preview.png", social.size)


if __name__ == "__main__":
    main()
