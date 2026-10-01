from __future__ import annotations

from pathlib import Path

import pytest

from archy.core import backend_for
from archy.core.backends.sevenzip_backend import SevenZipBackend

py7zr = pytest.importorskip("py7zr")


def _make_7z(path: Path) -> Path:
    # py7zr writestr order is (data, arcname) - the opposite of zipfile.
    with py7zr.SevenZipFile(path, "w") as z:
        z.writestr("original a", "keep/a.txt")
        z.writestr("delete me", "del/x.txt")
        z.writestr("orig", "ren/o.txt")
    return path


def _read_all(backend: SevenZipBackend) -> dict[str, bytes]:
    fresh = backend_for(backend.path)
    return {e.path: fresh.read_bytes(e) for e in fresh.list_entries() if not e.is_dir}


def test_sevenzip_add_remove_rename_save(tmp_path: Path) -> None:
    p = _make_7z(tmp_path / "a.7z")
    src = tmp_path / "extra.txt"
    src.write_bytes(b"seven added")
    backend = backend_for(p)
    assert isinstance(backend, SevenZipBackend)
    assert backend.has_unsaved_changes is False

    backend.add(str(src), "new/extra.txt")
    backend.remove("del/x.txt")
    backend.rename("ren/o.txt", "ren/new.txt")
    assert backend.has_unsaved_changes is True
    backend.save()
    assert backend.has_unsaved_changes is False

    contents = _read_all(backend)
    assert contents["new/extra.txt"] == b"seven added"
    assert "del/x.txt" not in contents
    assert contents["ren/new.txt"] == b"orig" and "ren/o.txt" not in contents
    assert contents["keep/a.txt"] == b"original a"
    assert not list(tmp_path.glob("*.zxtmp"))


def test_sevenzip_add_dir(tmp_path: Path) -> None:
    p = _make_7z(tmp_path / "a.7z")
    backend = backend_for(p)
    backend.add_dir("new/empty")
    assert backend.has_unsaved_changes is True
    backend.save()
    with py7zr.SevenZipFile(p, "r") as z:
        dirs = {fi.filename.rstrip("/") for fi in z.list() if getattr(fi, "is_directory", False)}
    assert "new/empty" in dirs


def test_sevenzip_effective_entries_and_discard(tmp_path: Path) -> None:
    p = _make_7z(tmp_path / "a.7z")
    before = p.read_bytes()
    backend = backend_for(p)

    backend.write_bytes("new/x.txt", b"hi")
    backend.remove("keep/a.txt")
    paths = {e.path for e in backend.list_effective_entries()}
    assert "new/x.txt" in paths and "keep/a.txt" not in paths

    backend.discard_changes()
    assert backend.has_unsaved_changes is False
    backend.save()
    assert p.read_bytes() == before
