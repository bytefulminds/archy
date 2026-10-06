from __future__ import annotations

import re

from PySide6.QtCore import QByteArray, QSettings

from archy.i18n import tr

# Thin, typed wrapper over QSettings for Archy's user preferences. QSettings
# persists per-user in the platform-native location (org/app set in app.py), so
# nothing extra needs writing. Every getter has a sensible default so a fresh
# install behaves exactly as before this screen existed.

# View indices match the segmented switch order in the toolbar.
VIEW_KEYS = ("view.list", "view.compact", "view.thumbnails", "view.large_thumbnails")


def view_labels() -> tuple[str, ...]:
    return tuple(tr(key) for key in VIEW_KEYS)


def system_languages() -> list[str]:
    """The user's preferred UI languages, most preferred first ("nl-NL", ...).

    Not ``QLocale.system().name()``: on macOS that is the locale of the running
    app, and a plain Python process only has an English localization, so a
    Dutch Mac reported "en_NL". ``uiLanguages()`` reads the user's own list
    (System Settings > Language & Region on macOS, the display language on
    Windows, LANGUAGE/LANG on Linux)."""
    from PySide6.QtCore import QLocale

    system = QLocale.system()
    return [*system.uiLanguages(), system.name()]


# Default accent pair (Soft Depth teal + a warm complementary secondary).
DEFAULT_ACCENT = "#45C0CD"
DEFAULT_ACCENT2 = "#CD7E45"
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

_DEFAULTS = {
    "ui/language": "auto",  # "auto" (system language, else English) | a code ("nl")
    "view/start": 3,  # Large thumbnails
    "view/compact_density": False,
    "slideshow/seconds": 4,
    # Sort order of every view: a column of the details table (0 Name, 1 Size,
    # 2 Compressed, 3 Ratio, 4 Type, 5 Modified); folders always come first.
    "view/sort_column": 0,
    "view/sort_descending": False,
    "preview/fill": "contain",  # "contain" (whole image) | "cover" (fill, crop)
    "window/remember": True,
    # "memory" (RAM only) | "overflow" (RAM first, rest encrypted on disk) |
    # "disk" (always encrypted temp files). Older versions stored "temp" for
    # what is now "overflow".
    "temp/mode": "memory",
    "temp/location": "system",  # "system" | "custom"
    "temp/dir": "",
    "temp/prune_on_exit": True,
    # Advanced: resources (Settings > Advanced). 0 means Automatic.
    "res/cap_mode": "percent",  # "percent" (of RAM) | "fixed" (GB)
    "res/cap_percent": 75,
    "res/cap_gb": 8,
    "res/reserve_gb": 0,
    "res/folder_mb": 0,
    "res/thumb_mb": 0,
    "res/read_mb": 0,
    "res/video_mb": 0,
    "res/threads": 0,
    "theme/accent": DEFAULT_ACCENT,
    "theme/accent2": DEFAULT_ACCENT2,
}


def _norm_hex(value: object, fallback: str) -> str:
    """Return a normalised #RRGGBB string, dropping any alpha, or the fallback."""
    text = str(value or "").strip()
    if len(text) == 9 and text.startswith("#"):  # #RRGGBBAA -> #RRGGBB
        text = text[:7]
    return text.upper() if _HEX_RE.match(text) else fallback


class Settings:
    def __init__(self) -> None:
        self._q = QSettings()

    # ------------------------------------------------------------ generic
    def _get(self, key: str):  # noqa: ANN202
        return self._q.value(key, _DEFAULTS[key])

    def _get_bool(self, key: str) -> bool:
        v = self._q.value(key, _DEFAULTS[key])
        return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")

    def _get_int(self, key: str) -> int:
        try:
            return int(self._q.value(key, _DEFAULTS[key]))
        except (TypeError, ValueError):
            return int(_DEFAULTS[key])

    @property
    def language(self) -> str:
        return str(self._get("ui/language") or "auto")

    def set(self, key: str, value) -> None:  # noqa: ANN001
        self._q.setValue(key, value)

    def sync(self) -> None:
        self._q.sync()

    # ------------------------------------------------------------ typed
    @property
    def start_view(self) -> int:
        v = self._get_int("view/start")
        return v if 0 <= v < len(VIEW_KEYS) else 3

    @property
    def sort_column(self) -> int:
        v = self._get_int("view/sort_column")
        return v if 0 <= v <= 5 else 0

    @property
    def sort_descending(self) -> bool:
        return self._get_bool("view/sort_descending")

    @property
    def slideshow_seconds(self) -> int:
        return max(1, min(60, self._get_int("slideshow/seconds")))

    @property
    def compact_density(self) -> bool:
        return self._get_bool("view/compact_density")

    @property
    def preview_fill(self) -> str:
        v = str(self._get("preview/fill"))
        return v if v in ("contain", "cover") else "contain"

    @property
    def remember_window(self) -> bool:
        return self._get_bool("window/remember")

    @property
    def window_geometry(self) -> QByteArray | None:
        v = self._q.value("window/geometry")
        return v if isinstance(v, QByteArray) and not v.isEmpty() else None

    @window_geometry.setter
    def window_geometry(self, value: QByteArray) -> None:
        self._q.setValue("window/geometry", value)

    @property
    def temp_mode(self) -> str:
        v = str(self._get("temp/mode"))
        if v == "temp":  # stored by older versions
            return "overflow"
        return v if v in ("memory", "overflow", "disk") else "memory"

    @property
    def temp_location(self) -> str:
        v = str(self._get("temp/location"))
        return v if v in ("system", "custom") else "system"

    @property
    def temp_dir(self) -> str:
        return str(self._get("temp/dir") or "")

    @property
    def prune_on_exit(self) -> bool:
        return self._get_bool("temp/prune_on_exit")

    # ------------------------------------------------------------ resources
    def res_int(self, key: str) -> int:
        """An advanced resource value (0 = Automatic), never negative."""
        return max(0, self._get_int(key))

    @property
    def cap_mode(self) -> str:
        v = str(self._get("res/cap_mode"))
        return v if v in ("percent", "fixed") else "percent"

    def limits(self):  # noqa: ANN201 - archy.core.memory.Limits
        """The advanced resource settings as the memory policy's Limits."""
        from archy.core.memory import Limits

        mb = 1024 * 1024
        gb = 1024 * mb

        def opt(key: str, unit: int) -> int | None:
            value = self.res_int(key)
            return value * unit if value > 0 else None

        fixed_cap = max(1, self.res_int("res/cap_gb")) * gb
        return Limits(
            cap_percent=min(95, max(10, self.res_int("res/cap_percent") or 75)),
            cap_bytes=fixed_cap if self.cap_mode == "fixed" else None,
            reserve_bytes=opt("res/reserve_gb", gb),
            folder_buffer_bytes=opt("res/folder_mb", mb),
            thumb_cache_bytes=opt("res/thumb_mb", mb),
            read_cache_bytes=opt("res/read_mb", mb),
            video_memory_bytes=opt("res/video_mb", mb),
            threads=self.res_int("res/threads") or None,
        )

    @property
    def accent(self) -> str:
        return _norm_hex(self._get("theme/accent"), DEFAULT_ACCENT)

    @property
    def accent2(self) -> str:
        return _norm_hex(self._get("theme/accent2"), DEFAULT_ACCENT2)
