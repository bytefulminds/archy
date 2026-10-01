from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from archy.core import backend_for
from archy.core.backends import zip_backend
from archy.core.backends.zip_backend import ZipBackend


def _make_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("keep/a.txt", "original a")
        zf.writestr("edit/note.txt", "before")
        zf.writestr("keep/b.bin", bytes(range(64)))
    return path


def _read_all(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i.filename) for i in zf.infolist()}


def test_write_bytes_and_save_in_memory(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    backend = backend_for(p)
    assert isinstance(backend, ZipBackend)
    assert backend.has_unsaved_changes is False

    backend.write_bytes("edit/note.txt", b"after edit")
    assert backend.has_unsaved_changes is True
    backend.save()
    assert backend.has_unsaved_changes is False

    contents = _read_all(p)
    assert contents["edit/note.txt"] == b"after edit"
    assert contents["keep/a.txt"] == b"original a"  # untouched entry preserved
    assert contents["keep/b.bin"] == bytes(range(64))
    assert not list(tmp_path.glob("*.zxtmp"))  # no temp file left behind


def test_save_via_tempfile_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the large-archive branch regardless of real size.
    monkeypatch.setattr(zip_backend, "_IN_MEMORY_SAVE_LIMIT", 0)
    p = _make_zip(tmp_path / "a.zip")
    backend = backend_for(p)
    backend.write_bytes("edit/note.txt", b"streamed")
    backend.save()

    contents = _read_all(p)
    assert contents["edit/note.txt"] == b"streamed"
    assert contents["keep/a.txt"] == b"original a"
    assert not list(tmp_path.glob("*.zxtmp"))


def test_add_and_remove_entries(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    backend = backend_for(p)
    backend.write_bytes("new/added.txt", b"hello new")
    backend.remove("keep/b.bin")
    backend.save()

    contents = _read_all(p)
    assert contents["new/added.txt"] == b"hello new"
    assert "keep/b.bin" not in contents
    assert contents["keep/a.txt"] == b"original a"


def test_save_no_pending_is_noop(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    before = p.read_bytes()
    backend = backend_for(p)
    backend.save()
    assert p.read_bytes() == before


def test_add_from_disk_and_rename(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    src = tmp_path / "from_disk.txt"
    src.write_bytes(b"disk bytes")
    backend = backend_for(p)

    backend.add(str(src), "added/from_disk.txt")
    backend.rename("edit/note.txt", "edit/renamed.txt")
    assert backend.has_unsaved_changes is True
    backend.save()

    contents = _read_all(p)
    assert contents["added/from_disk.txt"] == b"disk bytes"
    assert contents["edit/renamed.txt"] == b"before"  # content preserved
    assert "edit/note.txt" not in contents
    assert contents["keep/a.txt"] == b"original a"
    assert not list(tmp_path.glob("*.zxtmp"))


def test_effective_entries_reflect_pending(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    backend = backend_for(p)
    backend.write_bytes("new/x.txt", b"hi")
    backend.remove("keep/b.bin")
    backend.rename("keep/a.txt", "keep/a2.txt")

    paths = {e.path for e in backend.list_effective_entries()}
    assert "new/x.txt" in paths  # staged add is visible
    assert "keep/b.bin" not in paths  # staged delete is hidden
    assert "keep/a2.txt" in paths and "keep/a.txt" not in paths  # staged rename

    # a staged edit is served by read_bytes before save
    entry = next(e for e in backend.list_effective_entries() if e.path == "new/x.txt")
    assert backend.read_bytes(entry) == b"hi"


def test_add_dir(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    backend = backend_for(p)
    backend.add_dir("new/empty")
    assert backend.has_unsaved_changes is True
    # the staged empty folder shows up as a directory entry
    dirs = {e.path for e in backend.list_effective_entries() if e.is_dir}
    assert "new/empty/" in dirs
    backend.save()
    with zipfile.ZipFile(p) as zf:
        assert any(i.is_dir() and i.filename.rstrip("/") == "new/empty" for i in zf.infolist())


def test_discard_changes(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    before = p.read_bytes()
    backend = backend_for(p)
    backend.write_bytes("new/x.txt", b"hi")
    backend.remove("keep/a.txt")
    assert backend.has_unsaved_changes is True
    backend.discard_changes()
    assert backend.has_unsaved_changes is False
    backend.save()  # nothing pending -> no-op
    assert p.read_bytes() == before


def test_pooled_readers_see_saved_changes(tmp_path):
    # Readers are pooled for speed; after save() the archive must be reopened
    # (and on Windows the pool must not keep the file open during the save).
    import zipfile

    from archy.core import backend_for

    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("x.txt", b"old")
    backend = backend_for(archive)
    entry = next(e for e in backend.list_entries() if e.path == "x.txt")
    assert backend.read_bytes(entry, cache=False) == b"old"
    backend.write_bytes("x.txt", b"new content")
    backend.save()
    entry = next(e for e in backend.list_entries() if e.path == "x.txt")
    assert backend.read_bytes(entry, cache=False) == b"new content"
    backend.close()
    assert backend._idle_readers == []
