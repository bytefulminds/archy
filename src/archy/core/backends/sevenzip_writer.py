from __future__ import annotations

import contextlib
import io
import os
import queue
import stat
import threading
import zlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from archy.core.errors import ArchiveError

# Saving a 7z: 7z has no in-place edit, so a save writes a complete new archive
# next to the original and only replaces it when everything went well (the same
# thing 7-Zip does). This module does that as a stream:
#
# - The kept files are decoded from the original in ONE sequential pass (a
#   producer thread) and handed, chunk by chunk, through a small bounded queue
#   to the writer, which encrypts and writes them into the new archive. Memory
#   use is a few MB whatever the archive size, and nothing is ever written to
#   disk unencrypted: the new archive is encrypted from its first byte.
# - The new archive keeps the original's character: the same password, file
#   names encrypted when they were, Store (copy) when every block was stored,
#   and one block per file (non-solid) unless the original was solid. py7zr
#   itself always writes one solid block; _Writer starts a new block per file.
# - File times and attributes are copied from the original.
# - Before the original is replaced, the new archive is opened again and
#   checked: same names and sizes, the expected method and header encryption,
#   and a sample of files decoded against their CRC.
#
# Every step can be cancelled; the original is untouched until the final
# os.replace, and a half-written archive is removed.

_CHUNK = 1024 * 1024
_QUEUE_CHUNKS = 8  # bounded hand-over between decoder and writer (~8 MB)
_PIPES_AHEAD = 4  # files the decoder may be ahead of the writer
_HEADER_MARGIN = 64 * 1024 * 1024  # free-space slack for the header and rounding


class SaveCancelled(Exception):
    """The user cancelled the save; the original archive is unchanged."""


@dataclass(frozen=True)
class EditProfile:
    """How a 7z archive was made, as far as rewriting it is concerned."""

    encrypted: bool  # file data is encrypted (7zAES)
    header_encrypted: bool  # file names are encrypted too
    store_only: bool  # every block is stored (Copy), so a rewrite is a copy
    solid: bool  # several files share a block
    packed_size: int  # size of the archive on disk
    unpacked_size: int  # sum of the file sizes
    format: str = "7z"  # "zip": the edit lock explains a zip save (zip_backend)

    @property
    def rewrite_is_cheap(self) -> bool:
        """A rewrite costs about as much as copying the archive."""
        return self.store_only


@dataclass
class SourceFile:
    """One file or directory of the original archive that is kept."""

    name: str  # name in the new archive
    source: str  # name in the original archive
    size: int
    crc: int | None
    is_dir: bool
    mtime: object | None  # py7zr ArchiveTimestamp
    attributes: int | None


@dataclass
class NewFile:
    """A file added (or replaced) by the user: bytes staged in memory."""

    name: str
    data: bytes


def probe_profile(archive, path: Path, password: str | None) -> EditProfile:  # noqa: ANN001
    """Describe ``archive`` (an open py7zr.SevenZipFile)."""
    import py7zr

    try:
        methods = {str(m).upper() for m in archive._get_method_names()}
    except Exception:  # noqa: BLE001
        methods = set()
    # Solid means several files share a block. Counted, not taken from py7zr's
    # flag: an empty file sharing a block (as Archy writes them) is not solid.
    try:
        blocks = len(archive.header.main_streams.unpackinfo.folders)
        with_data = sum(1 for f in archive.files if not f.is_directory and f.uncompressed)
        solid = blocks < with_data
    except Exception:  # noqa: BLE001
        solid = False
    encrypted = "7ZAES" in methods or password is not None
    store_only = bool(methods) and methods <= {"COPY", "7ZAES"}
    header_encrypted = False
    if password is not None:
        # The names are encrypted exactly when the index cannot be read
        # without the password.
        try:
            with open(path, "rb") as fh, py7zr.SevenZipFile(fh, mode="r") as plain:
                plain.list()
        except py7zr.PasswordRequired:
            header_encrypted = True
        except Exception:  # noqa: BLE001 - any failure without a password
            header_encrypted = True
    unpacked = 0
    with contextlib.suppress(Exception):
        unpacked = sum(int(f.uncompressed or 0) for f in archive.files if not f.is_directory)
    return EditProfile(
        encrypted=encrypted,
        header_encrypted=header_encrypted,
        store_only=store_only or not methods,
        solid=solid,
        packed_size=path.stat().st_size,
        unpacked_size=unpacked,
    )


