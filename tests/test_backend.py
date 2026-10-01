from __future__ import annotations

from collections.abc import Iterator

import pytest

from archy.core import (
    ArchiveBackend,
    ArchiveEntry,
    ArchiveInfo,
    EntryType,
    ReadOnlyArchiveError,
)


def test_archive_backend_is_abstract() -> None:
    with pytest.raises(TypeError):
        ArchiveBackend("nope.zip")  # type: ignore[abstract]


class _DummyReadOnly(ArchiveBackend):
    def info(self) -> ArchiveInfo:
        return ArchiveInfo(path=self.path, format="dummy", writable=False)

    def list_entries(self) -> Iterator[ArchiveEntry]:
        yield ArchiveEntry(path="a/b.txt", type=EntryType.FILE, size=3)

    def _read_uncached(
        self, entry: ArchiveEntry, *, password: str | None = None, max_bytes: int | None = None
    ) -> bytes:
        return b"abc"


def test_read_only_backend_defaults() -> None:
    b = _DummyReadOnly("x.dummy")
    assert b.writable is False
    entries = list(b.list_entries())
    assert entries[0].name == "b.txt"
    assert entries[0].parent == "a"
    assert b.read_bytes(entries[0]) == b"abc"


def test_write_methods_raise_on_read_only_backend() -> None:
    b = _DummyReadOnly("x.dummy")
    with pytest.raises(ReadOnlyArchiveError):
        b.add("some/file", "file")
    with pytest.raises(ReadOnlyArchiveError):
        b.remove("file")
    with pytest.raises(ReadOnlyArchiveError):
        b.save()


def test_entry_helpers() -> None:
    d = ArchiveEntry(path="folder/", type=EntryType.DIRECTORY)
    assert d.is_dir
    assert d.name == "folder"
    assert d.parent == ""
