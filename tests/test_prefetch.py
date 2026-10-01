from __future__ import annotations

import io

import pytest

from archy.core import backend_for
from archy.core.backend import ArchiveBackend, ArchiveEntry, ArchiveInfo, EntryType


def _make_7z(path, files: dict[str, bytes]) -> None:
    py7zr = pytest.importorskip("py7zr")
    with py7zr.SevenZipFile(str(path), "w") as archive:
        for name, data in files.items():
            archive.writef(io.BytesIO(data), name)


def test_read_group_fills_folder_buffer(tmp_path):
    contents = {
        "sub/a.txt": b"alpha" * 100,
        "sub/b.txt": b"beta" * 100,
        "sub/c.txt": b"gamma" * 100,
    }
    archive = tmp_path / "solid.7z"
    _make_7z(archive, contents)

    backend = backend_for(archive)
    entries = [e for e in backend.list_entries() if not e.is_dir]
    assert len(entries) == 3

    gen = backend.begin_folder()
    stored = backend.read_group(entries, gen)
    assert stored == 3
    # The buffer holds every file, and reads come back correct.
    assert set(backend._folder_buffer) == {e.path for e in entries}
    for entry in entries:
        assert backend.read_bytes(entry) == contents[entry.path]
    backend.close()


def test_read_group_reports_progress(tmp_path):
    contents = {f"sub/f{i}.txt": (f"d{i}" * 40).encode() for i in range(6)}
    archive = tmp_path / "solid.7z"
    _make_7z(archive, contents)

    backend = backend_for(archive)
    entries = [e for e in backend.list_entries() if not e.is_dir]
    gen = backend.begin_folder()
    seen: list[tuple[int, int]] = []
    backend.read_group(entries, gen, on_progress=lambda d, t: seen.append((d, t)))

    assert seen, "expected progress callbacks during the decode pass"
    assert all(total == 6 for _done, total in seen)
    dones = [done for done, _total in seen]
    assert dones == sorted(dones)  # monotonic
    assert dones[-1] == 6  # reached every file
    backend.close()


def test_read_group_discards_stale_generation(tmp_path):
    archive = tmp_path / "solid.7z"
    _make_7z(archive, {"sub/a.txt": b"x" * 50, "sub/b.txt": b"y" * 50})

    backend = backend_for(archive)
    entries = [e for e in backend.list_entries() if not e.is_dir]

    stale = backend.begin_folder()
    backend.begin_folder()  # user navigated away -> generation moved on
    stored = backend.read_group(entries, stale)
    assert stored == 0
    assert backend._folder_buffer == {}
    backend.close()


def test_begin_folder_clears_previous_buffer(tmp_path):
    archive = tmp_path / "solid.7z"
    _make_7z(archive, {"sub/a.txt": b"z" * 40})

    backend = backend_for(archive)
    entries = [e for e in backend.list_entries() if not e.is_dir]
    gen = backend.begin_folder()
    assert backend.read_group(entries, gen) == 1
    assert backend._folder_buffer
    backend.begin_folder()  # entering a new folder empties the buffer
    assert backend._folder_buffer == {}
    backend.close()


class _DummyBackend(ArchiveBackend):
    """A backend with no group optimization, to prove read_group is a safe
    no-op by default (e.g. zip, which is already fast per file)."""

    def info(self) -> ArchiveInfo:
        return ArchiveInfo(path=self.path, format="dummy", writable=False)

    def list_entries(self):
        return iter(())

    def _read_uncached(self, entry, *, password=None, max_bytes=None):
        return b""


def test_default_read_group_is_noop(tmp_path):
    backend = _DummyBackend(tmp_path / "x")
    entry = ArchiveEntry(path="a.txt", type=EntryType.FILE, size=10)
    gen = backend.begin_folder()
    assert backend.read_group([entry], gen) == 0
    assert backend._folder_buffer == {}


def test_read_group_publishes_each_file_as_it_is_decoded(tmp_path):
    # Progress fires only after the file has landed in the buffer, so the UI
    # can show thumbnails while a long solid-block decode is still running.
    contents = {f"sub/f{i}.txt": (f"v{i}" * 200).encode() for i in range(5)}
    archive = tmp_path / "solid.7z"
    _make_7z(archive, contents)

    backend = backend_for(archive)
    entries = [e for e in backend.list_entries() if not e.is_dir]
    gen = backend.begin_folder()
    buffered_at_progress: list[tuple[int, int]] = []

    def on_progress(done: int, _total: int) -> None:
        buffered_at_progress.append((done, sum(backend.has_buffered(e.path) for e in entries)))

    assert backend.read_group(entries, gen, on_progress=on_progress) == 5
    assert all(buffered >= done for done, buffered in buffered_at_progress)
    for entry in entries:
        assert backend.read_bytes(entry) == contents[entry.path]
    assert not any(backend.is_prefetching(e.path) for e in entries)
    backend.close()