class _Pipe:
    """Read side of one file's bytes, filled by the decoder thread."""

    def __init__(self, abort: threading.Event) -> None:
        self._queue: queue.Queue[bytes | BaseException | None] = queue.Queue(_QUEUE_CHUNKS)
        self._abort = abort
        self._buffer = b""
        self._done = False

    # producer side
    def put(self, item: bytes | BaseException | None) -> None:
        while True:
            if self._abort.is_set():
                raise SaveCancelled
            try:
                self._queue.put(item, timeout=0.2)
                return
            except queue.Full:
                continue

    # consumer side
    def read(self, size: int = -1) -> bytes:
        while not self._buffer and not self._done:
            if self._abort.is_set():
                raise SaveCancelled
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                self._done = True
            elif isinstance(item, BaseException):
                raise item
            else:
                self._buffer = item
        if size is None or size < 0 or size >= len(self._buffer):
            data, self._buffer = self._buffer, b""
        else:
            data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data


class _Counting:
    """Wraps a reader to report progress and honour cancel per chunk."""

    def __init__(self, inner, on_bytes: Callable[[int], None], cancelled) -> None:  # noqa: ANN001
        self._inner = inner
        self._on_bytes = on_bytes
        self._cancelled = cancelled

    def read(self, size: int = -1) -> bytes:
        if self._cancelled():
            raise SaveCancelled
        data = self._inner.read(size)
        if data:
            self._on_bytes(len(data))
        return data


class _Writer:
    """A py7zr writer that can start a new block per file (non-solid) and keeps
    the original times and attributes."""

    def __init__(
        self,
        target: Path,
        *,
        password: str | None,
        header_encrypted: bool,
        store: bool,
        solid: bool,
    ) -> None:
        import py7zr
        from py7zr.properties import FILTER_COPY, FILTER_CRYPTO_AES256_SHA256

        filters = None
        if store:
            filters = [{"id": FILTER_COPY}]
            if password is not None:
                filters.append({"id": FILTER_CRYPTO_AES256_SHA256})
        self._solid = solid
        self._zf = py7zr.SevenZipFile(
            target,
            mode="w",
            filters=filters,
            password=password,
            header_encryption=bool(header_encrypted and password is not None),
        )
        self._open_block = False

    def _info(self, name: str, *, size: int, empty: bool, is_dir: bool, mtime, attributes, data):  # noqa: ANN001, ANN202, E501
        from py7zr.helpers import ArchiveTimestamp
        from py7zr.member import MemberType
        from py7zr.py7zr import FileInfoDict

        if attributes is None:
            # Windows flag plus unix mode (rwxr-xr-x / rw-r--r--), like 7-Zip.
            kind = MemberType.DIRECTORY if is_dir else MemberType.FILE
            mode = (stat.S_IFDIR | 0o755) if is_dir else (stat.S_IFREG | 0o644)
            attributes = kind.win32_file_attributes | 0x8000 | (mode << 16)
        stamp = mtime if mtime is not None else ArchiveTimestamp.from_now()
        info = FileInfoDict(
            origin=None,
            filename=name,
            emptystream=empty,
            attributes=attributes,
            creationtime=stamp,
            lastwritetime=stamp,
            lastaccesstime=stamp,
        )
        if not empty:
            info["data"] = data
            info["uncompressed"] = size
        return info

    def _append(self, info, folder) -> None:  # noqa: ANN001
        zf = self._zf
        zf.header.files_info.files.append(info)
        zf.header.files_info.emptyfiles.append(info["emptystream"])
        zf.files.append(info)
        zf.worker.archive(zf.fp, zf.files, folder, deref=False)
        # The header keeps every file's info until the end; the reader (and
        # through it the decode pipe) is not needed once the data is written.
        info.pop("data", None)

    def add_dir(self, name: str, *, mtime=None, attributes=None) -> None:  # noqa: ANN001
        zf = self._zf
        if zf.header.main_streams is None:
            zf.header.initialize()  # the header needs its streams section
        info = self._info(
            name, size=0, empty=True, is_dir=True, mtime=mtime, attributes=attributes, data=None
        )
        self._append(info, None)

    def add_file(self, name: str, size: int, reader, *, mtime=None, attributes=None) -> None:  # noqa: ANN001
        zf = self._zf
        folder = zf.header.initialize()
        self._open_block = True
        info = self._info(
            name,
            size=size,
            empty=False,
            is_dir=False,
            mtime=mtime,
            attributes=attributes,
            data=reader,
        )
        self._append(info, folder)
        # Non-solid: close this block, so the next file starts a new one. An
        # empty file shares the next file's block (a block of 0 bytes is not
        # something every 7z reader accepts).
        if not self._solid and size > 0:
            zf.worker.flush_archive(zf.fp, folder)
            zf.header._initialized = False
            # The header only needs the block's sizes and coder settings; its
            # compressor (cipher state, buffers) would otherwise stay alive
            # for every file until the end.
            folder.compressor = None
            self._open_block = False

    def close(self) -> None:
        self._zf.close()

    def abort(self) -> None:
        with contextlib.suppress(Exception):
            self._zf.fp.close()


