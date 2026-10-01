from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from archy.core import ArchiveError, backend_for
from archy.core.backends.sevenzip_backend import SevenZipBackend
from archy.core.backends.sevenzip_writer import SaveCancelled, _Writer

py7zr = pytest.importorskip("py7zr")

PW = "geheim"
FILES = {
    "fotos/a.jpg": b"A" * 200_000,
    "fotos/b.jpg": b"B" * 150_000,
    "fotos/sub/c.raw": b"C" * 300_000,
    "notes.txt": b"hallo",
    "leeg.txt": b"",
}


def _make_backup(path: Path, *, password: str | None = PW, store: bool = True) -> Path:
    """A 7z like a 7-Zip backup made with Store, non-solid, encrypted names."""
    writer = _Writer(path, password=password, header_encrypted=True, store=store, solid=False)
    writer.add_dir("fotos")
    writer.add_dir("fotos/sub")
    for name, data in FILES.items():
        writer.add_file(name, len(data), io.BytesIO(data))
    writer.close()
    return path


def _open(path: Path, password: str | None = PW) -> SevenZipBackend:
    backend = backend_for(path)
    assert isinstance(backend, SevenZipBackend)
    backend.set_password(password)
    return backend


def _contents(path: Path, password: str | None = PW) -> dict[str, bytes]:
    backend = _open(path, password)
    return {e.path: backend.read_bytes(e) for e in backend.list_entries() if not e.is_dir}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_edit_profile(tmp_path: Path) -> None:
    store = _open(_make_backup(tmp_path / "s.7z"))
    profile = store.edit_profile()
    assert profile.store_only and profile.encrypted and profile.header_encrypted
    assert not profile.solid
    packed = _open(_make_backup(tmp_path / "c.7z", store=False))
    assert not packed.edit_profile().store_only


def test_save_keeps_password_names_store_and_blocks(tmp_path: Path) -> None:
    path = _make_backup(tmp_path / "backup.7z")
    before = {i.filename: i.creationtime for i in py7zr.SevenZipFile(path, password=PW).list()}
    backend = _open(path)
    backend.rename("notes.txt", "docs/notes.txt")
    backend.remove("fotos/b.jpg")
    backend.write_bytes("nieuw/d.txt", b"nieuw")
    backend.add_dir("lege map")
    seen: list[tuple[int, int]] = []
    backend.save(on_progress=lambda done, total, name, files: seen.append((done, total)))

    assert not backend.has_unsaved_changes
    expected = dict(FILES)
    expected["docs/notes.txt"] = expected.pop("notes.txt")
    del expected["fotos/b.jpg"]
    expected["nieuw/d.txt"] = b"nieuw"
    assert _contents(path) == expected
    assert seen and seen[-1][0] == seen[-1][1] == sum(len(v) for v in expected.values())

    with py7zr.SevenZipFile(path, password=PW) as z:
        info = z.archiveinfo()
        names = {i.filename for i in z.list()}
        times = {i.filename: i.creationtime for i in z.list()}
    assert {m.upper() for m in info.method_names} == {"COPY", "7ZAES"}
    assert info.blocks >= 4  # one block per file with data: non-solid stays non-solid
    assert "lege map" in names and "fotos/sub" in names
    assert times["fotos/a.jpg"] == before["fotos/a.jpg"]  # file times are kept
    with pytest.raises(Exception):  # noqa: B017 - names stay encrypted
        py7zr.SevenZipFile(path).list()
    assert not list(tmp_path.glob("*.archy-save"))


def test_folder_rename_reads_nothing(tmp_path: Path, monkeypatch) -> None:
    backend = _open(_make_backup(tmp_path / "b.7z"))

    def no_reads(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("rename must not read file data")

    monkeypatch.setattr(backend, "_read_uncached", no_reads)
    for entry in list(backend.list_effective_entries()):
        if entry.path == "fotos" or entry.path.startswith("fotos/"):
            backend.rename(entry.path, "foto's" + entry.path[len("fotos") :])
    monkeypatch.undo()
    shown = {e.path: e for e in backend.list_effective_entries()}
    assert "foto's/sub/c.raw" in shown and "fotos/a.jpg" not in shown
    assert backend.read_bytes(shown["foto's/sub/c.raw"]) == FILES["fotos/sub/c.raw"]
    backend.save()
    assert _contents(tmp_path / "b.7z")["foto's/a.jpg"] == FILES["fotos/a.jpg"]


def test_cancelled_save_leaves_the_original(tmp_path: Path) -> None:
    path = _make_backup(tmp_path / "c.7z")
    before = _sha(path)
    backend = _open(path)
    backend.write_bytes("x.txt", b"x")
    calls = {"n": 0}

    def cancel_after_a_while() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    with pytest.raises(SaveCancelled):
        backend.save(is_cancelled=cancel_after_a_while)
    assert _sha(path) == before
    assert backend.has_unsaved_changes  # nothing lost: the edit is still staged
    assert not list(tmp_path.glob("*.archy-save"))


def test_save_needs_free_space(tmp_path: Path, monkeypatch) -> None:
    import shutil
    from collections import namedtuple

    path = _make_backup(tmp_path / "d.7z")
    before = _sha(path)
    backend = _open(path)
    backend.remove("notes.txt")
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: usage(1, 1, 10))
    with pytest.raises(ArchiveError, match="free disk space"):
        backend.save()
    assert _sha(path) == before


def test_plain_7z_save_still_works_and_stays_unencrypted(tmp_path: Path) -> None:
    path = _make_backup(tmp_path / "plain.7z", password=None, store=False)
    backend = _open(path, None)
    backend.rename("notes.txt", "n.txt")
    backend.save()
    with py7zr.SevenZipFile(path) as z:
        assert not z.needs_password()
    assert _contents(path, None)["n.txt"] == b"hallo"
