from __future__ import annotations

import threading
import time
from collections.abc import Iterable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from archy.core.backend import ArchiveBackend, ArchiveEntry

# Runs an extraction off the UI thread so the window stays responsive while a
# large archive is written to disk. Progress is throttled; cancellation is a
# cooperative threading.Event passed to the backend as is_cancelled.

_PROGRESS_INTERVAL = 0.05  # seconds between progress signals


class ExtractWorker(QObject):
    # Byte counts as 64-bit: a plain int signal is 32-bit in Qt, so above 2 GB
    # PySide printed a bare "OverflowError" for every update and passed 0.
    # done_bytes, total_bytes, current_name, files_started (counted on every
    # backend callback, so the throttling below never makes it skip files)
    progress = Signal("qint64", "qint64", str, int)
    finished = Signal(object)  # ExtractResult

    def __init__(self) -> None:
        super().__init__()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._cancel: threading.Event | None = None

    def start(
        self,
        backend: ArchiveBackend,
        entries: Iterable[ArchiveEntry],
        dest_dir: str,
    ) -> None:
        self._cancel = threading.Event()
        self._pool.start(_ExtractTask(self, backend, list(entries), dest_dir, self._cancel))

    def cancel(self) -> None:
        if self._cancel is not None:
            self._cancel.set()


class _ExtractTask(QRunnable):
    def __init__(
        self,
        worker: ExtractWorker,
        backend: ArchiveBackend,
        entries: list[ArchiveEntry],
        dest_dir: str,
        cancel: threading.Event,
    ) -> None:
        super().__init__()
        self._worker = worker
        self._backend = backend
        self._entries = entries
        self._dest_dir = dest_dir
        self._cancel = cancel

    def run(self) -> None:
        last = 0.0
        files = 0
        current = None

        def on_progress(done: int, total: int, name: str) -> None:
            nonlocal last, files, current
            if name != current:
                current = name
                files += 1
            now = time.monotonic()
            if now - last >= _PROGRESS_INTERVAL or done >= total:
                last = now
                self._worker.progress.emit(done, total, name, files)

        try:
            result = self._backend.extract(
                self._entries,
                self._dest_dir,
                on_progress=on_progress,
                is_cancelled=self._cancel.is_set,
            )
        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            from archy.core.extraction import ExtractResult

            result = ExtractResult()
            result.failed.append(("", str(exc)))
        self._worker.finished.emit(result)