def rewrite(
    *,
    open_source: Callable[[], object],
    target: Path,
    password: str | None,
    profile: EditProfile,
    kept: list[SourceFile],
    added: list[NewFile],
    new_dirs: Iterable[str],
    on_progress: Callable[[int, int, str, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> None:
    """Write the new archive to ``target``: the kept files streamed from the
    original (opened with ``open_source``), then the added files and the new
    empty directories. Raises SaveCancelled or ArchiveError; on any failure
    ``target`` is removed."""
    cancelled = is_cancelled or (lambda: False)
    abort = threading.Event()
    total = sum(f.size for f in kept if not f.is_dir) + sum(len(f.data) for f in added)
    done = 0
    files_done = 0

    def report(name: str) -> None:
        if on_progress is not None:
            on_progress(done, total, name, files_done)

    writer = _Writer(
        target,
        password=password,
        header_encrypted=profile.header_encrypted,
        store=profile.store_only,
        solid=profile.solid,
    )
    producer: threading.Thread | None = None
    try:
        # Directories first (cheap, no data).
        for item in kept:
            if item.is_dir:
                writer.add_dir(item.name, mtime=item.mtime, attributes=item.attributes)
        for name in sorted(set(new_dirs)):
            writer.add_dir(name)

        # Empty files need no decoding.
        for item in kept:
            if not item.is_dir and item.size == 0:
                writer.add_file(
                    item.name, 0, io.BytesIO(b""), mtime=item.mtime, attributes=item.attributes
                )
                files_done += 1
        files = [f for f in kept if not f.is_dir and f.size > 0]
        if files:
            by_source = {f.source: f for f in files}
            # Bounded: with many small files the decoder would otherwise run
            # far ahead and keep thousands of filled pipes in memory.
            pipes: queue.Queue[tuple[SourceFile, _Pipe] | BaseException] = queue.Queue(_PIPES_AHEAD)
            producer = threading.Thread(
                target=_decode_kept,
                args=(open_source, by_source, pipes, abort),
                name="archy-7z-save-decode",
                daemon=True,
            )
            producer.start()
            for _ in range(len(files)):
                item = _next_pipe(pipes, abort, cancelled)
                source, pipe = item

                def counted(n: int, _name: str = source.name) -> None:
                    nonlocal done
                    done += n
                    report(_name)

                report(source.name)
                writer.add_file(
                    source.name,
                    source.size,
                    _Counting(pipe, counted, cancelled),
                    mtime=source.mtime,
                    attributes=source.attributes,
                )
                files_done += 1
            producer.join()
            producer = None

        for new in added:
            if cancelled():
                raise SaveCancelled

            def counted_new(n: int, _name: str = new.name) -> None:
                nonlocal done
                done += n
                report(_name)

            report(new.name)
            writer.add_file(
                new.name, len(new.data), _Counting(io.BytesIO(new.data), counted_new, cancelled)
            )
            files_done += 1
        if cancelled():
            raise SaveCancelled
        writer.close()
        report("")
    except BaseException as exc:
        abort.set()
        writer.abort()
        if producer is not None:
            producer.join(timeout=5)
        with contextlib.suppress(OSError):
            os.remove(target)
        if isinstance(exc, (SaveCancelled, ArchiveError)):
            raise
        raise ArchiveError(f"Saving the 7z archive failed: {exc}") from exc


def _next_pipe(pipes, abort, cancelled):  # noqa: ANN001, ANN202
    while True:
        if cancelled():
            raise SaveCancelled
        try:
            item = pipes.get(timeout=0.2)
        except queue.Empty:
            continue
        if isinstance(item, BaseException):
            raise item
        return item


def _put(q: queue.Queue, item, abort: threading.Event) -> None:  # noqa: ANN001
    """Put that gives up when the save is aborted (the reader is gone)."""
    while True:
        if abort.is_set():
            raise SaveCancelled
        try:
            q.put(item, timeout=0.2)
            return
        except queue.Full:
            continue


def _decode_kept(open_source, by_source: dict[str, SourceFile], pipes, abort) -> None:  # noqa: ANN001
    """Producer: decode the kept files from the original in one pass and feed
    each into its own pipe, checking every file's CRC on the way."""
    from py7zr.io import Py7zIO, WriterFactory

    class _PipeIO(Py7zIO):
        def __init__(self, item: SourceFile, pipe: _Pipe) -> None:
            self._item = item
            self._pipe = pipe
            self._crc = 0
            self._size = 0
            self._closed = False

        def write(self, s):  # noqa: ANN001, ANN202
            data = bytes(s)
            self._crc = zlib.crc32(data, self._crc)
            self._size += len(data)
            for start in range(0, len(data), _CHUNK):
                self._pipe.put(data[start : start + _CHUNK])
            return len(data)

        def finish(self) -> None:
            if self._closed:
                return
            self._closed = True
            expected = self._item.crc
            if self._size != self._item.size or (
                expected is not None and self._item.size and self._crc != expected
            ):
                self._pipe.put(ArchiveError(f"CRC error in the original: {self._item.source}"))
            else:
                self._pipe.put(None)

        def close(self) -> None:
            self.finish()

        def read(self, size=None) -> bytes:  # noqa: ANN001
            return b""

        def seek(self, offset, whence=0) -> int:  # noqa: ANN001
            return 0

        def flush(self) -> None:
            pass

        def size(self) -> int:
            return self._size

    opened: list[_PipeIO] = []  # the file being decoded
    created = [0]

    class _Factory(WriterFactory):
        def create(self, filename: str) -> Py7zIO:
            if opened:
                opened[-1].finish()  # older py7zr does not call close()
                opened.clear()  # only the current file is needed
            item = by_source.get(filename.replace("\\", "/"))
            if item is None:
                raise ArchiveError(f"Unexpected file while saving: {filename}")
            pipe = _Pipe(abort)
            io_obj = _PipeIO(item, pipe)
            opened.append(io_obj)
            created[0] += 1
            _put(pipes, (item, pipe), abort)
            return io_obj

    try:
        archive = open_source()
        try:
            archive.extract(targets=list(by_source), factory=_Factory())  # type: ignore[attr-defined]
        finally:
            with contextlib.suppress(Exception):
                archive.close()  # type: ignore[attr-defined]
        if opened:
            opened[-1].finish()
        if created[0] != len(by_source):
            raise ArchiveError("Not every file could be read from the original archive")
    except SaveCancelled:
        return
    except BaseException as exc:  # noqa: BLE001 - handed to the writer side
        if not abort.is_set():
            error = exc if isinstance(exc, ArchiveError) else ArchiveError(str(exc))
            if opened and not opened[-1]._closed:
                with contextlib.suppress(SaveCancelled):
                    opened[-1]._pipe.put(error)
            with contextlib.suppress(SaveCancelled):
                _put(pipes, error, abort)


def verify(
    target: Path,
    *,
    password: str | None,
    profile: EditProfile,
    expected: dict[str, int],
    sample: int = 3,
) -> None:
    """Open the written archive again and check it before it replaces the
    original. ``expected`` maps every file name to its size."""
    import py7zr
    from py7zr.io import BytesIOFactory

    try:
        with open(target, "rb") as fh, py7zr.SevenZipFile(fh, mode="r", password=password) as zf:
            infos = {str(f.filename).replace("\\", "/"): f for f in zf.list() if not f.is_directory}
            if set(infos) != set(expected):
                raise ArchiveError("The new archive does not contain the expected files")
            for name, size in expected.items():
                if int(infos[name].uncompressed or 0) != size:
                    raise ArchiveError(f"Size mismatch in the new archive: {name}")
            if profile.encrypted and password is not None and not zf.needs_password():
                raise ArchiveError("The new archive is not encrypted")
            # Decode a few files (the smallest ones, cheap) against their CRC.
            names = sorted(expected, key=lambda n: expected[n])[:sample]
            if names:
                zf.reset()
                factory = BytesIOFactory(max(max(expected[n] for n in names), 1))
                zf.extract(targets=names, factory=factory)
                for name in names:
                    product = factory.products.get(name)
                    data = product.read() if product is not None else b""
                    crc = infos[name].crc32
                    if len(data) != expected[name] or (
                        crc is not None and expected[name] and zlib.crc32(data) != crc
                    ):
                        raise ArchiveError(f"Verification failed for {name}")
    except ArchiveError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ArchiveError(f"The new archive could not be verified: {exc}") from exc
    if profile.header_encrypted and password is not None:
        try:
            with open(target, "rb") as fh, py7zr.SevenZipFile(fh, mode="r") as plain:
                plain.list()
        except Exception:  # noqa: BLE001 - expected: names need the password
            return
        raise ArchiveError("The file names of the new archive are not encrypted")


def free_space_needed(profile: EditProfile, kept_bytes: int, added_bytes: int) -> int:
    """Disk space the new archive needs next to the original."""
    if profile.store_only:
        estimate = kept_bytes + added_bytes
    else:
        ratio = profile.packed_size / profile.unpacked_size if profile.unpacked_size else 1.0
        estimate = int((kept_bytes + added_bytes) * min(1.0, max(ratio, 0.05)))
    return estimate + _HEADER_MARGIN


__all__ = [
    "EditProfile",
    "NewFile",
    "SaveCancelled",
    "SourceFile",
    "free_space_needed",
    "probe_profile",
    "rewrite",
    "verify",
]
