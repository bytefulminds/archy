from __future__ import annotations

import pytest

from archy.services.text_decode import decode_text, looks_binary

CASES = [
    # Short UTF-8 with emoji used to be detected as cp1125 ("Caf├й тШХ").
    ("Café ☕".encode(), "utf-8", "Café ☕"),
    ("Hallo 😀 wereld 🎉👍".encode(), "utf-8", "Hallo 😀 wereld 🎉👍"),
    ("Hij zei: “mooi” – en ‘oké’… €5 •".encode(), "utf-8", "Hij zei: “mooi” – en ‘oké’… €5 •"),
    (b"plain ascii\n", "utf-8", "plain ascii\n"),
    ("﻿met BOM 😀".encode(), "utf-8-sig", "met BOM 😀"),
    ("UTF-16 😀 “x”".encode("utf-16"), "utf-16", "UTF-16 😀 “x”"),
    ("UTF-32 😀".encode("utf-32"), "utf-32", "UTF-32 😀"),
    ("Kladblok zonder BOM “x”".encode("utf-16-le"), "utf-16-le", "Kladblok zonder BOM “x”"),
    # Old Windows text: was read as cp437 ("CafΘ ômooiö").
    ("Café “mooi” – €5".encode("cp1252"), "cp1252", "Café “mooi” – €5"),
    ("Café àéè".encode("latin-1"), "cp1252", "Café àéè"),
]


@pytest.mark.parametrize(("data", "encoding", "text"), CASES)
def test_decode_text(data: bytes, encoding: str, text: str) -> None:
    assert not looks_binary(data)
    decoded = decode_text(data)
    assert decoded.encoding == encoding
    assert decoded.text == text
    assert decoded.text.encode(decoded.encoding) == data  # edits save back unchanged


def test_cyrillic_legacy_text_is_still_detected() -> None:
    data = ("Привет, как дела? Это проверка текста на русском языке. " * 3).encode("cp1251")
    assert decode_text(data).text.startswith("Привет")


def test_preview_cut_inside_an_emoji_stays_utf8() -> None:
    decoded = decode_text(("😀" * 10).encode(), limit=10)
    assert decoded.encoding == "utf-8" and decoded.truncated
    assert decoded.text.startswith("😀😀")


def test_binary_is_still_binary() -> None:
    assert looks_binary(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")


@pytest.mark.parametrize("guess", ["utf_16_be", "utf_32", "cp437", "mac_roman", "cp1006"])
def test_unlikely_detector_guesses_fall_back_to_cp1252(guess: str, monkeypatch) -> None:
    """The detector's guess differs per charset-normalizer version (CI had a
    newer one); implausible guesses must never win over Windows-1252."""
    import charset_normalizer

    class _Match:
        encoding = guess

    class _Matches:
        def best(self) -> _Match:
            return _Match()

    monkeypatch.setattr(charset_normalizer, "from_bytes", lambda _data: _Matches())
    data = "Café “mooi” – €5".encode("cp1252")
    decoded = decode_text(data)
    assert (decoded.encoding, decoded.text) == ("cp1252", "Café “mooi” – €5")
