#!/usr/bin/env python3
"""Render the listing icon: 512x512, flat background, the letters "OA".

Writes docs/listing/icon.png and copies it over the two plugin asset paths
(plugins/odoo-assistant/assets/icon.png and logo.png), which the OpenAI
plugin manifest references. Idempotent: the same input renders the same
bytes, so re-running the script only rewrites identical files. The mark is
plain text on a flat field — no Odoo logo or trademark is used.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 512
BACKGROUND = (30, 41, 59)  # flat slate, no gradient
FOREGROUND = (248, 250, 252)
TEXT = "OA"

REPO = Path(__file__).resolve().parent.parent
TARGETS = (
    REPO / "docs" / "listing" / "icon.png",
    REPO / "plugins" / "odoo-assistant" / "assets" / "icon.png",
    REPO / "plugins" / "odoo-assistant" / "assets" / "logo.png",
)

# Bold system faces, first match wins; load_default(size=) covers the rest.
FONT_CANDIDATES = (
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _font(size: int):
    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def render() -> Image.Image:
    image = Image.new("RGB", (SIZE, SIZE), BACKGROUND)
    draw = ImageDraw.Draw(image)
    font = _font(300)
    # textbbox works for every font, truetype or bitmap; anchor="mm" would not.
    left, top, right, bottom = draw.textbbox((0, 0), TEXT, font=font)
    xy = ((SIZE - (right - left)) / 2 - left, (SIZE - (bottom - top)) / 2 - top)
    draw.text(xy, TEXT, font=font, fill=FOREGROUND)
    return image


def main() -> None:
    payload = render()
    for target in TARGETS:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload.save(target, format="PNG")
        print(f"wrote {target.relative_to(REPO)} ({SIZE}x{SIZE})")


if __name__ == "__main__":
    main()
