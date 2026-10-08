"""A long folder decode reports how far it got (backend.group_progress), so
the window can show it is busy instead of a frozen "0 / N"."""

from __future__ import annotations

import os
import threading
import time

import py7zr

from archy.core.factory import backend_for


def test_solid_7z_reports_compressed_bytes_read_while_decoding(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "solid.7z"
    with py7zr.SevenZipFile(path, "w") as archive:
        for i in range(4):  # in the same solid block, before the wanted files
            archive.writestr(os.urandom(3_000_000), f"other/f{i}.bin")
        for i in range(3):
            archive.writestr(os.urandom(1_000_000), f"photos/p{i}.jpg")
    backend = backend_for(path)
    assert backend.group_progress() is None  # nothing running
    wanted = [e for e in backend.list_entries() if e.path.startswith("photos/")]
    generation = backend.begin_folder()
    seen: list[tuple[int, int]] = []
    worker = threading.Thread(target=lambda: backend.read_group(wanted, generation))
    worker.start()
    while worker.is_alive():
        progress = backend.group_progress()
        if progress is not None:
            seen.append(progress)
        time.sleep(0.005)
    worker.join()
    assert seen, "no progress while decoding"
    done = [d for d, _ in seen]
    assert done == sorted(done)  # only goes forward
    total = seen[-1][1]
    assert 0.9 * path.stat().st_size < total <= path.stat().st_size
    assert all(d <= total for d in done)
    assert backend.group_progress() is None  # cleared when done
    backend.close()
