from __future__ import annotations

import ast
import json
import re
import string
from pathlib import Path

import pytest

from archy import i18n
from archy.i18n import plural_category, tr

SRC = Path(__file__).resolve().parents[1] / "src" / "archy"
BUNDLED = SRC / "resources" / "i18n"


def _catalog(code: str) -> dict:
    return json.loads((BUNDLED / f"{code}.json").read_text(encoding="utf-8"))


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def _forms(value: object) -> list[str]:
    return list(value.values()) if isinstance(value, dict) else [str(value)]


def _keys_used_in_code() -> set[str]:
    """Every literal key passed to tr() anywhere in the package."""
    used: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                used.add(node.args[0].value)
    return used


def test_every_key_used_in_the_code_exists_in_english() -> None:
    english = set(_catalog("en")["strings"])
    missing = sorted(_keys_used_in_code() - english - {"some.key"})
    assert not missing, missing


@pytest.mark.parametrize("code", ["nl"])
def test_bundled_translation_is_complete_and_consistent(code: str) -> None:
    english = _catalog("en")["strings"]
    other = _catalog(code)["strings"]
    assert set(other) == set(english)
    for key, value in other.items():
        # Same placeholders as English, in every plural form.
        wanted = set().union(*(_fields(form) for form in _forms(english[key])))
        got = set().union(*(_fields(form) for form in _forms(value)))
        assert got == wanted, key
        assert isinstance(value, dict) == isinstance(english[key], dict), key


def test_no_en_or_em_dashes_in_texts() -> None:
    for code in ("en", "nl"):
        for key, value in _catalog(code)["strings"].items():
            for form in _forms(value):
                assert not re.search("[–—]", form), (code, key)


def test_plural_rules() -> None:
    assert [plural_category("en", n) for n in (0, 1, 2)] == ["other", "one", "other"]
    assert [plural_category("nl", n) for n in (1, 5)] == ["one", "other"]
    assert [plural_category("pl", n) for n in (1, 2, 5, 12, 22, 25)] == [
        "one", "few", "many", "many", "few", "many",
    ]  # fmt: skip
    assert [plural_category("ru", n) for n in (1, 3, 5, 11, 21, 101)] == [
        "one", "few", "many", "many", "one", "one",
    ]  # fmt: skip


def test_translation_with_plural_and_number_grouping() -> None:
    i18n.set_language("nl")
    assert tr("count.items", count=1) == "1 item"
    assert tr("count.items", count=101648) == "101.648 items"
    assert tr("duration.minutes", m=2, s=5) == "2 min 05 s"
    i18n.set_language("en")
    assert tr("count.items", count=101648) == "101,648 items"


def test_sizes_use_the_language_decimal_separator() -> None:
    from archy.services.formatting import format_size

    assert format_size(1536) == "1.5 KB"
    i18n.set_language("nl")
    assert format_size(1536) == "1,5 KB"


def test_resolve_picks_the_system_language_or_english() -> None:
    assert i18n.resolve("auto", "nl_NL") == "nl"
    assert i18n.resolve("auto", "de_DE") == "en"  # no German catalog (yet)
    assert i18n.resolve("nl", "en_US") == "nl"
    assert i18n.resolve("xx", "en_US") == "en"


def test_resolve_follows_the_preferred_language_list() -> None:
    # A Dutch Mac: the preferred languages say Dutch, while the process locale
    # of a plain Python app says "en_NL" (it only has an English localization).
    assert i18n.resolve("auto", ["nl-NL", "en-NL", "en_NL"]) == "nl"
    assert i18n.resolve("auto", ["nl-Latn-NL", "en"]) == "nl"
    assert i18n.resolve("auto", ["de-DE", "nl-NL", "en-US"]) == "nl"  # first one Archy has
    assert i18n.resolve("auto", ["fr-FR"]) == "en"
    assert i18n.resolve("auto", []) == "en"


def test_a_user_language_file_adds_a_language(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "languages"
    folder.mkdir()
    (folder / "de.json").write_text(
        json.dumps(
            {
                "_meta": {"code": "de", "name": "Deutsch", "decimal": ",", "group": "."},
                "strings": {
                    "action.open": "Öffnen...",
                    "count.items": {"one": "{count} Element", "other": "{count} Elemente"},
                },
            }
        ),
        encoding="utf-8",
    )
    (folder / "broken.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(i18n, "user_language_dir", lambda: folder)
    i18n.catalogs(reload=True)

    assert ("de", "Deutsch") in i18n.available_languages()
    assert i18n.resolve("auto", "de_AT") == "de"
    i18n.set_language("de")
    assert tr("action.open") == "Öffnen..."
    assert tr("count.items", count=2500) == "2.500 Elemente"
    assert tr("action.save") == "Save"  # not translated yet: English, never a crash
    assert tr("no.such.key") == "no.such.key"


def test_a_broken_translation_never_crashes(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "languages"
    folder.mkdir()
    (folder / "nl.json").write_text(
        json.dumps({"strings": {"status.selected_one": "{naam} geselecteerd"}}), encoding="utf-8"
    )
    monkeypatch.setattr(i18n, "user_language_dir", lambda: folder)
    i18n.catalogs(reload=True)
    i18n.set_language("nl")
    assert tr("status.selected_one", name="a.jpg") == "{naam} geselecteerd"
    assert tr("action.open") == "Openen..."  # the rest of the bundled Dutch stays


def test_a_partial_user_file_keeps_the_bundled_language_name(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "languages"
    folder.mkdir()
    (folder / "nl.json").write_text(json.dumps({"strings": {"action.open": "Open..."}}))
    monkeypatch.setattr(i18n, "user_language_dir", lambda: folder)
    i18n.catalogs(reload=True)
    assert ("nl", "Nederlands") in i18n.available_languages()
