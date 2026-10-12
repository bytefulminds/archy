from __future__ import annotations

import contextlib
import dataclasses
import os
import shutil
import tempfile
import threading
import zipfile
from collections.abc import Iterator
from datetime import datetime

from archy.core.backend import ArchiveBackend, ArchiveEntry, ArchiveInfo, EntryType
from archy.core.errors import ArchiveError, EncryptedArchiveError

# Read + write for zip. Reading never writes to disk: read_bytes returns one
# entry's decompressed content in memory. Edits are staged (write_bytes, and
# renames as a name map so a renamed 4 GB video is never read) and committed by
# save(), which writes a new archive next to the original, checks it, and swaps
# it in with an atomic os.replace: the original is never left half-written.
#
# Entries that did not change are copied as they are, byte for byte: still
# compressed and, in an encrypted zip, still encrypted with their own method
# (ZipCrypto or AES). A save therefore costs about a copy of the archive, never
# decompressing and compressing everything again. New and changed files are
# compressed, and in an archive opened with a password encrypted with AES-256
# under that password.

_ENCRYPTED_FLAG = 0x1

# For reading, prefer pyzipper.AESZipFile: a superset of stdlib zipfile that also
# decrypts WinZip AES entries (and still reads plain and legacy ZipCrypto zips).
# Fall back to stdlib if pyzipper is not installed.
try:
    import pyzipper as _pyzipper

    _ZipReader = _pyzipper.AESZipFile
except Exception:  # noqa: BLE001 - pyzipper is optional at import time
    _ZipReader = zipfile.ZipFile

_COPY_CHUNK = 8 * 1024 * 1024
_LOCAL_HEADER = b"PK\x03\x04"
_DESCRIPTOR = b"PK\x07\x08"
_DATA_DESCRIPTOR_FLAG = 0x08


def _zip_mtime(info: zipfile.ZipInfo) -> datetime | None:
    year = info.date_time[0]
    if year < 1980:
        return None
    try:
        return datetime(*info.date_time)
    except (ValueError, TypeError):
        return None


