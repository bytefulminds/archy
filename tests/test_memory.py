from __future__ import annotations

from archy.core import memory

_GB = 1024 * 1024 * 1024


def _machine(monkeypatch, total_gb: float, available_gb: float) -> None:
    monkeypatch.setattr(memory, "total", lambda: int(total_gb * _GB))
    monkeypatch.setattr(memory, "available", lambda: int(available_gb * _GB))


def test_reserve_is_at_least_two_gb_or_a_fifth(monkeypatch):
    _machine(monkeypatch, 8, 4)
    assert memory.reserve() == 2 * _GB
    _machine(monkeypatch, 32, 20)
    assert memory.reserve() == int(32 * _GB * 0.2)


def test_spare_never_touches_the_reserve(monkeypatch):
    _machine(monkeypatch, 16, 9)  # reserve 3.2 GB
    assert memory.spare() == 9 * _GB - int(16 * _GB * 0.2)
    _machine(monkeypatch, 16, 2)  # less free than the reserve
    assert memory.spare() == 0


def test_fits_counts_every_copy(monkeypatch):
    _machine(monkeypatch, 16, 9)  # ~5.8 GB spare
    assert memory.fits(2 * _GB)
    assert not memory.fits(2 * _GB, copies=3)  # a 2 GB video read from 7z


def test_budget_follows_what_is_free_now(monkeypatch):
    _machine(monkeypatch, 16, 12)
    roomy = memory.budget(0.6, floor=1, ceiling=100 * _GB)
    _machine(monkeypatch, 16, 5)  # memory filled up since
    tight = memory.budget(0.6, floor=1, ceiling=100 * _GB)
    assert tight < roomy


def test_folder_pass_stops_keeping_files_when_memory_gets_tight(tmp_path, monkeypatch):
    # The budget is set at the start of a pass; a live check per file keeps the
    # pass from eating into the reserve if memory runs low while it runs.
    # Files are still decoded (and observed, for thumbnails), just not kept.
    import io

    import pytest

    py7zr = pytest.importorskip("py7zr")
    from archy.core import backend_for

    archive = tmp_path / "solid.7z"
    with py7zr.SevenZipFile(str(archive), "w") as out:
        for i in range(5):
            out.writef(io.BytesIO(bytes([i]) * 5000), f"sub/f{i}.jpg")

    backend = backend_for(archive)
    entries = [e for e in backend.list_entries() if not e.is_dir]
    calls = {"n": 0}

    def fits(_size, copies=1):  # noqa: ARG001
        calls["n"] += 1
        return calls["n"] <= 2  # room for two, then memory is tight

    monkeypatch.setattr(memory, "fits", fits)
    seen: list[str] = []
    gen = backend.begin_folder()
    stored = backend.read_group(
        entries, gen, observe=lambda e: True, observer=lambda e, d: seen.append(e.path)
    )
    assert stored == 2
    assert len(backend._folder_buffer) == 2
    assert len(seen) == 5  # every file still reached the thumbnail observer
    backend.close()


def test_total_cap_limits_archy_as_a_whole(monkeypatch):
    _machine(monkeypatch, 16, 14)  # lots free: the cap decides
    monkeypatch.setattr(memory, "process_rss", lambda: 10 * _GB)
    memory.configure(memory.Limits(cap_percent=75))  # 12 GB
    assert memory.spare() == 2 * _GB
    memory.configure(memory.Limits(cap_bytes=11 * _GB))  # fixed instead
    assert memory.spare() == 1 * _GB


def test_fixed_reserve_overrides_auto(monkeypatch):
    _machine(monkeypatch, 16, 9)
    memory.configure(memory.Limits(reserve_bytes=1 * _GB))
    assert memory.reserve() == 1 * _GB


def test_fixed_or_auto_and_threads():
    assert memory.fixed_or(None, 123) == 123
    assert memory.fixed_or(456, 123) == 456
    assert memory.worker_threads(12) == 6  # auto: cores - 2, at most 6
    assert memory.worker_threads(3) == 2  # auto: at least 2
    memory.configure(memory.Limits(threads=10))
    assert memory.worker_threads(12) == 10
