from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from archy.core.backend import ArchiveBackend, ArchiveEntry
from archy.core.errors import ArchiveError, EncryptedArchiveError
from archy.i18n import tr

if TYPE_CHECKING:
    from collections.abc import Callable

    from archy.services.temp_files import TempManager

# Reads a single entry off the UI thread so previewing an entry near the end of
# a huge archive (where libarchive must scan to reach it) never freezes the
# window. Each request carries an id; the window ignores results whose id is no
# longer the current selection. Reads are serialised (one worker) to bound
# memory and avoid concurrent access to the same archive.


class PreviewLoader(QObject):
    loaded = Signal(int, object)  # request_id, bytes
    failed = Signal(int, str)  # request_id, reason ("encrypted" or a message)
    spilled = Signal(int, object)  # request_id, encrypted temp Blob
    # 64-bit byte counts (a plain int signal overflows above 2 GB).
    spill_progress = Signal(int, "qint64", "qint64")  # request_id, bytes_done, bytes_total

    def __init__(self) -> None:
        super().__init__()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

    def submit(
        self,
        request_id: int,
        backend: ArchiveBackend,
        entry: ArchiveEntry,
        max_bytes: int | None,
    ) -> None:
        self._pool.start(_ReadTask(self, request_id, backend, entry, max_bytes))

    def submit_spill(
        self,
        request_id: int,
        backend: ArchiveBackend,
        entry: ArchiveEntry,
        temp: TempManager,
        is_cancelled: Callable[[], bool],
    ) -> None:
        """Stream an entry into the encrypted temp store off the UI thread (for
        a video/PDF read from disk), then emit ``spilled`` with its blob."""
        self._pool.start(_SpillTask(self, request_id, backend, entry, temp, is_cancelled))


class _ReadTask(QRunnable):
    def __init__(
        self,
        loader: PreviewLoader,
        request_id: int,
        backend: ArchiveBackend,
        entry: ArchiveEntry,
        max_bytes: int | None,
    ) -> None:
        super().__init__()
        self._loader = loader
        self._id = request_id
        self._backend = backend
        self._entry = entry
        self._max_bytes = max_bytes

    def run(self) -> None:
        try:
            data = self._backend.read_bytes(self._entry, max_bytes=self._max_bytes)
        except EncryptedArchiveError:
            self._loader.failed.emit(self._id, "encrypted")
            return
        except ArchiveError as exc:
            self._loader.failed.emit(self._id, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - report anything unexpected to the UI
            self._loader.failed.emit(self._id, str(exc))
            return
        self._loader.loaded.emit(self._id, data)


class _Gone(Exception):
    """The receiving window no longer exists."""


class _SpillTask(QRunnable):
    def __init__(
        self,
        loader: PreviewLoader,
        request_id: int,
        backend: ArchiveBackend,
        entry: ArchiveEntry,
        temp: TempManager,
        is_cancelled: Callable[[], bool],
    ) -> None:
        super().__init__()
        self._loader = loader
        self._id = request_id
        self._backend = backend
        self._entry = entry
        self._temp = temp
        self._is_cancelled = is_cancelled

    def run(self) -> None:
        try:
            self._run()
        except (_Gone, RuntimeError):
            # The window closed while this was running (its signals are gone):
            # nothing left to report to.
            return

    def _run(self) -> None:
        def on_progress(done: int, total: int, _name: str) -> None:
            try:
                self._loader.spill_progress.emit(self._id, done, total)
            except RuntimeError:  # the window closed meanwhile: stop quietly
                raise _Gone from None

        try:
            blob = self._temp.spill_entry(
                self._backend,
                self._entry,
                on_progress=on_progress,
                is_cancelled=self._is_cancelled,
            )
        except EncryptedArchiveError:
            self._loader.failed.emit(self._id, "encrypted")
            return
        except ArchiveError as exc:
            self._loader.failed.emit(self._id, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - report anything unexpected to the UI
            self._loader.failed.emit(self._id, str(exc))
            return
        if blob is None:
            if not self._is_cancelled():
                reason = tr("preview.no_disk_space")
                self._loader.failed.emit(self._id, reason)
            return  # cancelled (stale selection) or no room on disk
        self._loader.spilled.emit(self._id, blob)