class ZipBackend(ArchiveBackend):
    def __init__(self, path: object) -> None:
        # The base class sets up the staged-edits map (_pending) and the caches.
        super().__init__(path)  # type: ignore[arg-type]
        # A small pool of open readers, reused across reads. Opening a zip
        # parses its whole central directory in pure Python (holding the GIL):
        # on a folder of 1500 photos that was 1500 parses of 1500 entries
        # each, which starved the UI thread while thumbnails loaded. A reader
        # is borrowed for one read, so it is never shared between threads
        # (thread-locals do not work here: Qt pool threads get a fresh Python
        # thread state per task). Keyed on the file's mtime/size so a saved
        # archive is reopened; all are closed before save() (Windows cannot
        # replace a file that is still open).
        self._idle_readers: list = []  # (stamp, reader) not in use right now
        self._readers_lock = threading.Lock()
        self._readers_epoch = 0
        # Renames: new name -> name in the archive. The entry is copied under
        # its new name on save, without being read.
        self._moved: dict[str, str] = {}

    _parallel_reads = True  # each read borrows its own reader
    archive_format = "zip"

    @property
    def writable(self) -> bool:
        return True

    @property
    def fast_seek(self) -> bool:
        return True  # zip reads are seek-based, so small reads can be synchronous

    @property
    def has_unsaved_changes(self) -> bool:
        return bool(self._pending or self._moved)

    def discard_changes(self) -> None:
        super().discard_changes()
        self._moved.clear()

    def edit_profile(self):  # noqa: ANN201 - EditProfile
        """How this zip is made, for the edit lock: a save copies the entries
        that stay as they are, so it is cheap whatever the encryption."""
        from archy.core.backends.sevenzip_writer import EditProfile

        encrypted = False
        unpacked = 0
        with zipfile.ZipFile(self.path) as zf:
            for info in zf.infolist():
                encrypted = encrypted or bool(info.flag_bits & _ENCRYPTED_FLAG)
                unpacked += info.file_size
        return EditProfile(
            encrypted=encrypted,
            header_encrypted=False,
            store_only=True,  # i.e. a rewrite is a copy (rewrite_is_cheap)
            solid=False,
            packed_size=self.path.stat().st_size,
            unpacked_size=unpacked,
            format="zip",
        )

    def _source_path(self, path: str) -> str:
        """Name in the archive of an entry shown under ``path`` (renames)."""
        return self._moved.get(path, path)

    def list_effective_entries(self) -> list[ArchiveEntry]:
        entries = super().list_effective_entries()
        if not self._moved:
            return entries
        disk = {e.path: e for e in self.list_entries()}
        shown = {e.path for e in entries}
        for new, old in self._moved.items():
            original = disk.get(old)
            if original is not None and new not in shown and new not in self._pending:
                entries.append(dataclasses.replace(original, path=new))
        return entries

    # ------------------------------------------------------------------ read
    def info(self) -> ArchiveInfo:
        try:
            with zipfile.ZipFile(self.path) as zf:
                count = len(zf.infolist())
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"Not a valid zip archive: {self.path}: {exc}") from exc
        return ArchiveInfo(path=self.path, format="zip", writable=True, entry_count=count)

    def list_entries(self) -> Iterator[ArchiveEntry]:
        try:
            with zipfile.ZipFile(self.path) as zf:
                for info in zf.infolist():
                    is_dir = info.is_dir()
                    yield ArchiveEntry(
                        path=info.filename.replace("\\", "/"),
                        type=EntryType.DIRECTORY if is_dir else EntryType.FILE,
                        size=info.file_size,
                        compressed_size=info.compress_size,
                        modified=_zip_mtime(info),
                        is_encrypted=bool(info.flag_bits & _ENCRYPTED_FLAG),
                    )
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"Not a valid zip archive: {self.path}: {exc}") from exc

    def _read_uncached(
        self, entry: ArchiveEntry, *, password: str | None = None, max_bytes: int | None = None
    ) -> bytes:
        if entry.is_dir:
            return b""
        pwd_str = self._effective_password(password)
        pwd = pwd_str.encode("utf-8") if pwd_str is not None else None
        try:
            stamp, zf = self._borrow_reader()
            source = self._source_path(entry.path)
            try:
                if max_bytes is None:
                    return zf.read(source, pwd=pwd)
                with zf.open(source, pwd=pwd) as fh:
                    return fh.read(max_bytes)
            finally:
                self._return_reader(stamp, zf)
        except KeyError as exc:
            raise ArchiveError(f"Entry not found in archive: {entry.path}") from exc
        except RuntimeError as exc:
            message = str(exc).lower()
            if "password" in message or "encrypted" in message:
                raise EncryptedArchiveError(
                    f"Entry is encrypted or the password is wrong: {entry.path}"
                ) from exc
            raise ArchiveError(f"Could not read entry {entry.path}: {exc}") from exc
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"Not a valid zip archive: {self.path}: {exc}") from exc

    def _stream_uncached(self, entry, sink) -> None:  # noqa: ANN001
        pwd_str = self._password
        pwd = pwd_str.encode("utf-8") if pwd_str is not None else None
        stamp, zf = self._borrow_reader()
        try:
            with zf.open(self._source_path(entry.path), pwd=pwd) as fh:
                while chunk := fh.read(1024 * 1024):
                    sink(chunk)
        finally:
            self._return_reader(stamp, zf)

    def _stamp(self) -> tuple[int, int, int]:
        st = os.stat(self.path)
        return (st.st_mtime_ns, st.st_size, self._readers_epoch)

    def _borrow_reader(self):  # noqa: ANN202 - (stamp, pyzipper ZipFile)
        """An open reader for the current file, reused when possible. Borrowed
        for one read, so no two threads ever share one."""
        stamp = self._stamp()
        with self._readers_lock:
            while self._idle_readers:
                old_stamp, zf = self._idle_readers.pop()
                if old_stamp == stamp:
                    return stamp, zf
                with contextlib.suppress(Exception):
                    zf.close()
        return stamp, _ZipReader(self.path)

    def _return_reader(self, stamp: tuple[int, int, int], zf) -> None:  # noqa: ANN001
        with self._readers_lock:
            if stamp == self._stamp() and len(self._idle_readers) < 16:
                self._idle_readers.append((stamp, zf))
                return
        with contextlib.suppress(Exception):
            zf.close()

    def _close_readers(self) -> None:
        with self._readers_lock:
            readers, self._idle_readers = self._idle_readers, []
            self._readers_epoch += 1  # readers still borrowed are closed on return
        for _stamp, zf in readers:
            with contextlib.suppress(Exception):  # closing is best-effort
                zf.close()

    def close(self) -> None:
        self._close_readers()
        super().close()

    # -------------------------------------------------------------- extract
    def extract(self, entries, dest_dir, *, on_progress=None, is_cancelled=None):  # noqa: ANN001, ANN201
        from pathlib import Path

        from archy.core.extraction import run_extract

        pwd_str = self._password
        pwd = pwd_str.encode("utf-8") if pwd_str is not None else None
        files = [e for e in entries if not e.is_dir]

        with _ZipReader(self.path) as zf:

            def block_iter(entry):  # noqa: ANN001, ANN202
                with zf.open(entry.path, pwd=pwd) as src:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        yield chunk

            return run_extract(
                Path(dest_dir),
                files,
                block_iter,
                on_progress=on_progress,
                is_cancelled=is_cancelled,
            )

    # ----------------------------------------------------------------- write
    def write_bytes(self, arcname: str, data: bytes) -> None:
        name = arcname.replace("\\", "/")
        self._moved.pop(name, None)
        self._pending[name] = data

    def add(self, source: object, arcname: str) -> None:
        """Stage a file from disk to be added (or replace an entry) at arcname."""
        from pathlib import Path

        self.write_bytes(arcname, Path(source).read_bytes())  # type: ignore[arg-type]

    def add_dir(self, arcname: str) -> None:
        self._pending[arcname.replace("\\", "/").rstrip("/") + "/"] = b""

    def remove(self, arcname: str) -> None:
        name = arcname.replace("\\", "/")
        self._moved.pop(name, None)
        self._pending[name] = None

    def rename(self, old_arcname: str, new_arcname: str) -> None:
        """Stage a rename. An entry from the archive is not read: it is copied
        under its new name on save. A staged (new or edited) entry moves its
        staged bytes."""
        old = old_arcname.replace("\\", "/")
        new = new_arcname.replace("\\", "/")
        if old == new:
            return
        if old in self._pending:
            staged = self._pending.pop(old)
            if staged is None:
                self._pending[old] = None  # deleted: stays deleted, nothing to move
                return
            self._moved.pop(new, None)
            self._pending[new] = staged
            return
        source = self._moved.pop(old, None)
        if source is None:
            source = old
            self._pending[old] = None  # the old name disappears on save
        self._pending.pop(new, None)
        self._moved[new] = source

    # ------------------------------------------------------------------ save
    def save_plan(self) -> dict[str, int]:
        """What a save involves: bytes and files to write, and the free disk
        space it needs next to the archive."""
        kept = 0
        files = 0
        with zipfile.ZipFile(self.path) as zf:
            infos = {i.filename.replace("\\", "/"): i for i in zf.infolist()}
        for name, info in infos.items():
            if name not in self._pending and name not in self._moved:
                kept += info.compress_size
                files += 1
        for old in self._moved.values():
            info = infos.get(old)
            kept += info.compress_size if info is not None else 0
            files += 1
        added = [data for data in self._pending.values() if data]
        total = kept + sum(len(data) for data in added)
        return {"bytes": total, "files": files + len(added), "needed": total + 1024 * 1024}

    def save(self, *, on_progress=None, is_cancelled=None) -> None:  # noqa: ANN001
        """Write the archive with the staged changes next to the original,
        check it, and replace the original with it. ``on_progress(done,
        total, name, files)`` follows the bytes written; ``is_cancelled()``
        stops it (SaveCancelled), leaving the original as it was."""
        if not self.has_unsaved_changes:
            return
        plan = self.save_plan()
        if shutil.disk_usage(self.path.parent).free < plan["needed"]:
            raise ArchiveError(
                f"Not enough free disk space next to the archive: {plan['needed']} bytes needed"
            )
        self._close_readers()  # Windows cannot replace a file that is open
        fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), suffix=".zxtmp")
        os.close(fd)
        progress = _Progress(plan["bytes"], on_progress, is_cancelled)
        try:
            expected = self._write_new(tmp_name, progress)
            self._check_new(tmp_name, expected)
            os.replace(tmp_name, self.path)
            progress.finish()
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(tmp_name)
            raise
        self._pending.clear()
        self._moved.clear()
        self._invalidate_caches()

    def _password_bytes(self) -> bytes | None:
        password = self._password
        return password.encode("utf-8") if password is not None else None

    def _write_new(self, target: str, progress: _Progress) -> dict[str, bytes | None]:
        """Write the new archive; returns its names, each with the bytes it
        must hold when they were written now (None: copied as it was)."""
        import pyzipper

        password = self._password_bytes()
        expected: dict[str, bytes | None] = {}
        with (
            open(self.path, "rb") as raw,
            _ZipReader(self.path) as src,
            pyzipper.AESZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as dst,
        ):
            if password is not None:
                dst.setpassword(password)
                dst.setencryption(pyzipper.WZ_AES, nbits=256)
            infos = {info.filename.replace("\\", "/"): info for info in src.infolist()}
            for name, info in infos.items():
                if name in self._moved:
                    continue  # a renamed entry takes this name (written below)
                if name in self._pending:
                    data = self._pending[name]
                    if data is not None:
                        _write_entry(dst, _new_info(dst, name, info), data)
                        expected[name] = data
                        progress.file_done(name, len(data))
                    continue
                _copy_raw(raw, info, name, dst, progress)
                expected[name] = None
            for new, old in self._moved.items():
                if new in self._pending:
                    continue
                info = infos.get(old)
                if info is None:
                    raise ArchiveError(f"Entry not found in archive: {old}")
                _copy_raw(raw, info, new, dst, progress)
                expected[new] = None
            for name, data in self._pending.items():
                if data is None or name in infos or name in self._moved:
                    continue
                _write_entry(dst, _new_info(dst, name, None), data)
                expected[name] = data
                progress.file_done(name, len(data))
        return expected

    def _check_new(self, target: str, expected: dict[str, bytes | None]) -> None:
        """Read the new archive back: every name there, each copied entry's
        local header in place, each written entry with the right bytes."""
        password = self._password_bytes()
        try:
            with _ZipReader(target) as zf:
                names = [info.filename for info in zf.infolist()]
                if sorted(names) != sorted(expected):
                    raise ArchiveError("The new archive does not list the expected files")
                for name, data in expected.items():
                    if name.endswith("/"):
                        continue
                    if data is None:
                        with zf.open(name, pwd=password) as fh:
                            fh.read(0)
                    elif zf.read(name, pwd=password) != data:
                        raise ArchiveError(f"The new archive does not hold {name} correctly")
        except (zipfile.BadZipFile, RuntimeError, KeyError, OSError) as exc:
            raise ArchiveError(f"Saving failed while checking the new archive: {exc}") from exc


