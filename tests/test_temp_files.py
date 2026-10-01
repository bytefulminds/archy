from __future__ import annotations

import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from archy.core import backend_for
from archy.services.temp_files import TempManager


@dataclass
class _FakeSettings:
    temp_mode: str = "memory"
    temp_location: str = "system"
    temp_dir: str = ""
    prune_on_exit: bool = True


def _make_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("media/clip.mp4", b"\x00\x01\x02\x03" * 32)
    return path


def test_modes_and_migration() -> None:
    assert TempManager(_FakeSettings(temp_mode="memory")).enabled() is False
    assert TempManager(_FakeSettings(temp_mode="overflow")).enabled() is True
    assert TempManager(_FakeSettings(temp_mode="disk")).mode() == "disk"
    assert TempManager(_FakeSettings(temp_mode="bogus")).mode() == "memory"


def test_base_dir_defaults_to_system_temp() -> None:
    mgr = TempManager(_FakeSettings())
    assert mgr.base_dir() == Path(tempfile.gettempdir())


def test_base_dir_uses_custom_when_valid(tmp_path: Path) -> None:
    custom = tmp_path / "myscratch"
    custom.mkdir()
    mgr = TempManager(_FakeSettings(temp_location="custom", temp_dir=str(custom)))
    assert mgr.base_dir() == custom


def test_base_dir_falls_back_when_custom_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    mgr = TempManager(_FakeSettings(temp_location="custom", temp_dir=str(missing)))
    assert mgr.base_dir() == Path(tempfile.gettempdir())


def test_spill_entry_is_encrypted_and_reads_back(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path / "a.zip")
    base = tmp_path / "scratch"
    base.mkdir()
    backend = backend_for(archive)
    mgr = TempManager(_FakeSettings(temp_mode="disk", temp_location="custom", temp_dir=str(base)))

    entry = next(e for e in backend.list_effective_entries() if e.path == "media/clip.mp4")
    blob = mgr.spill_entry(backend, entry)

    assert blob is not None
    assert blob.path.exists()
    assert base in blob.path.parents  # under the configured location
    plain = backend.read_bytes(entry)
    assert blob.path.read_bytes() != plain  # never the raw file on disk
    assert "clip" not in blob.path.name  # nor its name
    assert mgr.store.read(blob) == plain


def test_clear_and_prune_remove_files(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path / "a.zip")
    base = tmp_path / "scratch"
    base.mkdir()
    (base / "archy-oldsession").mkdir()  # leftover of a crashed session
    backend = backend_for(archive)
    mgr = TempManager(_FakeSettings(temp_mode="disk", temp_location="custom", temp_dir=str(base)))
    entry = next(e for e in backend.list_effective_entries() if e.path == "media/clip.mp4")

    blob = mgr.spill_entry(backend, entry)
    assert blob is not None and blob.path.exists()
    mgr.clear_preview()
    assert not blob.path.exists()

    blob2 = mgr.spill_entry(backend, entry)
    assert blob2 is not None and blob2.path.exists()
    mgr.prune()
    assert not blob2.path.exists()
    assert not list(base.glob("archy-*"))  # old sessions too


def test_location_change_applies_to_new_files_and_cleans_both(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path / "a.zip")
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    backend = backend_for(archive)
    settings = _FakeSettings(temp_mode="disk", temp_location="custom", temp_dir=str(first))
    mgr = TempManager(settings)
    entry = next(e for e in backend.list_effective_entries() if e.path == "media/clip.mp4")
    old = mgr.spill_entry(backend, entry)
    assert first in old.path.parents

    settings.temp_dir = str(second)  # user picks another folder and saves
    mgr.configure_backend(backend)
    new = mgr.spill_entry(backend, entry)
    assert second in new.path.parents  # new files go to the new location
    mgr.prune()
    assert not old.path.exists() and not new.path.exists()  # both cleaned
