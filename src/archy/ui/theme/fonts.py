from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase

# The three bundled fonts (OFL): Inter for body/UI, JetBrains Mono for sizes and
# paths, Sora for titles/brand. load_fonts() registers them at startup so the
# QSS family names (see palette.UI_FAMILY / MONO_FAMILY / DISPLAY_FAMILY)
# resolve. Files live in resources/fonts/ next to the package.
_FONT_DIR = Path(__file__).resolve().parent.parent.parent / "resources" / "fonts"


def load_fonts() -> None:
    for name in ("Inter.ttf", "JetBrainsMono.ttf", "Sora.ttf"):
        path = _FONT_DIR / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
