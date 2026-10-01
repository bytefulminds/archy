"""User interface translations.

Every text the user sees goes through ``tr("some.key", name=...)``. The texts
live in JSON catalogs, one file per language:

- bundled with Archy: ``archy/resources/i18n/<code>.json`` (``en`` and ``nl``)
- added by a user, no rebuild needed: ``<config dir>/languages/<code>.json``
  (see ``user_language_dir()``; on Windows ``%APPDATA%\\Archy\\languages``,
  on macOS ``~/Library/Application Support/Archy/languages``, on Linux
  ``~/.config/archy/languages``). A user file for an existing code overrides
  single texts of the bundled one.

A catalog looks like::

    {
      "_meta": {"code": "de", "name": "Deutsch", "english_name": "German",
                "decimal": ",", "group": "."},
      "strings": {
        "action.open": "Öffnen...",
        "status.files": {"one": "{count} Datei", "other": "{count} Dateien"}
      }
    }

Texts use ``{name}`` placeholders (Python ``str.format``). A text that depends
on a number is an object with plural forms; which forms a language needs
follows the CLDR rules in ``plural_category`` (English: one/other; Polish and
Russian: one/few/many). A missing text falls back to English, then to the
key itself, so a half-finished translation never breaks the app.

The language is chosen once at startup (Settings: Automatic = the system
language when Archy has it, else English); changing it applies after a
restart. This module has no Qt dependency so core code and tests can use it.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

FALLBACK = "en"
_BUNDLED_DIR = Path(__file__).resolve().parent / "resources" / "i18n"

_language = FALLBACK
_strings: dict[str, Any] = {}  # current language, merged over the fallback
_meta: dict[str, Any] = {}
_cache: dict[str, dict[str, Any]] | None = None  # code -> catalog


# --------------------------------------------------------------- catalogs
def user_language_dir() -> Path:
    """Where users drop extra language files (created on demand)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "Archy" / "languages"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Archy" / "languages"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "archy" / "languages"


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("strings"), dict):
        return None
    explicit = data.get("_meta") if isinstance(data.get("_meta"), dict) else {}
    meta = {"code": path.stem, "name": path.stem, **explicit}
    return {"_meta": meta, "_explicit": dict(explicit), "strings": data["strings"]}


def catalogs(*, reload: bool = False) -> dict[str, dict[str, Any]]:
    """All catalogs by language code: bundled ones, with user files on top."""
    global _cache
    if _cache is not None and not reload:
        return _cache
    found: dict[str, dict[str, Any]] = {}
    for folder in (_BUNDLED_DIR, user_language_dir()):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            catalog = _read(path)
            if catalog is None:
                continue
            code = str(catalog["_meta"]["code"]).lower()
            if code in found:  # a user file extends / overrides the bundled one
                found[code]["strings"].update(catalog["strings"])
                found[code]["_meta"].update(catalog["_explicit"])
            else:
                found[code] = catalog
    _cache = found
    return found


def available_languages() -> list[tuple[str, str]]:
    """(code, name in that language) for every catalog, sorted by name."""
    return sorted(
        ((code, str(c["_meta"].get("name", code))) for code, c in catalogs().items()),
        key=lambda item: item[1].casefold(),
    )


def resolve(preference: str, system_languages: str | Sequence[str]) -> str:
    """The language to use: the chosen one if it exists; for "auto" the first
    of the user's preferred languages (``nl-NL`` -> ``nl``) that Archy has;
    else English. ``system_languages`` is in order of preference."""
    known = catalogs()
    if preference and preference != "auto" and preference in known:
        return preference
    if isinstance(system_languages, str):
        system_languages = [system_languages]
    for language in system_languages:
        tag = (language or "").replace("-", "_").lower()
        for candidate in (tag, tag.split("_")[0]):
            if candidate in known:
                return candidate
    return FALLBACK


def set_language(code: str) -> None:
    global _language, _strings, _meta
    known = catalogs()
    if code not in known:
        code = FALLBACK
    merged: dict[str, Any] = dict(known.get(FALLBACK, {}).get("strings", {}))
    merged.update(known.get(code, {}).get("strings", {}))
    _language, _strings = code, merged
    _meta = dict(known.get(code, {}).get("_meta", {}))


def language() -> str:
    return _language


# ------------------------------------------------------------ translating
def plural_category(lang: str, count: int) -> str:
    """CLDR plural category of ``count`` in ``lang`` (integers only)."""
    n = abs(int(count))
    base = lang.split("_")[0]
    if base == "pl":
        if n == 1:
            return "one"
        if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
            return "few"
        return "many"
    if base in ("ru", "uk", "be"):
        if n % 10 == 1 and n % 100 != 11:
            return "one"
        if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
            return "few"
        return "many"
    if base in ("cs", "sk"):
        if n == 1:
            return "one"
        return "few" if 2 <= n <= 4 else "other"
    if base in ("fr", "pt"):
        return "one" if n in (0, 1) else "other"
    if base in ("ja", "zh", "ko", "vi", "th", "id"):
        return "other"
    return "one" if n == 1 else "other"


def tr(key: str, /, **values: Any) -> str:
    """The text for ``key`` in the current language, with ``values`` filled
    in. Pass ``count=`` for texts with plural forms; integers in ``values``
    are shown with the language's digit grouping (12.345 / 12,345)."""
    if not _strings and not _meta:
        set_language(_language)
    text = _strings.get(key, key)
    if isinstance(text, dict):
        count = values.get("count", 0)
        form = plural_category(_language, count if isinstance(count, int) else 0)
        text = text.get(form) or text.get("other") or next(iter(text.values()), key)
    text = str(text)
    if not values:
        return text
    shown = {
        k: _Int(v) if isinstance(v, int) and not isinstance(v, bool) else v
        for k, v in values.items()
    }
    try:
        return text.format(**shown)
    except (KeyError, IndexError, ValueError):
        return text  # a broken translation shows its raw text, never crashes


class _Int(int):
    """An integer that formats with digit grouping for a plain ``{count}``
    and like any int for ``{count:02d}``."""

    def __format__(self, spec: str) -> str:
        return number(int(self)) if not spec else int.__format__(int(self), spec)


def number(value: int | float, decimals: int = 0) -> str:
    """A number with the language's separators (``12.345,6`` in Dutch)."""
    group = str(_meta.get("group", ","))
    decimal = str(_meta.get("decimal", "."))
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "\0").replace(".", decimal).replace("\0", group)


__all__ = [
    "FALLBACK",
    "available_languages",
    "catalogs",
    "language",
    "number",
    "plural_category",
    "resolve",
    "set_language",
    "tr",
    "user_language_dir",
]
