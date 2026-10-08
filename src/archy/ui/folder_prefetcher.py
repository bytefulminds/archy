from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, QTimer, Signal

from archy.core.backend import ArchiveBackend, ArchiveEntry

if TYPE_CHECKING:
    from archy.ui.thumbnail_loader import ThumbnailLoader

# The folder pass: decodes a folder's files in ONE pass on a background thread
# (a solid 7z is only fast when read front to back) when the folder is opened
# in a thumbnail view, or on the first file opened in it. Files that fit the
# RAM budget land in the backend's folder buffer; every image is handed to the
# thumbnail loader as it comes out, so all thumbnails are made during the same
# pass. Progress counts a file as done once it is decoded AND (for an image)
# its thumbnail is ready, so when the indicator finishes the whole folder
# browses instantly. A pass for a folder the user already left is discarded
# (generation guard).

# Progress arrives twice per file (decoded, thumbnailed). Updating the status
# bar that often repaints the whole (frameless) window each time - thousands of
# synchronous full repaints on a big folder, which froze the UI on Windows. The
# GUI side forwards at most one update per interval (always the latest value).
_PROGRESS_INTERVAL_MS = 100
_ACTIVITY_INTERVAL_MS = 400  # how often a running pass reports it is alive


class FolderPrefetcher(QObject):
    started = Signal(int)  # a folder pass launched (files in the folder)
    progress = Signal(int, int)  # done, total - files decoded and thumbnailed
    done = Signal()  # the pass for the current folder finished
    # While a pass runs, a few times a second: (bytes done, bytes total) of the
    # compressed data it decodes when the backend can tell, else (-1, -1). Also
    # the heartbeat that keeps the indicator alive before any file is done.
    # 64-bit: a block of a big archive is easily more than 2 GB.
    activity = Signal("qint64", "qint64")
    _done = Signal(int, int)  # worker -> GUI thread (count buffered, pass token)
    _progress = Signal(int, int, int)  # worker -> GUI thread (done, total, pass token)

    def __init__(self) -> None:
        super().__init__()
        self._done.connect(self._on_done)
        self._progress.connect(self._on_progress)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._pool.setThreadPriority(QThread.Priority.LowPriority)  # the UI goes first
        self._backend: ArchiveBackend | None = None
        self._thumbs: ThumbnailLoader | None = None
        self._generation = 0
        # Identifies the current pass across archives. A backend's own folder
        # generation starts at 1 again for every archive, so a pass that was
        # still finishing for the previous archive could otherwise be taken
        # for the new one (ending its indicator too early).
        self._token = 0
        self._armed = False
        self._running = False  # a pass for the current token is in flight
        self._latest: tuple[int, int] | None = None
        self._progress_timer = QTimer(self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.setInterval(_PROGRESS_INTERVAL_MS)
        self._progress_timer.timeout.connect(self._flush_progress)
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(_ACTIVITY_INTERVAL_MS)
        self._activity_timer.timeout.connect(self._poll_activity)

    def set_thumbnails(self, thumbs: ThumbnailLoader | None) -> None:
        """The loader that receives the images decoded by each pass."""
        self._thumbs = thumbs

    def set_backend(self, backend: ArchiveBackend | None) -> None:
        self._backend = backend
        self._armed = False
        self._token += 1  # whatever still runs for the previous archive is stale
        self._end_stale_pass()

    def start_folder(self) -> None:
        """Arm the pass for the current folder (does not decode yet)."""
        if self._backend is None:
            self._armed = False
            self._end_stale_pass()
            return
        self._generation = self._backend.begin_folder()
        self._token += 1
        self._armed = True
        self._end_stale_pass()

    def _end_stale_pass(self) -> None:
        """A pass that was running belongs to another folder or archive now:
        its own end will be ignored, so end its indicator here (otherwise the
        progress bar would stay up after closing the archive or leaving the
        folder)."""
        if self._running:
            self._running = False
            self._progress_timer.stop()
            self._activity_timer.stop()
            self._latest = None
            self.done.emit()

    def ensure(self, entries: list[ArchiveEntry]) -> None:
        """Run the folder pass once for the current folder."""
        if not self._armed or self._backend is None or not entries:
            return
        self._armed = False
        files = [e for e in entries if not e.is_dir]
        thumbs = self._thumbs
        wanted = thumbs.wanted_paths(files) if thumbs is not None else set()
        thumb_generation = thumbs.generation if thumbs is not None else 0
        self._running = True
        self.started.emit(len(files))
        self._activity_timer.start()
        self._pool.start(
            _PrefetchTask(
                self,
                self._backend,
                files,
                self._generation,
                thumbs,
                wanted,
                thumb_generation,
                self._token,
            )
        )

    def _on_progress(self, done: int, total: int, token: int) -> None:
        if token != self._token:
            return
        self._latest = (done, total)
        if not self._progress_timer.isActive():
            self._flush_progress()  # first one right away, then throttled
            self._progress_timer.start()

    def _flush_progress(self) -> None:
        if self._latest is not None:
            latest, self._latest = self._latest, None
            self.progress.emit(*latest)

    def _poll_activity(self) -> None:
        if not self._running or self._backend is None:
            self._activity_timer.stop()
            return
        progress = self._backend.group_progress()
        self.activity.emit(*(progress if progress is not None else (-1, -1)))

    def _on_done(self, _count: int, token: int) -> None:
        if token == self._token:
            self._running = False
            self._progress_timer.stop()
            self._activity_timer.stop()
            self._flush_progress()  # the final "N / N" is never dropped
            self.done.emit()


class _PrefetchTask(QRunnable):
    def __init__(
        self,
        prefetcher: FolderPrefetcher,
        backend: ArchiveBackend,
        entries: list[ArchiveEntry],
        generation: int,
        thumbs: ThumbnailLoader | None,
        wanted: set[str],
        thumb_generation: int,
        token: int,
    ) -> None:
        super().__init__()
        self._prefetcher = prefetcher
        self._backend = backend
        self._entries = entries
        self._generation = generation
        self._thumbs = thumbs
        self._wanted = wanted
        self._thumb_generation = thumb_generation
        self._token = token

    def run(self) -> None:
        cond = threading.Condition()
        state = {"decoded": 0, "total": len(self._entries), "inflight": 0}
        thumbs = self._thumbs
        # Backpressure: at most this many decoded images wait for a thumbnail,
        # so a folder larger than RAM never piles up in memory.
        backlog = threading.Semaphore(thumbs.threads * 2 if thumbs is not None else 1)

        def emit() -> None:  # call with cond held
            finished = max(0, state["decoded"] - state["inflight"])
            self._prefetcher._progress.emit(finished, state["total"], self._token)

        def on_progress(done: int, total: int) -> None:
            with cond:
                state["decoded"] = done
                state["total"] = total
                emit()

        def thumb_finished() -> None:
            with cond:
                state["inflight"] -= 1
                emit()
                cond.notify_all()
            backlog.release()

        def observer(entry: ArchiveEntry, data: bytes) -> None:
            backlog.acquire()
            with cond:
                state["inflight"] += 1
            thumbs.submit_decoded(self._thumb_generation, entry, data, thumb_finished)

        wanted = self._wanted
        use_thumbs = thumbs is not None and bool(wanted)
        try:
            count = self._backend.read_group(
                self._entries,
                self._generation,
                on_progress=on_progress,
                observe=(lambda e: e.path in wanted) if use_thumbs else None,
                observer=observer if use_thumbs else None,
            )
        except Exception:  # noqa: BLE001 - the pass is best-effort
            count = 0
        # The indicator ends when the last thumbnail is ready, not when the
        # last byte is decoded.
        with cond:
            cond.wait_for(lambda: state["inflight"] <= 0, timeout=120)
            state["decoded"] = state["total"]
            emit()
        self._prefetcher._done.emit(count, self._token)
