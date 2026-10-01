from __future__ import annotations

from archy.services.preview import PreviewKind, kind_for_name
from archy.services.text_decode import (
    MAX_PREVIEW_BYTES,
    decode_text,
    looks_binary,
)


def test_kind_for_name() -> None:
    assert kind_for_name("a.txt") is PreviewKind.TEXT
    assert kind_for_name("a.PY") is PreviewKind.TEXT
    assert kind_for_name("photo.JPG") is PreviewKind.IMAGE
    assert kind_for_name("doc.pdf") is PreviewKind.PDF
    assert kind_for_name("clip.mp4") is PreviewKind.VIDEO
    assert kind_for_name("archive.bin") is PreviewKind.UNSUPPORTED
    assert kind_for_name("noext") is PreviewKind.UNSUPPORTED


def test_decode_utf8() -> None:
    d = decode_text("hallo wêreld\n".encode())
    assert "hallo wêreld" in d.text
    assert not d.truncated


def test_decode_latin1_fallback() -> None:
    # 0xE9 is é in latin-1; must decode without raising and stay readable.
    d = decode_text(b"caf\xe9 au lait")
    assert "caf" in d.text
    assert d.encoding


def test_decode_truncates_large_input() -> None:
    big = b"a" * (MAX_PREVIEW_BYTES + 500)
    d = decode_text(big)
    assert d.truncated
    assert "cut off" in d.text


def test_looks_binary() -> None:
    assert looks_binary(b"\x00\x01\x02text")
    assert not looks_binary(b"plain text only")
