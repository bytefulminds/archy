from __future__ import annotations

import contextlib
import dataclasses
import io
import os
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from archy.core.backend import ArchiveBackend, ArchiveEntry, ArchiveInfo, EntryType
from archy.core.backends.sevenzip_direct import (
    Span,
    SpanStream,
    StoreIndex,
    copy_to,
    read_span,
    read_whole,
)
from archy.core.errors import ArchiveError, EncryptedArchiveError
from archy.i18n import tr

# Read + write for 7z via py7zr. Reading never writes to disk: a single entry is
# extracted into an in-memory BytesIO (py7zr's BytesIOFactory) and returned as
# bytes. Writing is staged and committed by save() via a temp file + atomic
# replace.


class _PassCancelled(Exception):
    """Raised from inside py7zr's write to abort a folder pass."""


def _make_delivering_factory(on_complete, is_cancelled=None):  # noqa: ANN001, ANN202
    """A py7zr WriterFactory whose per-member buffers hand their bytes to
    ``on_complete(filename, data)`` the moment py7zr finishes writing that
    member (py7zr calls ``Py7zIO.close()`` right then, on its decode thread).
    The buffer is released immediately afterwards, so a whole-folder pass never
    holds more than the member being decoded plus what the caller keeps. Built
    lazily so the module needs no py7zr import to load."""
    from py7zr.io import Py7zIO, WriterFactory

    class _DeliveringIO(Py7zIO):
        def __init__(self, filename: str) -> None:
            self.filename = filename
            self._buffer = io.BytesIO()
            self.delivered = False

        def write(self, s):  # noqa: ANN001, ANN202
            if is_cancelled is not None and is_cancelled():
                raise _PassCancelled  # aborts py7zr's extract right here
            return self._buffer.write(s)

        def read(self, size=None):  # noqa: ANN001, ANN202
            return self._buffer.read(size)

        def seek(self, offset, whence=0):  # noqa: ANN001, ANN202
            return self._buffer.seek(offset, whence)

        def flush(self) -> None:
            self._buffer.flush()

        def size(self) -> int:
            return self._buffer.getbuffer().nbytes

        def take(self) -> bytes:
            data = self._buffer.getvalue()
            self._buffer = io.BytesIO()
            self.delivered = True
            return data

        def close(self) -> None:
            if not self.delivered:
                on_complete(self.filename, self.take())

    class _Factory(WriterFactory):
        def __init__(self) -> None:
            self.products: dict[str, _DeliveringIO] = {}

        def create(self, filename: str) -> Py7zIO:
            product = _DeliveringIO(filename)
            self.products[filename] = product
            return product

    return _Factory()


class _Exit:
    """Finish an entered context manager with whatever happens in the block
    (so errors inside still reach it)."""

    def __init__(self, context) -> None:  # noqa: ANN001
        self._context = context

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc):  # noqa: ANN002, ANN204
        return self._context.__exit__(*exc)


def _open_7z(path: object, password: str | None):  # noqa: ANN202 - py7zr.SevenZipFile
    """Open a 7z for reading, the way Archy needs it.

    Handing py7zr an open file (instead of a path) makes it extract blocks one
    after another. Given a path, py7zr decodes EVERY block of an unencrypted
    archive at the same time, one thread each: a non-solid archive (a block per
    file) then ran thousands of decoders at once - over 1500 threads and 1.3 GB
    of RAM for a 670 MB test archive, and a MemoryError on a 46 GB backup.
    One at a time is just as fast (the disk is the limit) and uses a fixed,
    small amount of memory. The file is closed together with the archive.
    """
    import py7zr

    _limit_decode_chunks()
    fh = open(path, "rb")  # noqa: SIM115 - closed by the archive's close()
    try:
        archive = py7zr.SevenZipFile(fh, mode="r", password=password)
    except BaseException:
        fh.close()
        raise
    original_close = archive.close

    def close() -> None:
        try:
            original_close()
        finally:
            fh.close()

    archive.close = close
    return archive


_DECODE_CHUNK = 16 * 1024 * 1024
_chunks_limited = False


