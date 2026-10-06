"""Archives inside an archive (core/backends/nested_backend.py): opened from
memory, read-only."""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from archy.core.backends.nested_backend import NestedBackend, looks_nested, nested_format
from archy.core.errors import EncryptedArchiveError


def _zip() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("docs/readme.txt", b"hello from inside")
        z.writestr("photo.jpg", b"\xff\xd8" + b"x" * 100)
    return out.getvalue()


def _tar_gz() -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        data = b"inside a tarball"
        info = tarfile.TarInfo("src/main.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def _entry(backend: NestedBackend, path: str):  # noqa: ANN202
    return next(e for e in backend.list_entries() if e.path == path)


def test_which_files_open_as_archives() -> None:
    assert looks_nested("backup/old.ZIP") and looks_nested("x.tar.gz") and looks_nested("y.7z")
    assert not looks_nested("photo.jpg")
    assert nested_format(_zip(), "a.bin") == "zip"
    assert nested_format(_tar_gz(), "a.bin") == "tar"


def test_zip_inside_reads_from_memory_and_extracts(tmp_path) -> None:  # noqa: ANN001
    backend = NestedBackend(_zip(), "inner.zip")
    paths = {e.path for e in backend.list_entries()}
    assert {"docs/readme.txt", "photo.jpg"} <= paths
    entry = _entry(backend, "docs/readme.txt")
    assert backend.read_bytes(entry) == b"hello from inside"
    assert not backend.writable
    result = backend.extract([entry], tmp_path)
    assert result.extracted == 1
    assert (tmp_path / "docs" / "readme.txt").read_bytes() == b"hello from inside"


def test_tar_gz_inside() -> None:
    backend = NestedBackend(_tar_gz(), "src.tar.gz")
    assert backend.read_bytes(_entry(backend, "src/main.py")) == b"inside a tarball"


def test_encrypted_7z_inside_asks_for_its_password() -> None:
    py7zr = pytest.importorskip("py7zr")
    out = io.BytesIO()
    with py7zr.SevenZipFile(out, "w", password="geheim", header_encryption=True) as z:
        z.writestr(b"secret notes", "notes.txt")
    backend = NestedBackend(out.getvalue(), "vault.7z")
    with pytest.raises(EncryptedArchiveError):
        list(backend.list_entries())
    backend.set_password("geheim")
    assert backend.read_bytes(_entry(backend, "notes.txt")) == b"secret notes"
