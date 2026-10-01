from __future__ import annotations

import io
import os
import tarfile
import zipfile

import pytest

from archy.core import backend_for, memory
from archy.core.secure_temp import EncryptedTempStore

pytest.importorskip("Cryptodome")


def _make(kind: str, path, files: dict[str, bytes]):
    if kind == "zip":
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in files.items():
                zf.writestr(name, data)
    elif kind == "7z":
        py7zr = pytest.importorskip("py7zr")
        with py7zr.SevenZipFile(str(path), "w") as z:
            for name, data in files.items():
                z.writef(io.BytesIO(data), name)
    else:
        with tarfile.open(path, "w:gz") as tar:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return backend_for(path)


FILES = {f"shoot/img{i}.jpg": os.urandom(40_000 + i) for i in range(4)}


@pytest.mark.parametrize("kind", ["zip", "7z", "tar.gz"])
def test_stream_entry_matches_read_bytes(tmp_path, kind):
    backend = _make(kind, tmp_path / f"a.{kind}", FILES)
    for entry in (e for e in backend.list_entries() if not e.is_dir):
        chunks: list[bytes] = []
        assert backend.stream_entry(entry, chunks.append) is True
        assert b"".join(chunks) == FILES[entry.path]
    backend.close()


def test_stream_entry_can_be_cancelled(tmp_path):
    big = {"shoot/video.mp4": os.urandom(5 * 1024 * 1024)}
    backend = _make("7z", tmp_path / "v.7z", big)
    entry = next(e for e in backend.list_entries() if not e.is_dir)
    seen = []
    ok = backend.stream_entry(entry, seen.append, is_cancelled=lambda: len(seen) >= 1)
    assert ok is False
    backend.close()


@pytest.mark.parametrize("kind", ["7z", "tar.gz"])
def test_disk_mode_keeps_the_folder_encrypted_on_disk(tmp_path, kind):
    store = EncryptedTempStore(tmp_path / "tmp")
    (tmp_path / "tmp").mkdir()
    backend = _make(kind, tmp_path / f"a.{kind}", FILES)
    backend.configure_spill(store, "disk")
    entries = [e for e in backend.list_entries() if not e.is_dir]
    gen = backend.begin_folder()
    assert backend.read_group(entries, gen) == len(FILES)
    assert backend._folder_buffer == {}  # nothing in RAM
    for entry in entries:
        assert backend.has_buffered(entry.path)
        assert backend.read_bytes(entry, cache=False) == FILES[entry.path]
        raw = backend.spilled_blob(entry.path).path.read_bytes()
        assert FILES[entry.path][:64] not in raw  # encrypted on disk
    blobs = [backend.spilled_blob(e.path).path for e in entries]
    backend.begin_folder()  # next folder: the previous one's temp files go
    assert not any(p.exists() for p in blobs)
    backend.close()
    store.clear()


def test_overflow_mode_spills_only_what_does_not_fit(tmp_path, monkeypatch):
    import archy.core.backend as backend_module

    store = EncryptedTempStore(tmp_path / "tmp")
    (tmp_path / "tmp").mkdir()
    backend = _make("7z", tmp_path / "a.7z", FILES)
    backend.configure_spill(store, "overflow")
    monkeypatch.setattr(backend_module, "_prefetch_budget", lambda: 90_000)  # two fit
    entries = [e for e in backend.list_entries() if not e.is_dir]
    gen = backend.begin_folder()
    assert backend.read_group(entries, gen) == 4  # all kept...
    assert len(backend._folder_buffer) == 2  # ...two in RAM
    assert len(backend._folder_spilled) == 2  # ...two encrypted on disk
    for entry in entries:
        assert backend.read_bytes(entry, cache=False) == FILES[entry.path]
    backend.close()
    store.clear()


def test_memory_mode_with_tight_memory_keeps_nothing(tmp_path, monkeypatch):
    backend = _make("7z", tmp_path / "a.7z", FILES)
    monkeypatch.setattr(memory, "available", lambda: 1024)  # below the reserve
    entries = [e for e in backend.list_entries() if not e.is_dir]
    gen = backend.begin_folder()
    assert backend.read_group(entries, gen) == 0
    assert backend._folder_buffer == {}
    backend.close()
