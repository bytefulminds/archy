from __future__ import annotations

# The Soft Depth theme, split into small modules a junior can navigate:
#   * palette  - the colours (fixed tokens + the runtime accent) and set_accent
#   * icons    - the inline-SVG icon set and symbol_icon()
#   * stylesheet - the big QSS builder for all the chrome
#   * fonts    - loading the three bundled fonts
#
# Callers keep using this package exactly as before: `from archy.ui import
# theme` then `theme.ACCENT`, `theme.symbol_icon(...)`, `theme.stylesheet()`,
# etc. Colour tokens are served live through __getattr__ (below) so that a
# runtime accent change via set_accent() is reflected everywhere on the next
# read - importing the names eagerly here would freeze them at their startup
# values and silently break live re-theming.
from archy.ui.theme import palette as _palette
from archy.ui.theme.fonts import load_fonts
from archy.ui.theme.icons import symbol_icon
from archy.ui.theme.palette import dark_palette, ratio_color, set_accent
from archy.ui.theme.stylesheet import stylesheet

__all__ = [
    "apply",
    "dark_palette",
    "load_fonts",
    "ratio_color",
    "refresh_app",
    "set_accent",
    "stylesheet",
    "symbol_icon",
]


def __getattr__(name: str) -> object:
    """Serve colour tokens (ACCENT, TEXT_PRIMARY, ACCENT_RGB, MONO_FAMILY, ...)
    live from the palette module, so accent changes are always up to date."""
    try:
        return getattr(_palette, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def apply(app: object) -> None:
    """Load fonts and apply the dark palette + stylesheet to the QApplication."""
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    assert isinstance(app, QApplication)
    load_fonts()
    app.setFont(QFont(_palette.UI_FAMILY, 10))
    app.setStyle("Fusion")  # consistent base across platforms for our palette
    app.setPalette(dark_palette())
    app.setStyleSheet(stylesheet())


def refresh_app() -> None:
    """Re-apply the palette + stylesheet to the running app so an accent change
    takes effect live for all widgets already shown."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        app.setPalette(dark_palette())
        app.setStyleSheet(stylesheet())
