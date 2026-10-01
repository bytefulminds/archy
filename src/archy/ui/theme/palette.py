from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

# The colour vocabulary of the Soft Depth theme, plus the runtime-accent logic.
#
# Everything here is plain data and pure functions - no widgets. Two kinds of
# value live in this module:
#   * fixed tokens (backgrounds, text colours, borders, glass fills) that never
#     change at runtime;
#   * the accent pair (ACCENT / SECONDARY and their shades) which set_accent()
#     recomputes from the user's chosen colours.
# The stylesheet builder (stylesheet.py) and the palette builder (dark_palette
# below) read these names, so changing them here re-themes the whole app.

# --- fixed palette tokens (the "Soft Depth" dark design) ---
BASE = "#0B0F14"
SURFACE = "#151B22"
SURFACE_ALT = "#1A212A"
ELEVATED = "#1F2731"
OVERLAY = "#28323E"

TEXT_PRIMARY = "#EAF1F5"
TEXT_SECONDARY = "#98A6B0"
TEXT_DISABLED = "#5C6773"

BORDER = "#28323D"
DIVIDER = "#1D2630"
BORDER_STRONG = "#3A4650"

# Accent pair. These are runtime-configurable: set_accent() recomputes them (and
# the derived rgba fills below) from a user-chosen primary + secondary, and the
# whole app is re-styled. The values here are the defaults, overwritten at
# startup from settings. ACCENT is the main interactive accent (selection,
# buttons, focus); SECONDARY is a complementary accent used to keep the palette
# balanced (brand mark, active navigation).
ACCENT = "#45C0CD"
ACCENT_HOVER = "#5AD0DC"
ACCENT_PRESSED = "#2E9AA6"
ACCENT_ON = "#07131A"
SELECTION_BG = "#153139"

SECONDARY = "#CD7E45"
SECONDARY_HOVER = "#DA9160"
SECONDARY_PRESSED = "#A6632F"
SECONDARY_ON = "#1A0F07"

SUCCESS = "#4FB477"
WARNING = "#E0A54B"
ERROR = "#E56E6E"

# Window gradient + glass fills (used in the QSS). Kept as literals so the QSS
# can use rgba() directly (QColor() only takes the hex tokens above).
_WIN_TOP = "#151C23"
_WIN_MID = "#10151B"
_WIN_BOTTOM = "#0D1218"
_GLASS = "rgba(255, 255, 255, 0.05)"
_GLASS_SOFT = "rgba(255, 255, 255, 0.03)"
_GLASS_STRONG = "rgba(255, 255, 255, 0.075)"
_GLASS_TOP = "rgba(255, 255, 255, 0.10)"
_EDGE = "rgba(255, 255, 255, 0.08)"
_EDGE_SOFT = "rgba(255, 255, 255, 0.05)"
_EDGE_STRONG = "rgba(255, 255, 255, 0.14)"
_HILITE = "rgba(255, 255, 255, 0.10)"
# Accent-tinted fills, recomputed by set_accent() from the current ACCENT.
_ACCENT_FILL = "rgba(69, 192, 205, 0.14)"
_ACCENT_FILL_HOVER = "rgba(69, 192, 205, 0.08)"
_SCRIM = "rgba(10, 14, 18, 0.5)"
_SCRIM_SOFT = "rgba(10, 14, 18, 0.42)"
_WINDOW_RADIUS = 18

# 5-step scale for the compression Ratio column (0% -> 100% compressed).
_RATIO_SCALE = (
    (0.20, "#8C96A3"),
    (0.45, "#B7C08C"),
    (0.70, "#8FC79A"),
    (0.90, "#5FC2A0"),
    (1.01, "#45C0CD"),
)

UI_FAMILY = "Inter"
MONO_FAMILY = "JetBrains Mono"
DISPLAY_FAMILY = "Sora"

# Main interactive accent as an (r, g, b) tuple, for painters that build QColors
# directly (e.g. the grid tile glow/ring). Recomputed by set_accent().
ACCENT_RGB: tuple[int, int, int] = (69, 192, 205)


# --------------------------------------------------------- colour helpers
def _to_rgb(hexstr: str) -> tuple[int, int, int]:
    h = hexstr.lstrip("#")
    if len(h) >= 6:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 69, 192, 205


