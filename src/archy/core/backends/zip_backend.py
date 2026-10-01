from __future__ import annotations

import contextlib
import io
import os
import tempfile
import threading
import zipfile
from collections.abc import Iterator
from datetime import datetime

from archy.core.backend import ArchiveBackend, ArchiveEntry, ArchiveInfo, EntryType
from archy.core.errors import ArchiveError, EncryptedArchiveError

# Read + write for zip. Reading uses stdlib zipfile and never writes to disk:
# read_bytes returns one entry's decompressed content in memory. Edits are
# staged in memory (write_bytes) and committed by save(): small archives are
# rebuilt entirely in memory and written in one go (no temp file); large ones
# are streamed through a temp file next to the archive and swapped in with an
# atomic os.replace, so the original is never left half-written and 300 GB is
# never held in RAM.

_ENCRYPTED_FLAG = 0x1

# For reading, prefer pyzipper.AESZipFile: a superset of stdlib zipfile that also
# decrypts WinZip AES entries (and still reads plain and legacy ZipCrypto zips).
# Fall back to stdlib if pyzipper is not installed.
try:
    import pyzipper as _pyzipper

    _ZipReader = _pyzipper.AESZipFile
except Exception:  # noqa: BLE001 - pyzipper is optional at import time
    _ZipReader = zipfile.ZipFile

# At or below this size, save() rebuilds the whole archive in memory (no temp
# file). Above it, save() streams through a temp file + atomic replace.
_IN_MEMORY_SAVE_LIMIT = 200 * 1024 * 1024


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

    _parallel_reads = True  # each read borrows its own reader

    @property
    def writable(self) -> bool:
        return True

    @property
    def fast_seek(self) -> bool:
        return True  # zip reads are seek-based, so small reads can be synchronous

    @property
    def has_unsaved_changes(self) -> bool:
        return bool(self._pending)

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
            try:
                if max_bytes is None:
                    return zf.read(entry.path, pwd=pwd)
                with zf.open(entry.path, pwd=pwd) as fh:
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
            with zf.open(entry.path, pwd=pwd) as fh:
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
        self._pending[arcname.replace("\\", "/")] = data

    def add(self, source: object, arcname: str) -> None:
        """Stage a file from disk to be added (or replace an entry) at arcname."""
        from pathlib import Path

        data = Path(source).read_bytes()  # type: ignore[arg-type]
        self._pending[arcname.replace("\\", "/")] = data

    def add_dir(self, arcname: str) -> None:
        self._pending[arcname.replace("\\", "/").rstrip("/") + "/"] = b""

    def remove(self, arcname: str) -> None:
        self._pending[arcname.replace("\\", "/")] = None

    def rename(self, old_arcname: str, new_arcname: str) -> None:
        """Stage a rename: the entry keeps its content, under a new name.

        Implemented as add-new + delete-old on top of the pending map, so save()
        needs no special case. If the entry was already staged (edited/added),
        the staged bytes move to the new name.
        """
        old = old_arcname.replace("\\", "/")
        new = new_arcname.replace("\\", "/")
        if old == new:
            return
        if old in self._pending:
            staged = self._pending.pop(old)
            if staged is None:
                # old was staged for deletion; keep it deleted, nothing to move.
                self._pending[old] = None
                return
            self._pending[new] = staged
            return
        entry = ArchiveEntry(path=old, type=EntryType.FILE)
        self._pending[new] = self.read_bytes(entry)
        self._pending[old] = None

    def save(self) -> None:
        if not self._pending:
            return
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0

        self._close_readers()  # the file is about to be rewritten/replaced
        if size <= _IN_MEMORY_SAVE_LIMIT:
            self._save_in_memory()
        else:
            self._save_via_tempfile()
        self._pending.clear()
        self._invalidate_caches()

    def _save_in_memory(self) -> None:
        buffer = io.BytesIO()
        with (
            zipfile.ZipFile(self.path) as src,
            zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as dst,
        ):
            self._copy_into(src, dst)
        # src is closed here; safe to overwrite the original with the new bytes.
        with open(self.path, "wb") as fh:
            fh.write(buffer.getvalue())

    def _save_via_tempfile(self) -> None:
        fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), suffix=".zxtmp")
        os.close(fd)
        try:
            with (
                zipfile.ZipFile(self.path) as src,
                zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED) as dst,
            ):
                self._copy_into(src, dst)
            os.replace(tmp_name, self.path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
            raise

    def _copy_into(self, src: zipfile.ZipFile, dst: zipfile.ZipFile) -> None:
        handled: set[str] = set()
        for info in src.infolist():
            name = info.filename.replace("\\", "/")
            if name in self._pending:
                handled.add(name)
                new = self._pending[name]
                if new is None:
                    continue  # deleted
                dst.writestr(info, new)  # reuse ZipInfo: keeps date, attrs
            else:
                dst.writestr(info, src.read(info.filename))
        # brand-new entries that were not in the original
        for name, new in self._pending.items():
            if name in handled or new is None:
                continue
            dst.writestr(name, new)
