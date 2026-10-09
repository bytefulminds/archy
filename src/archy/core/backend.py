from __future__ import annotations

import contextlib
import threading
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from archy.core.extraction import ExtractResult
    from archy.core.secure_temp import Blob, EncryptedTempStore

from archy.core import memory
from archy.core.errors import (
    ArchiveError,
    EncryptedArchiveError,
    ReadOnlyArchiveError,
    UnsupportedFormatError,
)
from archy.core.secure_temp import disk_reserve

__all__ = [
    "ArchiveBackend",
    "ArchiveEntry",
    "ArchiveError",
    "ArchiveInfo",
    "EncryptedArchiveError",
    "EntryType",
    "ReadOnlyArchiveError",
    "UnsupportedFormatError",
]


# LRU cache for read_bytes results (per open archive): a share of spare RAM at
# open time (see archy.core.memory), bounded; the per-entry cap keeps one huge
# file (e.g. video) from evicting everything.
_READ_CACHE_MIN_TOTAL = 128 * 1024 * 1024
_READ_CACHE_MAX_TOTAL = 1024 * 1024 * 1024
_READ_CACHE_FRACTION = 0.15
_READ_CACHE_MAX_ENTRY = 128 * 1024 * 1024


def _read_cache_budget() -> int:
    auto = memory.budget(
        _READ_CACHE_FRACTION, floor=_READ_CACHE_MIN_TOTAL, ceiling=_READ_CACHE_MAX_TOTAL
    )
    return memory.fixed_or(memory.limits().read_cache_bytes, auto)


# Folder pass: decode the current folder's (direct, non-recursive) files in ONE
# pass into an in-memory buffer. On a solid 7z this turns N decodes-from-block-
# start into one pass over the block, so browsing the folder afterwards is
# instant. The buffer gets most of the spare RAM (free memory minus the reserve
# that is always left for the OS, see archy.core.memory) and is re-checked while
# the pass runs, so it stops keeping files the moment memory gets tight. Files
# above the per-entry cap are left on-demand so one huge file never fills it.
_PREFETCH_FRACTION = 0.60
_PREFETCH_MIN_BUDGET = 128 * 1024 * 1024
_PREFETCH_MAX_BUDGET = 64 * 1024 * 1024 * 1024
_PREFETCH_MAX_ENTRY = 128 * 1024 * 1024


def _prefetch_budget() -> int:
    auto = memory.budget(
        _PREFETCH_FRACTION, floor=_PREFETCH_MIN_BUDGET, ceiling=_PREFETCH_MAX_BUDGET
    )
    return memory.fixed_or(memory.limits().folder_buffer_bytes, auto)


_STREAM_CHUNK = 1024 * 1024


class _StreamCancelled(Exception):
    """Raised inside a stream sink to stop it (the request went stale)."""


