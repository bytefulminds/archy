from __future__ import annotations

import io
import zipfile
from pathlib import Path

from archy.core import detect_format, is_probably_archive


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_detects_zip_by_magic(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "hi")
    p = _write(tmp_path / "sample.zip", buf.getvalue())
    assert detect_format(p) == "zip"
    assert is_probably_archive(p)


def test_detects_7z_by_magic(tmp_path: Path) -> None:
    p = _write(tmp_path / "sample.7z", b"7z\xbc\xaf\x27\x1c" + b"\x00" * 32)
    assert detect_format(p) == "7z"


def test_detects_rar5_by_magic(tmp_path: Path) -> None:
    p = _write(tmp_path / "sample.rar", b"Rar!\x1a\x07\x01\x00" + b"\x00" * 16)
    assert detect_format(p) == "rar"


def test_detects_tar_by_ustar_offset(tmp_path: Path) -> None:
    header = bytearray(512)
    header[257:262] = b"ustar"
    p = _write(tmp_path / "sample.tar", bytes(header))
    assert detect_format(p) == "tar"


def test_extension_fallback_for_double_extension(tmp_path: Path) -> None:
    # Bytes that match no magic signature; must fall back to extension.
    p = _write(tmp_path / "sample.tar.gz", b"not-a-real-gzip-header-here")
    # gzip magic would win if present; here we only check extension logic.
    p2 = _write(tmp_path / "sample.unknownext", b"random")
    assert detect_format(p2) is None
    assert detect_format(p) in {"gzip", "tar.gz"}


def test_unknown_is_not_archive(tmp_path: Path) -> None:
    p = _write(tmp_path / "notes.txt", b"just some text")
    assert detect_format(p) is None
    assert not is_probably_archive(p)
