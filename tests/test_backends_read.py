from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from archy.core import ArchiveError, EntryType, backend_for
from archy.core.backends.sevenzip_backend import SevenZipBackend
from archy.core.backends.tar_backend import TarBackend
from archy.core.backends.zip_backend import ZipBackend
from archy.core.errors import UnsupportedFormatError

# Small fixture content reused across formats.
_FILES = {
    "readme.txt": b"hello archy",
    "docs/guide.md": b"# Guide\n\nsome text\n",
    "data/bytes.bin": bytes(range(256)),
}


def _entries_by_path(backend) -> dict[str, object]:
    return {e.path.rstrip("/"): e for e in backend.list_entries()}


# --------------------------------------------------------------------- zip
def _make_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in _FILES.items():
            zf.writestr(name, data)
    return path


def test_zip_factory_picks_zip_backend(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    assert isinstance(backend_for(p), ZipBackend)


def test_zip_lists_and_reads(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    backend = backend_for(p)

    info = backend.info()
    assert info.format == "zip"
    assert info.writable is True

    entries = _entries_by_path(backend)
    for name, data in _FILES.items():
        assert name in entries
        assert entries[name].type is EntryType.FILE
        assert entries[name].size == len(data)
        assert backend.read_bytes(entries[name]) == data


def test_zip_read_missing_entry_raises(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    backend = backend_for(p)
    ghost = next(iter(backend.list_entries()))
    object.__setattr__(ghost, "path", "does/not/exist.txt")
    with pytest.raises(ArchiveError):
        backend.read_bytes(ghost)


# ---------------------------------------------------------------- 7z
def test_sevenzip_lists_and_reads(tmp_path: Path) -> None:
    py7zr = pytest.importorskip("py7zr")
    p = tmp_path / "a.7z"
    with py7zr.SevenZipFile(p, "w") as zf:
        for name, data in _FILES.items():
            zf.writestr(data, name)

    backend = backend_for(p)
    assert isinstance(backend, SevenZipBackend)
    assert backend.info().format == "7z"

    entries = _entries_by_path(backend)
    for name, data in _FILES.items():
        assert name in entries
        assert entries[name].size == len(data)
        assert backend.read_bytes(entries[name]) == data


# ------------------------------------------------- tar.* (stdlib TarBackend)
def _make_targz(path: Path) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in _FILES.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return path


def test_targz_uses_tar_backend_and_reads(tmp_path: Path) -> None:
    # tar/tar.gz/tar.bz2/tar.xz are read via the stdlib TarBackend - no native
    # libarchive needed - so this runs everywhere.
    p = _make_targz(tmp_path / "a.tar.gz")
    backend = backend_for(p)
    assert isinstance(backend, TarBackend)

    entries = _entries_by_path(backend)
    for name, data in _FILES.items():
        assert name in entries
        assert backend.read_bytes(entries[name]) == data


def test_plain_tar_reads(tmp_path: Path) -> None:
    p = tmp_path / "a.tar"
    with tarfile.open(p, "w") as tf:
        for name, data in _FILES.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    backend = backend_for(p)
    assert isinstance(backend, TarBackend)
    entries = _entries_by_path(backend)
    for name, data in _FILES.items():
        assert backend.read_bytes(entries[name]) == data


def test_unsupported_format_without_libarchive(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    # A rar (or any libarchive-only format) reports UnsupportedFormatError when
    # libarchive is not available, instead of failing obscurely later.
    p = tmp_path / "a.rar"
    p.write_bytes(b"Rar!\x1a\x07\x00fake")
    monkeypatch.setattr("archy.core.native.libarchive_available", lambda: False)
    with pytest.raises(UnsupportedFormatError):
        backend_for(p)


def test_backend_for_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ArchiveError):
        backend_for(tmp_path / "nope.zip")


def test_context_manager_closes(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    with backend_for(p) as backend:
        assert backend.info().entry_count == len(_FILES)


def test_zip_read_bytes_max_bytes(tmp_path: Path) -> None:
    p = tmp_path / "a.zip"
    payload = b"0123456789" * 1000
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.txt", payload)
    backend = backend_for(p)
    entry = next(e for e in backend.list_entries() if e.path == "big.txt")
    head = backend.read_bytes(entry, max_bytes=100)
    assert head == payload[:100]
    assert backend.read_bytes(entry) == payload


def test_targz_read_bytes_max_bytes(tmp_path: Path) -> None:
    payload = b"abcdefghij" * 1000
    p = tmp_path / "a.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        ti = tarfile.TarInfo("big.txt")
        ti.size = len(payload)
        tf.addfile(ti, io.BytesIO(payload))
    backend = backend_for(p)
    entry = next(e for e in backend.list_entries() if e.path == "big.txt")
    head = backend.read_bytes(entry, max_bytes=100)
    assert head == payload[:100]
    assert backend.read_bytes(entry) == payload


def test_read_cache_serves_repeat_reads(tmp_path: Path) -> None:
    p = tmp_path / "c.zip"
    payload = b"cache me" * 1000
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.bin", payload)
    backend = backend_for(p)
    entry = next(e for e in backend.list_entries() if e.path == "data.bin")

    calls: list[str] = []
    original = backend._read_uncached

    def counting(entry, *, password=None, max_bytes=None):  # noqa: ANN001, ANN202
        calls.append(entry.path)
        return original(entry, password=password, max_bytes=max_bytes)

    backend._read_uncached = counting  # type: ignore[method-assign]
    first = backend.read_bytes(entry)
    second = backend.read_bytes(entry)
    assert first == second == payload
    assert calls == ["data.bin"]  # decoded once; second read served from cache