def test_is_prefetching_during_pass(tmp_path):
    contents = {f"sub/f{i}.txt": b"x" * 300 for i in range(4)}
    archive = tmp_path / "solid.7z"
    _make_7z(archive, contents)

    backend = backend_for(archive)
    entries = [e for e in backend.list_entries() if not e.is_dir]
    gen = backend.begin_folder()
    seen: list[int] = []

    def on_progress(_done: int, _total: int) -> None:
        # Delivered entries are no longer "prefetching"; the rest still are.
        seen.append(sum(backend.is_prefetching(e.path) for e in entries))

    backend.read_group(entries, gen, on_progress=on_progress)
    assert seen[0] == 3  # first file delivered, three to go
    assert seen[-1] == 0
    backend.close()


def test_tar_read_group_publishes_progressively(tmp_path):
    import tarfile

    archive = tmp_path / "a.tar.gz"
    contents = {f"sub/f{i}.txt": (f"t{i}" * 100).encode() for i in range(3)}
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in contents.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    backend = backend_for(archive)
    entries = [e for e in backend.list_entries() if not e.is_dir]
    gen = backend.begin_folder()
    dones: list[int] = []
    assert backend.read_group(entries, gen, on_progress=lambda d, t: dones.append(d)) == 3
    assert dones == sorted(dones) and dones[-1] == 3
    for entry in entries:
        assert backend.read_bytes(entry) == contents[entry.path]
    backend.close()


def test_read_group_observes_entries_beyond_the_budget(tmp_path, monkeypatch):
    # Thumbnails for a whole folder are made in the same pass even when the
    # folder does not fit the RAM budget: those files are decoded and handed to
    # the observer, but not kept in the buffer.
    import archy.core.backend as backend_module

    contents = {f"sub/p{i}.png": bytes([i]) * 1000 for i in range(4)}
    archive = tmp_path / "solid.7z"
    _make_7z(archive, contents)
    monkeypatch.setattr(backend_module, "_prefetch_budget", lambda: 2000)  # room for two

    backend = backend_for(archive)
    entries = [e for e in backend.list_entries() if not e.is_dir]
    gen = backend.begin_folder()
    seen: dict[str, bytes] = {}
    stored = backend.read_group(
        entries,
        gen,
        observe=lambda e: True,
        observer=lambda e, data: seen.__setitem__(e.path, data),
    )
    assert stored == 2
    assert seen == contents  # every file observed, with the right bytes
    assert len(backend._folder_buffer) == 2
    backend.close()


@pytest.mark.parametrize("kind", ["7z", "tar.gz"])
def test_folder_pass_stops_when_the_archive_is_closed(tmp_path, kind):
    # A long pass must not keep the next archive waiting: closing the archive
    # (or opening another folder) stops it at the next file.
    import tarfile

    contents = {f"sub/f{i:02d}.bin": bytes([i]) * 20_000 for i in range(30)}
    archive = tmp_path / f"a.{kind}"
    if kind == "7z":
        _make_7z(archive, contents)
    else:
        with tarfile.open(archive, "w:gz") as tar:
            for name, data in contents.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    backend = backend_for(archive)
    entries = [e for e in backend.list_entries() if not e.is_dir]
    gen = backend.begin_folder()
    seen: list[int] = []

    def on_progress(done: int, _total: int) -> None:
        seen.append(done)
        if done == 3:
            backend.close()  # user closes the archive mid-pass

    backend.read_group(entries, gen, on_progress=on_progress)
    assert max(seen) < 10  # stopped soon after, not all 30 decoded
    with pytest.raises(Exception):  # noqa: B017 - any read of a closed archive fails fast
        backend.read_bytes(entries[-1])


def test_folder_pass_stops_when_another_folder_opens(tmp_path):
    contents = {f"sub/f{i:02d}.bin": bytes([i]) * 20_000 for i in range(30)}
    archive = tmp_path / "a.7z"
    _make_7z(archive, contents)
    backend = backend_for(archive)
    entries = [e for e in backend.list_entries() if not e.is_dir]
    gen = backend.begin_folder()
    seen: list[int] = []

    def on_progress(done: int, _total: int) -> None:
        seen.append(done)
        if done == 3:
            backend.begin_folder()  # user navigates to another folder

    backend.read_group(entries, gen, on_progress=on_progress)
    assert max(seen) < 10
    backend.close()