class SeekableReader(Protocol):
    """What ``ArchiveBackend.open_stream`` returns: a read-only file."""

    @property
    def size(self) -> int: ...

    def seek(self, offset: int, whence: int = 0) -> int: ...

    def read(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


class EntryType(Enum):
    FILE = auto()
    DIRECTORY = auto()
    SYMLINK = auto()
    OTHER = auto()


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    """A single entry inside an archive.

    ``path`` is the full path inside the archive, always using "/" as the
    separator regardless of platform.
    """

    path: str
    type: EntryType
    size: int = 0
    compressed_size: int | None = None
    modified: datetime | None = None
    is_encrypted: bool = False

    @property
    def name(self) -> str:
        return self.path.rstrip("/").rsplit("/", 1)[-1]

    @property
    def parent(self) -> str:
        stripped = self.path.rstrip("/")
        if "/" not in stripped:
            return ""
        return stripped.rsplit("/", 1)[0]

    @property
    def is_dir(self) -> bool:
        return self.type is EntryType.DIRECTORY


@dataclass(slots=True)
class ArchiveInfo:
    path: Path
    format: str
    writable: bool
    entry_count: int = 0


class ArchiveBackend(ABC):
    """Read, and optionally write, a single archive file.

    Contract:
    - Reading NEVER writes to disk. ``read_bytes`` returns the decompressed
      content of one entry in memory.
    - Write operations are staged and only committed by ``save`` (temp file +
      atomic replace). Backends that cannot write leave ``writable`` False and
      the write methods raise ``ReadOnlyArchiveError``.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        # Session password for encrypted archives, held in memory only (never
        # written to disk). Set once per archive; used by list/read unless a
        # call passes an explicit password.
        self._password: str | None = None
        # Bounded in-memory LRU of recently read entries, so re-opening a file
        # (or browsing back and forth) is instant even when the archive is slow
        # for random access (e.g. a solid 7z, where each read must decode from
        # the block start). Keyed by (entry path, max_bytes).
        self._read_cache: OrderedDict[tuple[str, int | None], bytes] = OrderedDict()
        self._read_cache_bytes = 0
        self._read_cache_max_total = _read_cache_budget()
        # Whole-folder prefetch buffer (full entry bytes, keyed by path) plus a
        # generation counter so a prefetch that finishes after the user has
        # navigated away is discarded instead of serving stale bytes. One lock
        # guards both the LRU cache and this buffer, because reads, thumbnails
        # and the prefetch all run on different threads.
        self._read_lock = threading.Lock()
        self._folder_buffer: dict[str, bytes] = {}
        self._folder_gen = 0
        self._closed = False  # set by close(): stops a running folder pass
        # Temp-file modes (Settings > Temporary files): the folder pass can
        # keep files in the encrypted temp store instead of RAM ("disk"), or
        # only what does not fit RAM ("overflow"). _folder_spilled maps a path
        # to its encrypted blob; reads decrypt it on demand.
        self._spill_store: EncryptedTempStore | None = None
        self._spill_mode = "memory"
        self._folder_spilled: dict[str, Blob] = {}
        # Paths a running prefetch is about to deliver (emptied as each lands).
        # Lets the thumbnail loader wait for the prefetch instead of starting an
        # expensive per-file read of the same entry.
        self._prefetching: set[str] = set()
        # Serialises the actual archive-library decode calls (_read_uncached /
        # _read_group_uncached). Some backends (py7zr) are not safe to use from
        # two threads at once, so a preview read and the whole-folder prefetch
        # must never decode concurrently, or the app can deadlock. This is a
        # separate lock from _read_lock (which only guards the cache/buffer) and
        # is never held together with it.
        self._io_lock = threading.Lock()
        # Staged, uncommitted edits: arcname -> new bytes (add/replace) or None
        # (delete). Empty for read-only backends; writable backends populate it
        # via write_bytes/add/remove/rename and commit it in save(). Reads and
        # the entry listing overlay it so the UI reflects unsaved changes.
        self._pending: dict[str, bytes | None] = {}

    # --------------------------------------------------------------- password
    @property
    def password(self) -> str | None:
        return self._password

    def set_password(self, password: str | None) -> None:
        self._password = password

    def _effective_password(self, explicit: str | None) -> str | None:
        return explicit if explicit is not None else self._password

    # ------------------------------------------------------------------ read
    @abstractmethod
    def info(self) -> ArchiveInfo:
        """Return metadata about the archive as a whole."""

    @abstractmethod
    def list_entries(self) -> Iterator[ArchiveEntry]:
        """Yield every entry. Consumers build the tree from the paths."""

    def list_effective_entries(self) -> list[ArchiveEntry]:
        """The current logical entries: on-disk entries with staged edits applied
        (adds/replaces added or updated, deletes removed). Read-only backends
        have no pending edits, so this is just the on-disk listing. The UI builds
        the tree from this so unsaved changes are visible before save()."""
        base = list(self.list_entries())
        if not self._pending:
            return base
        by_path: dict[str, ArchiveEntry] = {e.path: e for e in base}
        for path, data in self._pending.items():
            if data is None:
                by_path.pop(path, None)
                continue
            if path.endswith("/"):
                by_path[path] = ArchiveEntry(path=path, type=EntryType.DIRECTORY, size=0)
                continue
            existing = by_path.get(path)
            by_path[path] = ArchiveEntry(
                path=path,
                type=EntryType.FILE,
                size=len(data),
                compressed_size=None,
                modified=existing.modified if existing is not None else datetime.now(),
            )
        return list(by_path.values())

    # True for backends whose _read_uncached is safe to run on several threads
    # at once (zip: a reader per thread). Others are serialised by _io_lock.
    _parallel_reads = False

    def read_bytes(
        self,
        entry: ArchiveEntry,
        *,
        password: str | None = None,
        max_bytes: int | None = None,
        cache: bool = True,
    ) -> bytes:
        """Return the uncompressed content of one entry, in memory.

        ``max_bytes`` caps how much is read and materialised - pass it for a
        preview of a possibly huge entry so the whole thing is never pulled into
        memory. None means read the entire entry.

        Results are served from a small LRU cache so repeat reads are instant;
        the actual decoding happens in ``_read_uncached``. Pass ``cache=False``
        for one-off reads (thumbnails): caching those only filled RAM with
        bytes nobody reads again.
        """
        if entry.is_dir:
            return b""
        if self._closed:
            # A worker still busy for an archive that was closed or replaced:
            # do not start decoding it again.
            raise ArchiveError(f"Archive is closed: {self.path}")
        # Staged edits win over the on-disk content, so a preview shows unsaved
        # changes and reads never fall back to the (now stale) original.
        if entry.path in self._pending:
            staged = self._pending[entry.path]
            if staged is None:
                return b""
            return staged[:max_bytes] if max_bytes is not None else staged
        key = (entry.path, max_bytes)
        with self._read_lock:
            cached = self._read_cache.get(key)
            if cached is not None:
                self._read_cache.move_to_end(key)
                return cached
        buffered = self._from_folder(entry.path, max_bytes)
        if buffered is not None:
            return buffered
        guard = contextlib.nullcontext() if self._reads_freely(entry) else self._io_lock
        with guard:  # serial backends never decode on two threads at once
            # A folder prefetch may have delivered this entry while we waited
            # for the lock; serve that instead of decoding it a second time.
            buffered = self._from_folder(entry.path, max_bytes)
            if buffered is not None:
                return buffered
            data = self._read_uncached(entry, password=password, max_bytes=max_bytes)
        # With "Always use temporary files" nothing is kept in RAM beyond what
        # is on screen, so the read cache stays off.
        if cache and self._spill_mode != "disk":
            with self._read_lock:
                self._cache_put(key, data)
        return data

    def peek_bytes(self, entry: ArchiveEntry, *, max_bytes: int | None = None) -> bytes | None:
        """The entry's bytes when they are in memory already (a staged edit,
        the read cache or the folder buffer in RAM), else None. Never reads
        the archive, never waits for a running read or folder pass: cheap
        enough for the UI thread."""
        if entry.is_dir or self._closed:
            return None
        if entry.path in self._pending:
            staged = self._pending[entry.path] or b""
            return staged[:max_bytes] if max_bytes is not None else staged
        with self._read_lock:
            cached = self._read_cache.get((entry.path, max_bytes))
            if cached is None and max_bytes is not None:
                cached = self._read_cache.get((entry.path, None))
            data = self._folder_buffer.get(entry.path) if cached is None else cached
        if data is None:
            return None
        return data[:max_bytes] if max_bytes is not None else data

    def _from_folder(self, path: str, max_bytes: int | None) -> bytes | None:
        """The entry from the folder pass: RAM, or decrypted from the encrypted
        temp store. None when the pass does not hold it."""
        with self._read_lock:
            data = self._folder_buffer.get(path)
            blob = self._folder_spilled.get(path) if data is None else None
            store = self._spill_store
        if data is not None:
            return data[:max_bytes] if max_bytes is not None else data
        if blob is not None and store is not None:
            try:
                return store.read(blob, 0, max_bytes)
            except OSError:
                return None  # temp file gone (cleaned up): read from the archive
        return None

    def stream_entry(
        self,
        entry: ArchiveEntry,
        write: Callable[[bytes], None],
        *,
        on_progress: Callable[[int, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> bool:
        """Hand one entry's bytes to ``write`` in chunks, never holding the
        whole entry in memory (how a too-large preview reaches the encrypted
        temp store). Returns False when cancelled."""
        if entry.is_dir:
            return True
        if self._closed:
            raise ArchiveError(f"Archive is closed: {self.path}")
        total = max(int(entry.size or 0), 0)
        done = 0

        def sink(chunk: bytes) -> None:
            nonlocal done
            if is_cancelled is not None and is_cancelled():
                raise _StreamCancelled
            write(chunk)
            done += len(chunk)
            if on_progress is not None:
                on_progress(done, total)

        try:
            if entry.path in self._pending:
                staged = self._pending[entry.path]
                sink(staged or b"")
                return True
            with self._read_lock:
                data = self._folder_buffer.get(entry.path)
                blob = self._folder_spilled.get(entry.path)
                store = self._spill_store
            if data is not None:
                for start in range(0, len(data), _STREAM_CHUNK):
                    sink(data[start : start + _STREAM_CHUNK])
                return True
            if blob is not None and store is not None:
                with store.open(blob) as reader:
                    while chunk := reader.read(_STREAM_CHUNK):
                        sink(chunk)
                return True
            guard = contextlib.nullcontext() if self._reads_freely(entry) else self._io_lock
            with guard:
                self._stream_uncached(entry, sink)
            return True
        except _StreamCancelled:
            return False

    def _reads_freely(self, entry: ArchiveEntry) -> bool:
        """True when reading ``entry`` needs no turn on the shared decoder
        (``_io_lock``): it can run next to a folder pass or another read."""
        return self._parallel_reads

    def can_stream(self, entry: ArchiveEntry) -> bool:
        """Whether ``open_stream`` works for this entry (no I/O)."""
        return False

    def mixed_storage(self) -> tuple[int, int] | None:
        """(stored files, compressed files) when the archive mixes the two in
        a way that matters: a 7z backup made with Store to which files were
        later added with compression (7-Zip's drag and drop does that). The
        compressed part opens far slower. None otherwise (no I/O; known once
        the archive has been listed)."""
        return None

    def open_stream(self, entry: ArchiveEntry) -> SeekableReader | None:
        """A seekable, read-only file over the entry's content, read from the
        archive on demand, or None when the format cannot do that cheaply.

        Lets a video play (or a PDF open) while it is being read, instead of
        loading it whole into memory first. Close it when done."""
        return None

    def _stream_uncached(self, entry: ArchiveEntry, sink: Callable[[bytes], None]) -> None:
        """Feed the entry to ``sink`` in chunks. Default: one read (backends
        that can stream override this so a huge entry never sits in RAM)."""
        sink(self._read_uncached(entry))

    @abstractmethod
    def _read_uncached(
        self, entry: ArchiveEntry, *, password: str | None = None, max_bytes: int | None = None
    ) -> bytes:
        """Decode one entry's content in memory (no caching). Implemented by
        each backend; callers use ``read_bytes``."""

    def apply_limits(self) -> None:
        """Re-read the memory policy after a settings change (read cache size)."""
        with self._read_lock:
            self._read_cache_max_total = _read_cache_budget()
            while self._read_cache_bytes > self._read_cache_max_total and self._read_cache:
                _, evicted = self._read_cache.popitem(last=False)
                self._read_cache_bytes -= len(evicted)

    def _cache_put(self, key: tuple[str, int | None], data: bytes) -> None:
        if len(data) > _READ_CACHE_MAX_ENTRY:
            return  # do not cache very large entries (e.g. video)
        self._read_cache[key] = data
        self._read_cache.move_to_end(key)
        self._read_cache_bytes += len(data)
        while self._read_cache_bytes > self._read_cache_max_total and self._read_cache:
            _, evicted = self._read_cache.popitem(last=False)
            self._read_cache_bytes -= len(evicted)

    # ------------------------------------------------------- folder prefetch
    def begin_folder(self) -> int:
        """Start a new folder context: drop the previous folder buffer and bump
        the generation. Returns the generation to hand to ``read_group`` so a
        late prefetch for the old folder is discarded. Call on folder change."""
        with self._read_lock:
            self._folder_buffer.clear()
            self._prefetching.clear()
            spilled, self._folder_spilled = self._folder_spilled, {}
            self._folder_gen += 1
            generation = self._folder_gen
        self._drop_blobs(spilled.values())
        return generation

    def has_buffered(self, path: str) -> bool:
        """True if this entry's full bytes are already in the folder buffer, so a
        read is free. Used to decide whether an expensive-to-read entry (a video)
        is cheap enough to thumbnail."""
        with self._read_lock:
            return path in self._folder_buffer or path in self._folder_spilled

    def spilled_blob(self, path: str) -> Blob | None:
        """The encrypted temp blob holding this entry, if the folder pass put
        it on disk (lets a video/PDF preview read it without loading it)."""
        with self._read_lock:
            return self._folder_spilled.get(path)

    def configure_spill(self, store: EncryptedTempStore | None, mode: str) -> None:
        """Where the folder pass keeps files: "memory" (RAM only, the default),
        "overflow" (RAM first, the rest encrypted on disk) or "disk" (always
        encrypted on disk). Applies from the next folder pass."""
        self._spill_store = store
        on_disk = store is not None and mode in ("overflow", "disk")
        self._spill_mode = mode if on_disk else "memory"
        if self._spill_mode == "disk":
            with self._read_lock:  # drop what the read cache held in RAM
                self._read_cache.clear()
                self._read_cache_bytes = 0

    @property
    def spill_mode(self) -> str:
        return self._spill_mode

    @property
    def spill_store(self) -> EncryptedTempStore | None:
        return self._spill_store

    def _drop_blobs(self, blobs: Iterable[Blob]) -> None:
        store = self._spill_store
        if store is None:
            return
        for blob in blobs:
            store.delete(blob)

    def is_prefetching(self, path: str) -> bool:
        """True while a running folder prefetch is still going to deliver this
        entry. A caller that wants it cheaply should wait for the prefetch."""
        with self._read_lock:
            return path in self._prefetching

    def group_progress(self) -> tuple[int, int] | None:
        """(bytes done, bytes total) of a read_group() decode that is running,
        for backends that can tell (a solid 7z: the compressed bytes read of
        the blocks it decodes); None otherwise. Safe to call from any thread."""
        return None

    def read_group(
        self,
        entries: Iterable[ArchiveEntry],
        generation: int,
        *,
        password: str | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        observe: Callable[[ArchiveEntry], bool] | None = None,
        observer: Callable[[ArchiveEntry, bytes], None] | None = None,
    ) -> int:
        """Decode a folder's files in one pass into the folder buffer.

        Runs on a background thread when a folder is opened in a thumbnail
        view, or on the first file opened in it. Files up to the per-entry cap
        and within the RAM budget are kept in the buffer (in the given order);
        the rest stay on-demand. Entries for which ``observe(entry)`` is true
        are decoded in the same pass even when they do not fit the budget, and
        every decoded one of them is handed to ``observer(entry, data)`` (on the
        decode thread; it may block to apply backpressure). That is how the
        thumbnails for a whole folder get made during the single pass without
        having to keep every file in memory.

        Each entry lands in the buffer the moment it is decoded, and
        ``on_progress(done, total)`` follows right after (total is the number
        of files this pass decodes). Backends that cannot do a cheaper-than-
        per-file group read leave ``_read_group_uncached`` returning ``{}``, so
        this is a no-op for them (zip is already fast per file). Returns how
        many entries were buffered. Best-effort: a failure just means no (or a
        partial) buffer.
        """
        entries = [e for e in entries if not e.is_dir]
        total = len(entries)
        budget = _prefetch_budget()
        store = self._spill_store
        mode = self._spill_mode if store is not None else "memory"
        keep: set[str] = set()
        decode: list[ArchiveEntry] = []
        running = 0
        for entry in entries:
            size = int(entry.size or 0)
            if size <= 0 or size > _PREFETCH_MAX_ENTRY:
                continue
            with self._read_lock:
                if entry.path in self._folder_buffer or entry.path in self._folder_spilled:
                    continue
            if mode != "memory" or running + size <= budget:
                # With temp files on, everything is kept: in RAM while it fits
                # (overflow) and encrypted on disk otherwise (or always: disk).
                keep.add(entry.path)
                running += size
                decode.append(entry)
            elif observe is not None and observe(entry):
                decode.append(entry)  # thumbnail only; not kept in memory
        if not decode:
            if on_progress is not None and total:
                on_progress(total, total)
            return 0

        want = len(decode)
        by_path = {e.path: e for e in decode}
        stored = 0
        done = 0
        in_ram = 0  # bytes kept in RAM by this pass (overflow mode)
        with self._read_lock:
            if generation != self._folder_gen:
                return 0
            self._prefetching = set(by_path)

        def on_item(path: str, data: bytes) -> None:
            nonlocal stored, done, in_ram
            entry = by_path.get(path)
            # Where to keep it. Live check: other apps (or a video preview) may
            # have taken memory since the budget was set; past the reserve RAM
            # is not used - the file goes to the encrypted temp store when temp
            # files are on, or is not kept (thumbnails are still made from it,
            # previews then read on demand).
            target = None
            if path in keep:
                size = len(data)
                ram_ok = memory.fits(size)
                if mode == "disk":
                    target = "disk"
                elif mode == "overflow":
                    target = "ram" if ram_ok and in_ram + size <= budget else "disk"
                elif ram_ok:
                    target = "ram"
                if target == "disk" and store.free_disk() - size < disk_reserve():
                    target = None  # the disk is nearly full: never fill it up
            blob = None
            if target == "disk":
                try:
                    blob = store.put(data)
                except OSError:
                    blob = None  # disk trouble: just do not keep it
            with self._read_lock:
                current = generation == self._folder_gen and path in self._prefetching
                if current:
                    self._prefetching.discard(path)
                    if blob is not None:
                        self._folder_spilled[path] = blob
                        stored += 1
                    elif target == "ram" and path not in self._folder_buffer:
                        self._folder_buffer[path] = data
                        in_ram += len(data)
                        stored += 1
                    done += 1
                count = done
            if not current:
                # Navigated away, or already delivered: drop what we wrote.
                if blob is not None:
                    store.delete(blob)
                return
            # Observer before progress, so a caller counting "finished" work
            # never sees this file as done before its follow-up was queued.
            if observer is not None and entry is not None and observe is not None:
                with contextlib.suppress(Exception):  # thumbnails are best-effort
                    if observe(entry):
                        observer(entry, data)
            if on_progress is not None:
                with contextlib.suppress(Exception):  # progress is best-effort
                    on_progress(count, want)

        def cancelled() -> bool:
            # Another folder was opened, or the archive was closed.
            return self._closed or generation != self._folder_gen

        try:
            with self._io_lock:  # serialise with on-demand reads (same library)
                leftover = self._read_group_uncached(
                    decode, password=password, on_item=on_item, is_cancelled=cancelled
                )
            for path, data in leftover.items():
                on_item(path, data)
        except Exception:  # noqa: BLE001 - prefetch is best-effort
            pass
        finally:
            with self._read_lock:
                if generation == self._folder_gen:
                    self._prefetching.clear()
        if on_progress is not None and not cancelled():
            with contextlib.suppress(Exception):
                on_progress(want, want)
        return stored

    def _read_group_uncached(
        self,
        entries: list[ArchiveEntry],
        *,
        password: str | None = None,
        on_item: Callable[[str, bytes], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, bytes]:
        """Decode several entries in one pass.

        Backends where a single pass beats per-file reads (solid 7z, a
        compressed tar stream) override this and hand each entry to
        ``on_item(path, data)`` as soon as it is decoded; anything they could
        not hand over that way may be returned as a path -> bytes dict instead.
        They stop early once ``is_cancelled()`` is true (the user left the
        folder or closed the archive), so a long pass never keeps the next
        folder or archive waiting. Default: no group optimization (return
        empty), so ``read_group`` is a no-op.
        """
        return {}

    # -------------------------------------------------------------- extract
    def extract(
        self,
        entries: Iterable[ArchiveEntry],
        dest_dir: Path | str,
        *,
        on_progress: Callable[[int, int, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> ExtractResult:
        """Stream the given file entries to ``dest_dir`` preserving structure.

        The second allowed disk write (besides saving an edited archive). Never
        holds a whole entry in memory. Backends that cannot extract raise.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support extraction")

    @property
    def fast_seek(self) -> bool:
        """True when reading one entry is cheap random access (seek-based, like
        zip), so a small read may run synchronously on the UI thread. False for
        formats where a single read can decode a large solid block (7z) or must
        stream sequentially (libarchive) - those always read off the UI thread so
        the window never freezes."""
        return False

    # ----------------------------------------------------------------- write
    @property
    def writable(self) -> bool:
        return False

    @property
    def has_unsaved_changes(self) -> bool:
        return False

    def write_bytes(self, arcname: str, data: bytes) -> None:
        """Stage in-memory content for ``arcname`` (add or replace).

        Committed by ``save()``. Reading never writes to disk; only ``save``
        touches it, via the strategy the backend chooses.
        """
        raise ReadOnlyArchiveError(f"{type(self).__name__} is read-only")

    def add(self, source: Path | str, arcname: str) -> None:
        raise ReadOnlyArchiveError(f"{type(self).__name__} is read-only")

    def add_dir(self, arcname: str) -> None:
        """Stage an (empty) directory to be created at ``arcname``."""
        raise ReadOnlyArchiveError(f"{type(self).__name__} is read-only")

    def remove(self, arcname: str) -> None:
        raise ReadOnlyArchiveError(f"{type(self).__name__} is read-only")

    def rename(self, old_arcname: str, new_arcname: str) -> None:
        raise ReadOnlyArchiveError(f"{type(self).__name__} is read-only")

    def save(self) -> None:
        """Commit staged changes via a temp file plus atomic replace."""
        raise ReadOnlyArchiveError(f"{type(self).__name__} is read-only")

    def discard_changes(self) -> None:
        """Drop all staged, uncommitted edits without touching the archive."""
        self._pending.clear()

    def _invalidate_caches(self) -> None:
        """Drop the read LRU and folder buffer, e.g. after a save changed entry
        content, so the next read returns fresh bytes instead of stale cache."""
        with self._read_lock:
            self._read_cache.clear()
            self._read_cache_bytes = 0
            self._folder_buffer.clear()
            spilled, self._folder_spilled = self._folder_spilled, {}
        self._drop_blobs(spilled.values())

    # ------------------------------------------------------------- lifecycle
    def close(self) -> None:  # noqa: B027
        """Release any held resources. Override if needed (call super).
        Also stops a folder pass that is still running for this archive."""
        with self._read_lock:
            self._closed = True
            self._folder_gen += 1  # a running pass sees this and stops
            self._folder_buffer.clear()
            spilled, self._folder_spilled = self._folder_spilled, {}
        self._drop_blobs(spilled.values())

    def __enter__(self) -> ArchiveBackend:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
