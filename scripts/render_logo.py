"""Render Housekeeperr logo variants as PNGs from the same primitives as
static/favicon.svg. Run after editing the SVG to refresh raster outputs:

    python scripts/render_logo.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BG = (15, 23, 42, 255)        # #0f172a
HOUSE = (77, 156, 246, 255)   # #4d9cf6
TEXT = (230, 237, 243, 255)   # #e6edf3
MUTED = (139, 148, 158, 255)  # #8b949e

ROOT = Path(__file__).resolve().parent.parent


def _house_polygon(scale: float, offset: tuple[float, float] = (0, 0)) -> list[tuple[float, float]]:
    # Matches static/favicon.svg viewBox 0..512
    base = [(256, 96), (96, 232), (96, 424), (416, 424), (416, 232)]
    ox, oy = offset
    return [(x * scale + ox, y * scale + oy) for x, y in base]


def _play_polygon(scale: float, offset: tuple[float, float] = (0, 0)) -> list[tuple[float, float]]:
    base = [(218, 268), (340, 332), (218, 396)]
    ox, oy = offset
    return [(x * scale + ox, y * scale + oy) for x, y in base]


def _square_logo(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = int(size * (96 / 512))
    d.rounded_rectangle((0, 0, size, size), radius=radius, fill=BG)
    scale = size / 512
    d.polygon(_house_polygon(scale), fill=HOUSE)
    d.polygon(_play_polygon(scale), fill=BG)
    return img


def _find_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",   # Segoe UI Bold
        "C:/Windows/Fonts/seguisb.ttf",    # Segoe UI Semibold
        "C:/Windows/Fonts/arialbd.ttf",    # Arial Bold
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

    # Logo block on the left
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
    d.text((text_x, logo_y + logo_size - 24), badge, font=badge_font, fill=HOUSE)

    return img


def main() -> None:
    static_dir = ROOT / "static"
    assets_dir = ROOT / ".github"
    static_dir.mkdir(exist_ok=True)
    assets_dir.mkdir(exist_ok=True)

    # Small square logo for README header
    small = _square_logo(512)
    small.save(static_dir / "logo.png", optimize=True)

    # Social preview for GitHub repo settings
    social = _social_preview()
    social.save(assets_dir / "social-preview.png", optimize=True)

    print("Wrote", static_dir / "logo.png", small.size)
    print("Wrote", assets_dir / "social-preview.png", social.size)


if __name__ == "__main__":
    main()