def _limit_decode_chunks() -> None:
    """Keep py7zr's memory use small and flat while it decodes.

    1. Chunk size: py7zr sizes its buffers from free RAM, up to 128 MB per
       chunk (on Windows always 128 MB), and briefly holds a few of them per
       file. 16 MB keeps the same speed with a small, fixed footprint.
    2. A leak: py7zr keeps the decompressor of every block it has decoded
       (each with its LZMA dictionary, up to the block's dictionary size) until
       the archive is closed. Memory then grows with every block; on a 46 GB
       backup with hundreds of blocks that ends in a MemoryError. A block is
       finished when py7zr's per-block extract returns, so its decompressor is
       released right there.
    """
    global _chunks_limited
    if _chunks_limited:
        return
    with contextlib.suppress(Exception):  # an optimisation only
        import py7zr.py7zr as core

        original = core.get_memory_limit
        core.get_memory_limit = lambda: max(1024 * 1024, min(_DECODE_CHUNK, original()))
    with contextlib.suppress(Exception):  # an optimisation only
        import py7zr.py7zr as core

        extract_single = core.Worker.extract_single

        def extract_single_and_release(self, fp, files, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
            try:
                return extract_single(self, fp, files, *args, **kwargs)
            finally:
                for member in files or ():
                    folder = getattr(member, "folder", None)
                    if folder is not None:
                        folder.decompressor = None

        core.Worker.extract_single = extract_single_and_release
    _chunks_limited = True


# py7zr refuses archives with more than 65,536 packed streams, a guard against
# malicious headers. A non-solid archive has a stream per file, so a real
# backup of 100,000+ files was rejected as "not a valid 7z". A stream needs at
# least one byte of the archive, so the archive size bounds the count anyway;
# 16 million streams leaves room for any real backup while keeping the guard.
_MAX_STREAMS = 16 * 1024 * 1024
_streams_raised = False


def _raise_stream_limit() -> None:
    global _streams_raised
    if _streams_raised:
        return
    with contextlib.suppress(Exception):
        import py7zr.archiveinfo as archiveinfo

        archiveinfo.MAX_NUMSTREAMS = max(archiveinfo.MAX_NUMSTREAMS, _MAX_STREAMS)
    _streams_raised = True


_key_cache_installed = False


def _install_key_cache() -> None:
    """Derive each 7z AES key once instead of once per block.

    7z derives the AES key from the password with 2^19 SHA-256 rounds. 7-Zip
    itself remembers the result; py7zr redoes it for every block it opens.
    In a non-solid encrypted archive every file is its own block, so that cost
    (about a quarter second in Python) was paid for every single file - a
    folder of 1500 photos took minutes. The inputs (password, rounds, salt) are
    the same for every block, so a small in-memory cache makes it one-off. The
    derived key never leaves memory; the password is held there anyway.
    """
    global _key_cache_installed
    if _key_cache_installed:
        return
    try:
        import functools

        import py7zr.compressor as compressor

        original = compressor.calculate_key
        compressor.calculate_key = functools.lru_cache(maxsize=16)(original)
    except Exception:  # noqa: BLE001, S110 - an optimisation only
        pass
    _key_cache_installed = True


def _seven_type(info: object) -> EntryType:
    if getattr(info, "is_directory", False):
        return EntryType.DIRECTORY
    if getattr(info, "is_symlink", False):
        return EntryType.SYMLINK
    return EntryType.FILE


def _seven_mtime(info: object) -> datetime | None:
    raw = getattr(info, "creationtime", None)
    return raw if isinstance(raw, datetime) else None


class SevenZipBackend(ArchiveBackend):
    def __init__(self, path: object) -> None:
        # The base class sets up the staged-edits map (_pending) and the caches.
        super().__init__(path)  # type: ignore[arg-type]
        _install_key_cache()
        _raise_stream_limit()
        # One parsed archive reused for reads (always under the base class's
        # _io_lock). Opening means parsing (and for an encrypted header,
        # decrypting) the whole index: 100,000 files took ~2 s per open, which
        # every single preview or thumbnail paid again.
        self._shared = None
        self._shared_password: str | None = None
        # Renames are staged as a name map (new name -> name in the archive),
        # not as bytes: renaming a folder of large videos must not read them
        # all into memory. The data is copied under the new name on save.
        self._moved: dict[str, str] = {}
        self._profile = None  # EditProfile, probed on first use
        self._disk: dict[str, ArchiveEntry] | None = None  # path -> on-disk entry
        # Stored (uncompressed) files are read straight from the archive, at
        # any offset, without py7zr (see sevenzip_direct.py). Built with the
        # listing; None until then, or when the index could not be read.
        self._direct: StoreIndex | None = None
        self._verified: str | None = None  # password a direct read proved right
        self._verify_lock = threading.Lock()

    @contextlib.contextmanager
    def _shared_archive(self, password: str | None):  # noqa: ANN202
        """The reusable, rewound archive for one read. Call with _io_lock
        held. Dropped (and reopened next time) after any error, after a save
        and on close."""
        archive = self._shared
        if archive is None or self._shared_password != password:
            self._drop_shared()
            archive = _open_7z(self.path, password)
            self._shared, self._shared_password = archive, password
        else:
            archive.reset()
        try:
            yield archive
        except BaseException:
            self._drop_shared()
            raise

    def _drop_shared(self) -> None:
        archive, self._shared = self._shared, None
        if archive is not None:
            with contextlib.suppress(Exception):
                archive.close()

    def close(self) -> None:
        self._drop_shared()
        super().close()

    def _invalidate_caches(self) -> None:
        self._drop_shared()  # the file was rewritten by a save
        self._profile = None
        self._disk = None
        self._direct = None
        super()._invalidate_caches()

    @property
    def writable(self) -> bool:
        return True

    @property
    def has_unsaved_changes(self) -> bool:
        return bool(self._pending or self._moved)

    def discard_changes(self) -> None:
        super().discard_changes()
        self._moved.clear()

    def set_password(self, password: str | None) -> None:
        super().set_password(password)
        self._profile = None  # whether the names are encrypted depends on it
        self._disk = None
        self._direct = None

    # ---------------------------------------------------------- edit profile
    def edit_profile(self):  # noqa: ANN201 - EditProfile
        """How this archive was made (stored or compressed, encrypted names,
        solid), which decides whether and how cheaply it can be rewritten."""
        from archy.core.backends.sevenzip_writer import probe_profile

        if self._profile is None:
            with self._io_lock, self._shared_archive(self._password) as archive:
                self._profile = probe_profile(archive, self.path, self._password)
        return self._profile

    def _disk_entries(self) -> dict[str, ArchiveEntry]:
        if self._disk is None:
            self._disk = {e.path: e for e in self._list_fresh()}
        return self._disk

    def _source_path(self, path: str) -> str:
        """Name in the archive of an entry shown under ``path`` (renames)."""
        return self._moved.get(path, path)

    # --------------------------------------------------------- direct reads
    def _span(self, entry: ArchiveEntry) -> Span | None:
        index = self._direct
        if index is None or entry.is_dir or entry.path in self._pending:
            return None
        return index.span(self._source_path(entry.path))

    def _reads_freely(self, entry: ArchiveEntry) -> bool:
        # A direct read uses its own file handle: it never waits for a folder
        # pass or another read.
        return self._span(entry) is not None

    def _verify_password(self, password: str | None) -> None:
        """Before a partial read or a stream of an encrypted file (no checksum
        to catch a wrong password), prove the password once by reading the
        smallest encrypted file whole and checking its CRC."""
        if password is None or password == self._verified or self._direct is None:
            return
        with self._verify_lock:
            if password == self._verified:
                return
            smallest = self._direct.smallest_encrypted()
            if smallest is not None:
                read_whole(self.path, smallest[1], password)  # raises when wrong
            self._verified = password

    def _read_direct(self, span: Span, password: str | None, max_bytes: int | None) -> bytes:
        if max_bytes is None or max_bytes >= span.size:
            data = read_whole(self.path, span, password)
            if span.aes is not None:
                self._verified = password
            return data
        if span.aes is not None:
            self._verify_password(password)
        with open(self.path, "rb") as fh:
            return read_span(fh, span, password, 0, max_bytes)

    def can_stream(self, entry: ArchiveEntry) -> bool:
        return self._span(entry) is not None

    def open_stream(self, entry: ArchiveEntry) -> SpanStream | None:
        span = self._span(entry)
        if span is None or self._closed:
            return None
        password = self._effective_password(None)
        if span.aes is not None:
            if password is None:
                raise EncryptedArchiveError(f"Entry is encrypted: {entry.path}")
            self._verify_password(password)
        return SpanStream(self.path, span, password)

    # ------------------------------------------------------------------ read
    def info(self) -> ArchiveInfo:
        count = sum(1 for _ in self.list_entries())
        return ArchiveInfo(path=self.path, format="7z", writable=True, entry_count=count)

    def mixed_storage(self) -> tuple[int, int] | None:
        index = self._direct
        if index is None or not len(index) or not index.compressed_files:
            return None
        return len(index), index.compressed_files

    def list_entries(self) -> Iterator[ArchiveEntry]:
        # The listing is kept until the archive changes (save) or the password
        # does: re-listing 100,000 entries after every staged edit froze the UI.
        yield from self._disk_entries().values()

    def _list_fresh(self) -> Iterator[ArchiveEntry]:
        import py7zr

        try:
            # Parsed once and kept for the reads that follow (see _shared_archive).
            with self._io_lock, self._shared_archive(self._password) as archive:
                infos = archive.list()
                shared: set[str] = set()
                with contextlib.suppress(Exception):  # only for the size column
                    for member in archive.files:
                        folder = getattr(member, "folder", None)
                        if folder is not None and len(getattr(folder, "files", None) or ()) > 1:
                            shared.add(str(member.filename))
                try:
                    self._direct = StoreIndex.build(archive)
                except Exception:  # noqa: BLE001 - direct reads are an optimisation
                    self._direct = None
        except py7zr.Bad7zFile as exc:
            raise ArchiveError(f"Not a valid 7z archive: {self.path}: {exc}") from exc
        except py7zr.PasswordRequired as exc:
            raise EncryptedArchiveError(
                f"7z archive has an encrypted header and needs a password: {self.path}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            # With "Encrypt file names" a wrong password decrypts the index into
            # garbage, which surfaces as a decoder error (e.g. LZMAError). Report
            # it as a wrong password so the user is asked again.
            if self._password is not None:
                raise EncryptedArchiveError(
                    f"Wrong password for the encrypted 7z index: {self.path}"
                ) from exc
            raise ArchiveError(f"Could not read 7z archive: {self.path}: {exc}") from exc

        for fi in infos:
            etype = _seven_type(fi)
            compressed = getattr(fi, "compressed", None)
            if str(fi.filename) in shared:
                # In a solid block the archive only knows the packed size of
                # the whole block; py7zr reports it on the block's first file
                # (a 1.6 MB "compressed size" for a 140 KB photo). No figure
                # is better than a wrong one.
                compressed = None
            yield ArchiveEntry(
                path=fi.filename.replace("\\", "/"),
                type=etype,
                size=int(getattr(fi, "uncompressed", 0) or 0),
                compressed_size=compressed,
                modified=_seven_mtime(fi),
            )

    def list_effective_entries(self) -> list[ArchiveEntry]:
        entries = super().list_effective_entries()
        if not self._moved:
            return entries
        disk = self._disk_entries()
        shown = {e.path for e in entries}
        for new, old in self._moved.items():
            original = disk.get(old)
            if original is not None and new not in shown and new not in self._pending:
                entries.append(dataclasses.replace(original, path=new))
        return entries

    def _read_uncached(
        self, entry: ArchiveEntry, *, password: str | None = None, max_bytes: int | None = None
    ) -> bytes:
        if entry.is_dir:
            return b""
        if not entry.size and entry.path not in self._moved:
            return b""
        span = self._span(entry)
        if span is not None:
            return self._read_direct(span, self._effective_password(password), max_bytes)
        source = self._source_path(entry.path)

        import py7zr
        from py7zr.io import BytesIOFactory

        # py7zr extracts a whole entry at once, so max_bytes only trims the
        # result here (7z cannot cheaply stream a prefix like zip/libarchive).
        limit = max(int(entry.size or 0), 1)
        pw = self._effective_password(password)
        try:
            shared = self._shared_archive(pw)
            archive = shared.__enter__()
        except py7zr.PasswordRequired as exc:
            raise EncryptedArchiveError(
                f"Entry is encrypted or the password is wrong: {entry.path}"
            ) from exc
        except py7zr.Bad7zFile as exc:
            raise ArchiveError(f"Not a valid 7z archive: {self.path}: {exc}") from exc

        with _Exit(shared):
            try:
                encrypted = bool(archive.needs_password())
            except Exception:  # noqa: BLE001 - fall back to "password was supplied"
                encrypted = pw is not None
            try:
                factory = BytesIOFactory(limit)
                archive.extract(targets=[source], factory=factory)
                product = factory.products.get(source)
                if product is None:
                    raise ArchiveError(f"Entry not found in archive: {entry.path}")
                data = product.read()
                return data[:max_bytes] if max_bytes is not None else data
            except ArchiveError:
                raise
            except (py7zr.PasswordRequired, py7zr.exceptions.CrcError) as exc:
                raise EncryptedArchiveError(
                    f"Entry is encrypted or the password is wrong: {entry.path}"
                ) from exc
            except py7zr.Bad7zFile as exc:
                raise ArchiveError(f"Not a valid 7z archive: {self.path}: {exc}") from exc
            except Exception as exc:  # noqa: BLE001
                # A wrong password usually corrupts the stream (e.g. LZMAError);
                # map that to an encryption error only when the archive is
                # actually encrypted, so genuine corruption still surfaces.
                if encrypted:
                    raise EncryptedArchiveError(
                        f"Entry is encrypted or the password is wrong: {entry.path}"
                    ) from exc
                raise ArchiveError(f"Could not read entry {entry.path}: {exc}") from exc

    def _read_group_uncached(self, entries, *, password=None, on_item=None, is_cancelled=None):  # noqa: ANN001, ANN201, E501
        # One pass over the solid block(s): py7zr decodes each needed block once
        # and hands back every requested target, instead of restarting from the
        # block start per file. This is the whole point of the folder prefetch.
        # Each member is handed over as soon as it is decoded (see
        # _make_delivering_factory), after checking its CRC ourselves: py7zr
        # only verifies it after handing the data over, and a wrong password
        # must never put garbage in the buffer.
        import zlib

        pw = self._effective_password(password)
        # Stored files: read directly, one after another in archive order, so
        # the disk reads front to back. No py7zr, and no lock that on-demand
        # reads (a click) would have to wait for.
        direct = [(e, span) for e in entries if (span := self._span(e)) is not None]
        direct.sort(key=lambda item: (item[1].start, item[1].offset))
        for entry, span in direct:
            if is_cancelled is not None and is_cancelled():
                return {}
            try:
                data = self._read_direct(span, pw, None)
            except Exception:  # noqa: BLE001 - prefetch is best-effort
                continue
            if on_item is not None:
                on_item(entry.path, data)
        done = {e.path for e, _ in direct}
        entries = [e for e in entries if e.path not in done]
        if not entries:
            return {}
        shown_as = {self._source_path(e.path): e.path for e in entries}  # source -> shown
        wanted = set(shown_as)
        out: dict[str, bytes] = {}
        crcs: dict[str, int] = {}

        def deliver(filename: str, data: bytes) -> None:
            source = filename.replace("\\", "/")
            if source not in wanted:
                return
            path = shown_as[source]
            expected = crcs.get(filename)
            if expected is not None and zlib.crc32(data) != expected:
                return  # corrupt / wrong password: drop, reads stay on-demand
            if on_item is not None:
                on_item(path, data)
            else:
                out[path] = data

        factory = _make_delivering_factory(deliver, is_cancelled)
        try:
            shared = self._shared_archive(pw)
            archive = shared.__enter__()
        except Exception:  # noqa: BLE001 - prefetch is best-effort
            return out
        with _Exit(shared):
            with contextlib.suppress(Exception):  # CRCs are an extra safety net
                for info in archive.files:
                    crc = getattr(info, "crc32", None)
                    if crc is not None:
                        crcs[str(info.filename)] = int(crc)
            try:
                archive.extract(targets=list(wanted), factory=factory)
            except Exception:  # noqa: BLE001 - cancelled, or broken: keep what we have
                return out
        # Older py7zr releases do not call Py7zIO.close(); hand over whatever
        # was not delivered during the pass now (CRC-checked by py7zr already).
        for filename, product in factory.products.items():
            if not product.delivered:
                with contextlib.suppress(Exception):
                    deliver(filename, product.take())
        return out

    def _stream_uncached(self, entry: ArchiveEntry, sink) -> None:  # noqa: ANN001
        # py7zr writes the decoded member in chunks into the IO its factory
        # hands out; this one forwards each chunk straight to the sink, so a
        # multi-GB video goes to the encrypted temp store without ever being
        # held in memory. Cancelling raises out of write(), which aborts py7zr.
        span = self._span(entry)
        if span is not None:
            copy_to(self.path, span, self._effective_password(None), sink)
            return

        import py7zr
        from py7zr.io import Py7zIO, WriterFactory

        class _SinkIO(Py7zIO):
            def __init__(self) -> None:
                self._size = 0

            def write(self, s):  # noqa: ANN001, ANN202
                sink(bytes(s))
                self._size += len(s)
                return len(s)

            def read(self, size=None) -> bytes:  # noqa: ANN001
                return b""

            def seek(self, offset, whence=0) -> int:  # noqa: ANN001
                return 0

            def flush(self) -> None:
                pass

            def size(self) -> int:
                return self._size

        class _Factory(WriterFactory):
            def create(self, filename: str) -> Py7zIO:
                return _SinkIO()

        pw = self._effective_password(None)
        try:
            with self._shared_archive(pw) as archive:
                archive.extract(targets=[self._source_path(entry.path)], factory=_Factory())
        except (py7zr.PasswordRequired, py7zr.exceptions.CrcError) as exc:
            raise EncryptedArchiveError(
                f"Entry is encrypted or the password is wrong: {entry.path}"
            ) from exc
        except py7zr.Bad7zFile as exc:
            raise ArchiveError(f"Not a valid 7z archive: {self.path}: {exc}") from exc

    # -------------------------------------------------------------- extract
    def extract(self, entries, dest_dir, *, on_progress=None, is_cancelled=None):  # noqa: ANN001, ANN201
        # py7zr hands every decoded member to an IO object from our factory, so
        # Archy writes the files itself: that gives real progress while a big
        # solid block is decoded (py7zr's own extract reported nothing until
        # the very end), a Cancel that works, a zip-slip-safe path for every
        # file, a CRC check before a file is kept, and an honest count of what
        # actually landed on disk. Files are written as "<name>.archy-part" and
        # renamed when complete, so an interrupted extraction never leaves a
        # file that looks whole but is not. One pass over each solid block.
        import threading
        import zlib
        from pathlib import Path

        import py7zr
        from py7zr.io import Py7zIO, WriterFactory

        from archy.core.extraction import ExtractResult, safe_join

        dest_dir = Path(dest_dir)
        result = ExtractResult()
        files = [e for e in entries if not e.is_dir]
        total = sum(max(int(e.size or 0), 0) for e in files)
        wanted: dict[str, Path] = {}
        for entry in files:
            dest = safe_join(dest_dir, entry.path)
            if dest is None:
                result.failed.append((entry.path, "onveilig pad overgeslagen"))
            else:
                wanted[entry.path] = dest
        if not wanted:
            return result
        if is_cancelled is not None and is_cancelled():
            result.cancelled = True
            return result
        dest_dir.mkdir(parents=True, exist_ok=True)

        lock = threading.Lock()
        progress = {"done": 0}
        completed: set[str] = set()
        open_parts: set[Path] = set()
        crcs: dict[str, int] = {}

        class _Cancelled(Exception):
            pass

        class _FileIO(Py7zIO):
            def __init__(self, path: str, dest: Path) -> None:
                self.path = path
                self.dest = dest
                self.part = dest.with_name(dest.name + ".archy-part")
                self.fh = None
                self.crc = 0
                self.written = 0

            def _ensure_open(self) -> None:
                if self.fh is None:
                    self.dest.parent.mkdir(parents=True, exist_ok=True)
                    self.fh = open(self.part, "wb")  # noqa: SIM115 - closed in close()
                    with lock:
                        open_parts.add(self.part)

            def write(self, s):  # noqa: ANN001, ANN202
                if is_cancelled is not None and is_cancelled():
                    raise _Cancelled
                self._ensure_open()
                self.fh.write(s)
                self.crc = zlib.crc32(s, self.crc)
                self.written += len(s)
                with lock:
                    progress["done"] += len(s)
                    done = progress["done"]
                if on_progress is not None:
                    on_progress(done, total, self.path)
                return len(s)

            def read(self, size=None) -> bytes:  # noqa: ANN001
                return b""

            def seek(self, offset, whence=0) -> int:  # noqa: ANN001
                return 0

            def flush(self) -> None:
                if self.fh is not None:
                    self.fh.flush()

            def size(self) -> int:
                return self.written

            def close(self) -> None:  # py7zr: this member is complete
                self._ensure_open()  # an empty file still gets created
                self.fh.close()
                expected = crcs.get(self.path)
                with lock:
                    open_parts.discard(self.part)
                if expected is not None and self.written and self.crc != expected:
                    self.part.unlink(missing_ok=True)
                    raise py7zr.exceptions.CrcError(self.crc, expected, self.path)
                os.replace(self.part, self.dest)
                with lock:
                    completed.add(self.path)

        class _Discard(Py7zIO):
            def write(self, s):  # noqa: ANN001, ANN202
                return len(s)

            def read(self, size=None) -> bytes:  # noqa: ANN001
                return b""

            def seek(self, offset, whence=0) -> int:  # noqa: ANN001
                return 0

            def flush(self) -> None:
                pass

            def size(self) -> int:
                return 0

        class _Factory(WriterFactory):
            def create(self, filename: str) -> Py7zIO:
                path = filename.replace("\\", "/")
                dest = wanted.get(path)
                return _FileIO(path, dest) if dest is not None else _Discard()

        reason = None
        try:
            with _open_7z(self.path, self._effective_password(None)) as archive:
                # Target the archive's own spelling of each name (it may use
                # backslashes), and note the CRCs to check every file.
                targets = []
                for info in archive.files:
                    name = str(info.filename)
                    path = name.replace("\\", "/")
                    if path in wanted:
                        targets.append(name)
                        crc = getattr(info, "crc32", None)
                        if crc is not None:
                            crcs[path] = int(crc)
                archive.extract(targets=targets, factory=_Factory())
        except _Cancelled:
            result.cancelled = True
        except (py7zr.PasswordRequired, py7zr.exceptions.CrcError):
            reason = "versleuteld, fout wachtwoord of beschadigd"
        except Exception as exc:  # noqa: BLE001 - report, keep what was written
            reason = str(exc) or type(exc).__name__
        finally:
            # A file that was being written when it stopped is not kept.
            with lock:
                leftovers = list(open_parts)
            for part in leftovers:
                with contextlib.suppress(OSError):
                    part.unlink(missing_ok=True)

        result.extracted = len(completed)
        result.bytes_written = progress["done"]
        if not result.cancelled:
            for path in wanted:
                if path not in completed:
                    result.failed.append((path, reason or tr("error.not_in_archive")))
        return result

    # ----------------------------------------------------------------- write
    def write_bytes(self, arcname: str, data: bytes) -> None:
        name = arcname.replace("\\", "/")
        self._moved.pop(name, None)
        self._pending[name] = data

    def add(self, source: object, arcname: str) -> None:
        from pathlib import Path

        self.write_bytes(arcname, Path(source).read_bytes())  # type: ignore[arg-type]

    def add_dir(self, arcname: str) -> None:
        self._pending[arcname.replace("\\", "/").rstrip("/") + "/"] = b""

    def remove(self, arcname: str) -> None:
        name = arcname.replace("\\", "/")
        self._moved.pop(name, None)
        self._pending[name] = None

    def rename(self, old_arcname: str, new_arcname: str) -> None:
        old = old_arcname.replace("\\", "/")
        new = new_arcname.replace("\\", "/")
        if old == new:
            return
        if old in self._pending:
            staged = self._pending.pop(old)
            if staged is None:
                self._pending[old] = None
                return
            self._moved.pop(new, None)
            self._pending[new] = staged
            return
        source = self._moved.pop(old, None)
        if source is None:
            if old not in self._disk_entries():
                raise ArchiveError(f"Entry not found in archive: {old}")
            source = old
            self._pending[old] = None  # the old name disappears on save
        self._pending.pop(new, None)
        self._moved[new] = source

    # ------------------------------------------------------------------ save
    def save_plan(self) -> dict[str, int]:
        """What a save involves: bytes and files to write, and the free disk
        space it needs next to the archive."""
        from archy.core.backends.sevenzip_writer import free_space_needed

        kept, added, _dirs = self._save_items()
        kept_bytes = sum(f.size for f in kept if not f.is_dir)
        added_bytes = sum(len(f.data) for f in added)
        return {
            "bytes": kept_bytes + added_bytes,
            "files": sum(1 for f in kept if not f.is_dir) + len(added),
            "needed": free_space_needed(self.edit_profile(), kept_bytes, added_bytes),
        }

    def _save_items(self):  # noqa: ANN202
        """The new archive's content: kept files (streamed from this archive,
        possibly under a new name), added files and new empty directories."""
        from archy.core.backends.sevenzip_writer import NewFile, SourceFile

        with self._io_lock, self._shared_archive(self._password) as archive:
            files = list(archive.files)
        shown: dict[str, str] = {}  # source -> new name, for renamed entries
        for new, old in self._moved.items():
            shown[old] = new
        kept: list[SourceFile] = []
        names: set[str] = set()
        for f in files:
            source = str(f.filename).replace("\\", "/")
            name = shown.get(source)
            if name is None:
                if source in self._pending:
                    continue  # deleted, or replaced by staged bytes
                name = source
            elif name in self._pending:
                continue
            is_dir = bool(f.is_directory)
            kept.append(
                SourceFile(
                    name=name,
                    source=source,
                    size=0 if is_dir else int(f.uncompressed or 0),
                    crc=getattr(f, "crc32", None),
                    is_dir=is_dir,
                    mtime=getattr(f, "lastwritetime", None),
                    attributes=f._get_property("attributes"),
                )
            )
            names.add(name.rstrip("/"))
        added: list[NewFile] = []
        new_dirs: list[str] = []
        for name, data in self._pending.items():
            if data is None:
                continue
            if name.endswith("/"):
                if name.rstrip("/") not in names:
                    new_dirs.append(name.rstrip("/"))
                continue
            added.append(NewFile(name=name, data=data))
        # A directory that was deleted takes its explicit entry with it; one
        # whose files all left keeps it, so nothing the user did not delete
        # disappears.
        return kept, added, new_dirs

    def save(self, *, on_progress=None, is_cancelled=None) -> None:  # noqa: ANN001
        """Commit staged changes: write a complete new archive next to this one
        and replace this one only when the new archive checks out.

        The kept files stream from this archive into the new one (see
        sevenzip_writer): memory use stays small whatever the archive size,
        and the new archive keeps the password, the encrypted file names, Store
        or compression, and non-solid blocks of the original.
        """
        import shutil
        import tempfile

        from archy.core.backends.sevenzip_writer import rewrite, verify

        if not self.has_unsaved_changes:
            return
        profile = self.edit_profile()
        kept, added, new_dirs = self._save_items()
        plan = self.save_plan()
        free = shutil.disk_usage(self.path.parent).free
        if free < plan["needed"]:
            raise ArchiveError(
                f"Not enough free disk space next to the archive: {plan['needed']} bytes "
                f"needed, {free} available"
            )
        # The parsed index of a big archive is large (100,000 files: ~250 MB);
        # the save parses its own copy, so this one is let go meanwhile.
        with self._io_lock:
            self._drop_shared()
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=f".{self.path.stem}.", suffix=".archy-save"
        )
        os.close(fd)
        target = Path(tmp_name)
        password = self._password
        try:
            rewrite(
                open_source=lambda: _open_7z(self.path, password),
                target=target,
                password=password,
                profile=profile,
                kept=kept,
                added=added,
                new_dirs=new_dirs,
                on_progress=on_progress,
                is_cancelled=is_cancelled,
            )
            expected = {f.name: f.size for f in kept if not f.is_dir}
            expected.update({f.name: len(f.data) for f in added})
            verify(target, password=password, profile=profile, expected=expected)
            # Only now the original goes: every handle on it is closed first
            # (Windows refuses to replace an open file).
            with self._io_lock:
                self._drop_shared()
                os.replace(target, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                target.unlink(missing_ok=True)
            raise

        self._pending.clear()
        self._moved.clear()
        self._invalidate_caches()
