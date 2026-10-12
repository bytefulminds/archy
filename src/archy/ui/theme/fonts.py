from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase

# The three bundled fonts (OFL): Inter for body/UI, JetBrains Mono for sizes and
# paths, Sora for titles/brand. load_fonts() registers them at startup so the
# QSS family names (see palette.UI_FAMILY / MONO_FAMILY / DISPLAY_FAMILY)
# resolve. Files live in resources/fonts/ next to the package.
_FONT_DIR = Path(__file__).resolve().parent.parent.parent / "resources" / "fonts"


_loaded = False


def load_fonts() -> None:
    global _loaded
    if _loaded:  # the splash screen loads them before theme.apply() does
        return
    _loaded = True
    for name in ("Inter.ttf", "JetBrainsMono.ttf", "Sora.ttf"):
        path = _FONT_DIR / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