def _to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def _mix(a: str, b: str, t: float) -> str:
    """Mix hex ``a`` toward hex ``b`` by fraction ``t`` (0..1)."""
    ar, ag, ab = _to_rgb(a)
    br, bg, bb = _to_rgb(b)
    return _to_hex((ar + (br - ar) * t, ag + (bg - ag) * t, ab + (bb - ab) * t))


def _lighten(hexstr: str, t: float) -> str:
    return _mix(hexstr, "#FFFFFF", t)


def _darken(hexstr: str, t: float) -> str:
    return _mix(hexstr, "#000000", t)


def _luminance(hexstr: str) -> float:
    r, g, b = _to_rgb(hexstr)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _on_color(hexstr: str) -> str:
    """A readable text/icon color to place on top of ``hexstr``: a deep tint of
    the same hue for light accents, a pale tint for dark ones."""
    return _darken(hexstr, 0.90) if _luminance(hexstr) > 0.55 else _lighten(hexstr, 0.90)


def _rgba(hexstr: str, alpha: float) -> str:
    r, g, b = _to_rgb(hexstr)
    return f"rgba({r}, {g}, {b}, {alpha})"


def set_accent(primary: str | None = None, secondary: str | None = None) -> None:
    """Recompute the accent tokens from a chosen primary/secondary color. Call
    before building widgets (startup) and again on a live change, followed by
    refresh_app() to restyle everything already on screen."""
    global ACCENT, ACCENT_HOVER, ACCENT_PRESSED, ACCENT_ON, SELECTION_BG, ACCENT_RGB
    global SECONDARY, SECONDARY_HOVER, SECONDARY_PRESSED, SECONDARY_ON
    global _ACCENT_FILL, _ACCENT_FILL_HOVER
    if primary:
        ACCENT = primary.upper()
        ACCENT_HOVER = _lighten(ACCENT, 0.18)
        ACCENT_PRESSED = _darken(ACCENT, 0.22)
        ACCENT_ON = _on_color(ACCENT)
        SELECTION_BG = _mix(ACCENT, BASE, 0.80)
        ACCENT_RGB = _to_rgb(ACCENT)
        _ACCENT_FILL = _rgba(ACCENT, 0.14)
        _ACCENT_FILL_HOVER = _rgba(ACCENT, 0.08)
    if secondary:
        SECONDARY = secondary.upper()
        SECONDARY_HOVER = _lighten(SECONDARY, 0.18)
        SECONDARY_PRESSED = _darken(SECONDARY, 0.22)
        SECONDARY_ON = _on_color(SECONDARY)


def ratio_color(ratio: float) -> QColor:
    """Color for a compression ratio in [0, 1] using the 5-step scale."""
    for threshold, hexcolor in _RATIO_SCALE:
        if ratio < threshold:
            return QColor(hexcolor)
    return QColor(_RATIO_SCALE[-1][1])


def dark_palette() -> QPalette:
    """The app-wide QPalette so item views render dark at native speed."""
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(BASE))
    p.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_PRIMARY))
    p.setColor(QPalette.ColorRole.Base, QColor(SURFACE))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE_ALT))
    p.setColor(QPalette.ColorRole.Text, QColor(TEXT_PRIMARY))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(OVERLAY))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT_PRIMARY))
    p.setColor(QPalette.ColorRole.Button, QColor(ELEVATED))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT_PRIMARY))
    p.setColor(QPalette.ColorRole.BrightText, QColor("#FFFFFF"))
    p.setColor(QPalette.ColorRole.Highlight, QColor(SELECTION_BG))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(TEXT_PRIMARY))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_DISABLED))
    p.setColor(QPalette.ColorRole.Link, QColor(ACCENT))

    disabled = QPalette.ColorGroup.Disabled
    p.setColor(disabled, QPalette.ColorRole.Text, QColor(TEXT_DISABLED))
    p.setColor(disabled, QPalette.ColorRole.WindowText, QColor(TEXT_DISABLED))
    p.setColor(disabled, QPalette.ColorRole.ButtonText, QColor(TEXT_DISABLED))
    return p