def _write_entry(dst, info: zipfile.ZipInfo, data: bytes) -> None:  # noqa: ANN001
    """Write a new entry; a folder entry holds nothing, so it is not
    encrypted (as 7-Zip and WinZip do)."""
    if not info.filename.endswith("/"):
        dst.writestr(info, data)
        return
    encryption, password = dst.encryption, dst.pwd
    dst.encryption, dst.pwd = None, None
    try:
        dst.writestr(info, data)
    finally:
        dst.encryption, dst.pwd = encryption, password


def _new_info(dst, name: str, old: zipfile.ZipInfo | None) -> zipfile.ZipInfo:  # noqa: ANN001
    """The entry for a new or changed file in ``dst``: dated now, keeping the
    old entry's permissions."""
    info = dst.zipinfo_cls(name, date_time=datetime.now().timetuple()[:6])
    info.compress_type = zipfile.ZIP_STORED if name.endswith("/") else zipfile.ZIP_DEFLATED
    if old is not None:
        info.external_attr = old.external_attr
        info.create_system = old.create_system
    elif name.endswith("/"):
        info.external_attr = 0o40755 << 16 | 0x10  # a directory (Unix and DOS bits)
    else:
        info.external_attr = 0o644 << 16
    return info


class _Progress:
    """Save progress in bytes, and the place where a cancel takes effect."""

    def __init__(self, total: int, on_progress, is_cancelled) -> None:  # noqa: ANN001
        self.total = max(1, total)
        self.done = 0
        self.files = 0
        self._on_progress = on_progress
        self._is_cancelled = is_cancelled

    def bytes_done(self, name: str, count: int) -> None:
        if self._is_cancelled is not None and self._is_cancelled():
            from archy.core.backends.sevenzip_writer import SaveCancelled

            raise SaveCancelled
        self.done += count
        if self._on_progress is not None:
            self._on_progress(min(self.done, self.total), self.total, name, self.files)

    def file_done(self, name: str, count: int) -> None:
        self.files += 1
        self.bytes_done(name, count)

    def finish(self) -> None:
        if self._on_progress is not None:
            self._on_progress(self.total, self.total, "", self.files)


