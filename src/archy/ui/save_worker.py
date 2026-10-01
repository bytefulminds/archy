from __future__ import annotations

import contextlib
import threading
import time

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from archy.core.backend import ArchiveBackend

# Saving a big 7z rewrites the whole archive (48 GB: minutes), so it runs here,
# off the GUI thread, with progress and cancel - like extracting. Progress is
# throttled to a few updates per second; the final "all bytes written" update
# is always sent (the dialog then shows the verification step).

_PROGRESS_INTERVAL = 0.1


class SaveWorker(QObject):
    progress = Signal("qint64", "qint64", str, int)  # done, total, name, files
    finished = Signal(bool, bool, str)  # saved, cancelled, error

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._cancel = threading.Event()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self, backend: ArchiveBackend) -> None:
        self._cancel.clear()
        self._running = True
        self._pool.start(_SaveTask(self, backend))

    def cancel(self) -> None:
        self._cancel.set()

    def wait(self, msecs: int = -1) -> bool:
        return self._pool.waitForDone(msecs)

    def _done(self) -> None:
        self._running = False


class _SaveTask(QRunnable):
    def __init__(self, owner: SaveWorker, backend: ArchiveBackend) -> None:
        super().__init__()
        self._owner = owner
        self._backend = backend

    def run(self) -> None:
        from archy.core.backends.sevenzip_writer import SaveCancelled

        owner = self._owner
        last = [0.0]

        def on_progress(done: int, total: int, name: str, files: int) -> None:
            now = time.monotonic()
            final = not name and done >= total
            if final or now - last[0] >= _PROGRESS_INTERVAL:
                last[0] = now
                owner.progress.emit(done, total, name, files)

        saved, cancelled, error = False, False, ""
        try:
            self._backend.save(on_progress=on_progress, is_cancelled=owner._cancel.is_set)
            saved = True
            # Read the new index here, not on the GUI thread when the window
            # reloads (100,000 entries: seconds).
            with contextlib.suppress(Exception):
                list(self._backend.list_entries())
        except SaveCancelled:
            cancelled = True
        except Exception as exc:  # noqa: BLE001 - shown to the user
            error = str(exc)
        owner._done()
        owner.finished.emit(saved, cancelled, error)
