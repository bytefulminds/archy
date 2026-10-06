from __future__ import annotations

import contextlib
import io
import threading
from collections import OrderedDict
from collections.abc import Callable

from PySide6.QtCore import (
    QObject,
    QRunnable,
    QSize,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QImage, QPixmap, QTransform

from archy.core import memory
from archy.core.backend import ArchiveBackend, ArchiveEntry
from archy.core.secure_temp import disk_reserve
from archy.services.image_decode import decode_image
from archy.services.preview import PreviewKind, kind_for_name

# Thumbnail generation for the grid views. Images are decoded straight to
# thumbnail size (GIL-free, see _decode_image_thumb); videos get an early frame
# decoded via PyAV from a bounded prefix. Results live in an LRU cache sized to
# hold a whole folder; failures are cached so they are not retried; work is
# tagged with a generation so a folder change drops stale results.
#
# Where the work comes from depends on the backend:
# - Solid / sequential archives (7z, tar): the folder pass (FolderPrefetcher +
#   ArchiveBackend.read_group) decodes the folder once and hands every image to
#   submit_decoded() as it comes out, so all thumbnails are made during that one
#   pass and the folder browses instantly afterwards. While the pass runs,
#   visible tiles wait for it instead of decoding the same block again.
# - Seek-friendly archives (zip): each file is cheap to read on its own, so the
#   loader fills the whole folder itself in the background while a thumbnail
#   view is showing (visible tiles first), and reports progress(done, total).

_THUMB_SIZE = 240  # source size; grid tiles cover-crop from this
_MAX_SOURCE_BYTES = 40 * 1024 * 1024  # skip images larger than this
_VIDEO_THUMB_PREFIX = 16 * 1024 * 1024  # bytes read to find an early video frame
_VIDEO_FRAME_INDEX = 5  # decode up to this many frames, use the last (skip a black intro)
# On a solid archive (7z) reading one entry decodes its block, so a video is
# thumbnailed there only when its (uncompressed) bytes still fit comfortably in
# memory. Ceiling below; the effective cap is lowered on low-RAM machines - see
# _video_thumb_solid_cap.
_VIDEO_THUMB_SOLID_CEILING = 1024 * 1024 * 1024


# Decoding is GIL-free, so use the cores (leaving two for the UI and the pass);
# Settings > Advanced can fix the number (see memory.worker_threads).
def _threads() -> int:
    return memory.worker_threads(QThread.idealThreadCount())


# The cache holds a whole folder's thumbnails: a share of free RAM, bounded.
_CACHE_RAM_FRACTION = 0.10  # of spare RAM (see archy.core.memory)
_CACHE_MIN_BYTES = 128 * 1024 * 1024
_CACHE_MAX_BYTES = 1024 * 1024 * 1024
_THUMB_BYTES_ESTIMATE = _THUMB_SIZE * (_THUMB_SIZE * 3 // 4) * 4  # a 4:3 ARGB thumbnail
_PRIO_VISIBLE = 10  # QThreadPool priority: visible tiles jump the queue
_PRIO_PRELOAD = 0


def _pyav_available() -> bool:
    try:
        import av  # noqa: F401
    except Exception:  # noqa: BLE001 - optional dependency
        return False
    return True


# A whole-video read from a solid archive briefly holds the bytes about three
# times (py7zr's buffer, the result, PyAV's view). Only one runs at a time.
_VIDEO_THUMB_COPIES = 3
_solid_video_gate = threading.Semaphore(1)


def _video_thumb_solid_cap() -> int:
    """Max (uncompressed) video size to thumbnail on a solid archive: as large as
    fits in spare memory right now (all copies included, the OS reserve left
    alone - see archy.core.memory), capped at 1 GB. Measured live, so it shrinks
    once a folder fills memory."""
    return int(min(_VIDEO_THUMB_SOLID_CEILING, memory.spare() // _VIDEO_THUMB_COPIES))


def _decode_image_thumb(data: bytes, name: str = "") -> QImage:
    """Decode image bytes straight to thumbnail size, upright (see
    services/image_decode.py: scaled JPEG decoding, GIL released, HEIC, the
    embedded preview of a camera RAW, a PSD's composite)."""
    return decode_image(data, QSize(_THUMB_SIZE, _THUMB_SIZE), name)


def _decode_video_frame(data: object) -> QImage | None:
    """Decode an early frame from video bytes (possibly a truncated prefix) or a
    seekable file-like object (an encrypted temp blob reader), and return it as
    a QImage; None if PyAV is missing or no frame can be decoded (e.g. an MP4
    whose index sits past the prefix)."""
    try:
        import av
    except Exception:  # noqa: BLE001 - optional dependency, no video thumbnails
        return None
    try:
        source = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
        with av.open(source) as container:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            last = None
            for count, frame in enumerate(container.decode(stream), start=1):
                last = frame
                if count >= _VIDEO_FRAME_INDEX:
                    break
            if last is None:
                return None
            rgb = last.to_rgb()
            plane = rgb.planes[0]
            # line_size is the (possibly padded) row stride; QImage honours it.
            image = QImage(
                bytes(plane), rgb.width, rgb.height, plane.line_size, QImage.Format.Format_RGB888
            ).copy()
            return _upright(image, _video_rotation(last, stream))
    except Exception:  # noqa: BLE001 - undecodable prefix just gets no thumbnail
        return None


def _video_rotation(frame: object, stream: object) -> int:
    """How far the frame must turn counter-clockwise to stand upright, in
    degrees. Phones film portrait clips as landscape frames with a "display
    matrix" saying how to turn them (players apply it; a decoded frame does
    not). PyAV reports it as ``frame.rotation`` (counter-clockwise, like
    ffmpeg); older PyAV only has the stream's ``rotate`` tag (clockwise)."""
    rotation = getattr(frame, "rotation", None)
    if rotation is None:
        try:
            rotation = -int(float(stream.metadata.get("rotate", 0)))  # type: ignore[attr-defined]
        except (AttributeError, TypeError, ValueError):
            rotation = 0
    try:
        return round(float(rotation) / 90) * 90 % 360
    except (TypeError, ValueError):
        return 0


def _upright(image: QImage, rotation: int) -> QImage:
    if not rotation or image.isNull():
        return image
    # QTransform.rotate turns clockwise on screen (y points down).
    return image.transformed(QTransform().rotate(-rotation))


def _cache_budget() -> int:
    auto = memory.budget(_CACHE_RAM_FRACTION, floor=_CACHE_MIN_BYTES, ceiling=_CACHE_MAX_BYTES)
    return memory.fixed_or(memory.limits().thumb_cache_bytes, auto)


def _pixmap_bytes(pixmap: QPixmap) -> int:
    return max(1, pixmap.width() * pixmap.height() * 4)


class ThumbnailLoader(QObject):
    ready = Signal(str)  # entry path whose thumbnail just became available ("" = repaint)
    progress = Signal(int, int)  # done, total of the loader's own work (0, 0 = idle)
    _done = Signal(int, str, object, bool)  # generation, path, QImage, preload

    def __init__(self) -> None:
        super().__init__()
        self._done.connect(self._on_done)
        # Tiles and zip background loading read from the archive; the folder
        # pass hands over bytes it already decoded. Separate pools, so pass work
        # can never be stuck behind a read waiting for the pass to finish.
        # Low thread priority: the UI thread always wins the CPU, so a folder
        # full of thumbnails being decoded on every core never makes the
        # window stutter (the OS marks a starved window "not responding").
        self._pool = QThreadPool(self)
        self._pool.setThreadPriority(QThread.Priority.LowPriority)
        self._pass_pool = QThreadPool(self)
        self._pass_pool.setThreadPriority(QThread.Priority.LowPriority)
        self._threads = _threads()
        self._pool.setMaxThreadCount(self._threads)
        self._pass_pool.setMaxThreadCount(self._threads)
        self._cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._cache_bytes = 0
        self._cache_budget = _cache_budget()
        self._pending: set[str] = set()
        self._failed: set[str] = set()
        self._generation = 0
        self._backend: ArchiveBackend | None = None
        self._av = _pyav_available()
        self._pass_active = False  # a folder pass is producing the thumbnails
        # Background loading (seek-friendly backends).
        self._active = False  # a thumbnail view is showing
        self._folder: list[ArchiveEntry] = []  # thumbnailable entries, display order
        self._goal: set[str] = set()  # everything being worked on (progress total)
        self._finished: set[str] = set()  # of those, done (progress done)
        self._preload_inflight = 0
        self._pump_from = 0  # background entries before this index are handled
        self._last_progress = (0, 0)
        # Progress drives a status-bar widget whose updates repaint the window;
        # emit at most ~10 per second (see FolderPrefetcher for the same reason).
        self._progress_timer = QTimer(self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.setInterval(100)
        self._progress_timer.timeout.connect(self._on_progress_tick)
        self._progress_dirty = False
        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.setInterval(0)
        self._rescan_timer.timeout.connect(self._rescan)

    # --------------------------------------------------------------- setup
    @property
    def generation(self) -> int:
        return self._generation

    @property
    def threads(self) -> int:
        return self._threads

    def apply_limits(self) -> None:
        """Re-read the memory policy after a settings change: thread count and
        cache size take effect immediately (the cache shrinks right away)."""
        self._threads = _threads()
        self._pool.setMaxThreadCount(self._threads)
        self._pass_pool.setMaxThreadCount(self._threads)
        self._cache_budget = _cache_budget()
        while self._cache_bytes > self._cache_budget and len(self._cache) > 1:
            _, evicted = self._cache.popitem(last=False)
            self._cache_bytes -= _pixmap_bytes(evicted)

    def set_backend(self, backend: ArchiveBackend | None) -> None:
        if backend is not self._backend:
            # Thumbnails are keyed by path inside the archive: another archive
            # (or an archive inside this one) can have the same paths.
            self._cache.clear()
            self._cache_bytes = 0
        self._backend = backend
        self.clear()

    def clear(self) -> None:
        # New folder/archive: drop pending work (bump generation) but keep the
        # LRU cache (revisiting a folder stays instant). Failures are folder-
        # dependent (a video not yet buffered may become buffered), so retry them.
        self._generation += 1
        self._pending.clear()
        self._failed.clear()
        self._pass_active = False
        self._folder = []
        self._goal = set()
        self._finished = set()
        self._preload_inflight = 0
        self._pump_from = 0
        self._rescan_timer.stop()
        self._flush_progress()

    def set_folder(self, entries: list[ArchiveEntry]) -> None:
        """The current folder's file entries in display order."""
        self._folder = [e for e in entries if self._is_thumbnailable(e)]
        self._rescan()

    def set_active(self, active: bool) -> None:
        """Whether a thumbnail view is showing (zip background loading only then)."""
        if active != self._active:
            self._active = active
            self._rescan()

    def set_pass_active(self, active: bool) -> None:
        """A folder pass started/finished. While it runs on a solid archive,
        tiles wait for it; when it ends, repaint so anything it did not cover
        (videos) is requested normally."""
        self._pass_active = active
        if not active:
            self._rescan()
            self.ready.emit("")

    # ------------------------------------------------------- folder pass
    def wanted_paths(self, entries: list[ArchiveEntry]) -> set[str]:
        """Images in ``entries`` that still need a thumbnail - what the folder
        pass should hand to submit_decoded(). GUI thread."""
        return {
            e.path
            for e in entries
            if e.path not in self._cache
            and kind_for_name(e.name) is PreviewKind.IMAGE
            and self._is_thumbnailable(e)
        }

    def submit_decoded(
        self,
        generation: int,
        entry: ArchiveEntry,
        data: bytes,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        """Make a thumbnail from bytes the folder pass already decoded. Safe to
        call from any thread; ``on_finished`` runs on the worker when done
        (always, also for stale work) so the pass can pace itself."""
        task = _ThumbTask(self, generation, None, entry, False, data=data, on_finished=on_finished)
        self._pass_pool.start(task)

    # ------------------------------------------------------------ tiles
    def thumbnail(self, entry: ArchiveEntry) -> QPixmap | None:
        """Return a cached thumbnail, or None (scheduling a load) if not ready.

        Returns None for entries that will never get a thumbnail (unsupported,
        too large, or a video too expensive to reach) as well - the caller then
        shows a type icon.
        """
        pm = self._cache.get(entry.path)
        if pm is not None:
            self._cache.move_to_end(entry.path)
            return pm
        if entry.path in self._failed:
            return None
        if not self._is_thumbnailable(entry) or self._backend is None:
            return None
        if entry.path in self._pending:
            return None
        if not self._backend.fast_seek and (
            self._pass_active or self._backend.is_prefetching(entry.path)
        ):
            # The folder pass is decoding this folder and makes the thumbnail
            # itself; a separate read now would decode the same solid block a
            # second time (and wait for the pass anyway).
            return None
        self._goal.add(entry.path)
        self._start(entry, preload=False)
        return None

    # ------------------------------------------------------------ internals
    def _start(self, entry: ArchiveEntry, *, preload: bool) -> None:
        self._pending.add(entry.path)
        self._goal.add(entry.path)
        if preload:
            self._preload_inflight += 1
        task = _ThumbTask(self, self._generation, self._backend, entry, preload)
        self._pool.start(task, _PRIO_PRELOAD if preload else _PRIO_VISIBLE)

    def _background_enabled(self) -> bool:
        return self._active and self._backend is not None and self._backend.fast_seek

    def _rescan(self) -> None:
        """(Re)build the background set for a seek-friendly backend and top it
        up. Solid archives get theirs from the folder pass instead."""
        self._rescan_timer.stop()
        self._pump_from = 0
        if self._background_enabled():
            limit = max(1, self._cache_budget // _THUMB_BYTES_ESTIMATE)
            self._goal |= {e.path for e in self._folder[:limit]}
        self._pump()
        self._emit_progress()

    def _pump(self) -> None:
        if not self._background_enabled():
            return
        # Walk forward from where the last pump stopped: entries before the
        # cursor are started or done, so each call is O(new work), not O(folder).
        limit = min(len(self._folder), max(1, self._cache_budget // _THUMB_BYTES_ESTIMATE))
        while self._pump_from < limit and self._preload_inflight < self._threads * 2:
            entry = self._folder[self._pump_from]
            self._pump_from += 1
            path = entry.path
            if (
                path in self._cache
                or path in self._pending
                or path in self._failed
                or path in self._finished
            ):
                continue
            self._start(entry, preload=True)

    def _emit_progress(self) -> None:
        if not self._progress_timer.isActive():
            self._flush_progress()  # first one right away, then throttled
            self._progress_timer.start()
        else:
            self._progress_dirty = True

    def _on_progress_tick(self) -> None:
        if self._progress_dirty:
            self._flush_progress()
            self._progress_timer.start()

    def _flush_progress(self) -> None:
        self._progress_dirty = False
        total = len(self._goal)
        done = len(self._finished & self._goal)
        busy = total and done < total
        state = (done, total) if busy else (0, 0)
        if state != self._last_progress:
            self._last_progress = state
            self.progress.emit(*state)

    def can_show(self, entry: ArchiveEntry) -> bool:
        """Whether a picture can be made of this entry (not too large, not a
        file that already failed to decode): what a slideshow shows."""
        return self._is_thumbnailable(entry) and entry.path not in self._failed

    def _is_thumbnailable(self, entry: ArchiveEntry) -> bool:
        if entry.is_dir:
            return False
        kind = kind_for_name(entry.name)
        if kind is PreviewKind.IMAGE:
            # The real (uncompressed) size: on a solid 7z the first entry of a
            # block reports the whole block as its compressed size, which used
            # to rule out the first image of every such folder.
            return int(entry.size or 0) <= _MAX_SOURCE_BYTES
        if kind is PreviewKind.VIDEO and self._av and self._backend is not None:
            # A video frame needs PyAV. On a seek-friendly backend (zip) a prefix
            # read is cheap; if the clip is already in the folder buffer it is
            # free. On a solid 7z an unbuffered read decodes the block, so we do
            # it only for videos up to a size cap, per visible tile. Bigger
            # videos stay an icon.
            if (
                self._backend.fast_seek
                or self._backend.can_stream(entry)
                or self._backend.has_buffered(entry.path)
            ):
                return True
            if self._backend.spill_mode != "memory":
                # Via the encrypted temp store: only the time it takes bounds it.
                return int(entry.size or 0) <= _VIDEO_THUMB_SOLID_CEILING
            return int(entry.size or 0) <= _video_thumb_solid_cap()
        return False

    def _on_done(self, generation: int, path: str, image: object, preload: bool) -> None:
        # Runs on the GUI thread (queued from the worker), so cache mutation and
        # QPixmap creation are safe here.
        if generation != self._generation:
            return  # stale folder; clear() already reset the bookkeeping
        self._pending.discard(path)
        if preload:
            self._preload_inflight = max(0, self._preload_inflight - 1)
        self._finished.add(path)
        if not isinstance(image, QImage) or image.isNull():
            self._failed.add(path)  # negative cache: do not retry this folder
        else:
            old = self._cache.pop(path, None)
            if old is not None:
                self._cache_bytes -= _pixmap_bytes(old)
            pixmap = QPixmap.fromImage(image)
            self._cache[path] = pixmap
            self._cache_bytes += _pixmap_bytes(pixmap)
            while self._cache_bytes > self._cache_budget and len(self._cache) > 1:
                _, evicted = self._cache.popitem(last=False)
                self._cache_bytes -= _pixmap_bytes(evicted)
            self.ready.emit(path)
        self._pump()
        self._emit_progress()


class _ThumbTask(QRunnable):
    def __init__(
        self,
        loader: ThumbnailLoader,
        generation: int,
        backend: ArchiveBackend | None,
        entry: ArchiveEntry,
        preload: bool,
        *,
        data: bytes | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._loader = loader
        self._generation = generation
        self._backend = backend
        self._entry = entry
        self._preload = preload
        self._data = data
        self._on_finished = on_finished

    def run(self) -> None:
        try:
            if self._generation != self._loader._generation:
                return  # folder changed while queued; skip the (possibly slow) work
            image = QImage()
            try:
                if self._data is not None:
                    image = _decode_image_thumb(self._data, self._entry.name)
                elif kind_for_name(self._entry.name) is PreviewKind.VIDEO:
                    image = self._video_thumbnail() or QImage()
                elif self._backend is not None:
                    data = self._backend.read_bytes(self._entry, cache=False)
                    image = _decode_image_thumb(data, self._entry.name)
            except Exception:  # noqa: BLE001 - a bad file just gets no thumbnail
                image = QImage()
            self._data = None  # let the (possibly large) bytes go before signalling
            # Hand back to the GUI thread via a queued signal.
            self._loader._done.emit(self._generation, self._entry.path, image, self._preload)
        finally:
            if self._on_finished is not None:
                self._on_finished()

    def _via_temp_store(self) -> bool:
        """Temp-files mode: a solid-archive video goes through the encrypted
        store instead of RAM - always ("Always use temporary files"), or when
        it does not fit spare memory ("To disk when memory is tight")."""
        backend = self._backend
        if backend is None or backend.spill_store is None:
            return False
        if backend.spill_mode == "disk":
            return True
        return backend.spill_mode == "overflow" and (
            int(self._entry.size or 0) > _video_thumb_solid_cap()
        )

    def _video_thumbnail_from_disk(self) -> QImage | None:
        # Stream the video into the encrypted store (never whole in RAM), let
        # PyAV read the few frames it needs straight from the decrypting reader,
        # then delete the temp blob again. One video at a time.
        backend = self._backend
        store = backend.spill_store
        size = int(self._entry.size or 0)
        if size > _VIDEO_THUMB_SOLID_CEILING:
            return None
        with _solid_video_gate:
            blob = backend.spilled_blob(self._entry.path)
            owned = blob is None
            if owned:
                if store.free_disk() - size < disk_reserve():
                    return None
                writer = store.writer()
                try:
                    ok = backend.stream_entry(self._entry, writer.write)
                except BaseException:
                    writer.abort()
                    raise
                if not ok:
                    writer.abort()
                    return None
                blob = writer.close()
            try:
                with store.open(blob) as reader:
                    frame = _decode_video_frame(reader)
            finally:
                if owned:
                    store.delete(blob)
        if frame is None or frame.isNull():
            return None
        return frame.scaled(
            QSize(_THUMB_SIZE, _THUMB_SIZE),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _video_thumbnail(self) -> QImage | None:
        backend = self._backend
        if backend is None:
            return None
        # A stored 7z reads at any offset: PyAV reads just the parts it needs
        # (also an MP4 whose index sits at the end).
        if backend.can_stream(self._entry):
            # One video at a time: probing several at once only competes with
            # the photos and the preview for the disk and the CPU.
            with _solid_video_gate:
                frame = None
                with contextlib.suppress(Exception):
                    stream = backend.open_stream(self._entry)
                    try:
                        frame = _decode_video_frame(stream)
                    finally:
                        stream.close()
            if frame is None or frame.isNull():
                return None
            return frame.scaled(
                QSize(_THUMB_SIZE, _THUMB_SIZE),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        # Read the bytes as cheaply as possible: a bounded prefix on a seek-
        # friendly backend, or the full (already in-memory) bytes when buffered.
        if backend.fast_seek:
            data = backend.read_bytes(self._entry, max_bytes=_VIDEO_THUMB_PREFIX, cache=False)
        elif backend.has_buffered(self._entry.path):
            data = backend.read_bytes(self._entry, cache=False)
        elif self._via_temp_store():
            return self._video_thumbnail_from_disk()
        else:
            # Solid archive (7z): decode the whole entry, one video at a time,
            # and only if it still fits in spare memory at this moment.
            with _solid_video_gate:
                if int(self._entry.size or 0) > _video_thumb_solid_cap():
                    return None
                data = backend.read_bytes(self._entry, cache=False)
                frame = _decode_video_frame(data)
                del data
            if frame is None or frame.isNull():
                return None
            return frame.scaled(
                QSize(_THUMB_SIZE, _THUMB_SIZE),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        frame = _decode_video_frame(data)
        if frame is None or frame.isNull():
            return None
        return frame.scaled(
            QSize(_THUMB_SIZE, _THUMB_SIZE),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
