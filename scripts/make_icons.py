"""Make the macOS app icons from the logo (docs/Archy_square.png).

    python scripts/make_icons.py

macOS icons follow Apple's icon grid: on a 1024 x 1024 canvas the rounded
square is 824 x 824, centred, with a soft shadow below it. A logo that fills
the canvas looks a size too big next to the other apps in the Dock. Writes
packaging/archy.icns (the bundle icon) and
src/archy/resources/icons/archy_macos.png (the Dock icon when Archy runs from
source). The Windows and Linux icons use the logo as it is.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "Archy_square.png"
CANVAS = 1024
BODY = 824  # Apple's macOS icon grid
SHADOW_OFFSET = 12
SHADOW_BLUR = 14
SHADOW_OPACITY = 0.3


def macos_icon() -> Image.Image:
    logo = Image.open(SOURCE).convert("RGBA")
    body = logo.crop(logo.getchannel("A").getbbox()).resize((BODY, BODY), Image.LANCZOS)
    left = top = (CANVAS - BODY) // 2
    alpha = body.getchannel("A").point(lambda v: int(v * SHADOW_OPACITY))
    shadow = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    shadow.paste(
        Image.new("RGBA", (BODY, BODY), (0, 0, 0, 255)), (left, top + SHADOW_OFFSET), alpha
    )
    icon = shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    icon.alpha_composite(body, (left, top))
    return icon


def main() -> None:
    icon = macos_icon()
    icon.save(ROOT / "packaging" / "archy.icns", format="ICNS")
    dock = ROOT / "src" / "archy" / "resources" / "icons" / "archy_macos.png"
    icon.resize((512, 512), Image.LANCZOS).save(dock, optimize=True)
    print(f"wrote {ROOT / 'packaging' / 'archy.icns'} and {dock}")


if __name__ == "__main__":
    main()