def _copy_raw(raw, info: zipfile.ZipInfo, name: str, dst, progress: _Progress) -> None:  # noqa: ANN001
    """Copy one entry as it is in the archive (compressed, encrypted as it
    was) into ``dst``, under ``name``: its local header, data and data
    descriptor, then its central directory record via the ZipInfo."""
    import copy

    raw.seek(info.header_offset)
    header = raw.read(30)
    if len(header) != 30 or header[:4] != _LOCAL_HEADER:
        raise ArchiveError(f"Damaged entry in the archive: {info.filename}")
    name_length = int.from_bytes(header[26:28], "little")
    extra_length = int.from_bytes(header[28:30], "little")
    raw.seek(name_length, os.SEEK_CUR)
    extra = raw.read(extra_length)
    data_start = raw.tell()
    length = info.compress_size
    if info.flag_bits & _DATA_DESCRIPTOR_FLAG:
        raw.seek(data_start + length)
        signature = raw.read(4)
        large = info.compress_size >= 0xFFFFFFFF or info.file_size >= 0xFFFFFFFF
        large = large or b"\x01\x00" in extra[:2]  # a zip64 extra field in the local header
        length += (4 if signature == _DESCRIPTOR else 0) + 4 + (16 if large else 8)

    new = copy.copy(info)
    new.filename = name
    new.orig_filename = name
    encoded, flags = new._encodeFilenameFlags()
    new.flag_bits = flags
    local = bytearray(header)
    local[6:8] = flags.to_bytes(2, "little")
    local[26:28] = len(encoded).to_bytes(2, "little")

    fp = dst.fp
    fp.seek(dst.start_dir)
    new.header_offset = fp.tell()
    fp.write(bytes(local))
    fp.write(encoded)
    fp.write(extra)
    raw.seek(data_start)
    remaining = length
    while remaining > 0:
        chunk = raw.read(min(_COPY_CHUNK, remaining))
        if not chunk:
            raise ArchiveError(f"The archive ends unexpectedly: {info.filename}")
        fp.write(chunk)
        remaining -= len(chunk)
        progress.bytes_done(name, len(chunk))
    progress.files += 1
    dst.filelist.append(new)
    dst.NameToInfo[name] = new
    dst.start_dir = fp.tell()
    dst._didModify = True
