from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from archy.core.backend import ArchiveBackend, ArchiveEntry, ArchiveInfo, EntryType
from archy.core.detect import detect_format
from archy.core.errors import ArchiveError, EncryptedArchiveError
from archy.core.native import ensure_libarchive_env

# Broad read-only access via libarchive. libarchive streams entries
# sequentially, so we never need the whole archive on disk to browse it: we
# iterate the entry headers for the listing, and re-stream a single entry's
# blocks into a bytes buffer when its content is requested. Nothing is written
# to disk.

# Substrings that mark a libarchive error as a missing/incorrect passphrase.
_ENCRYPTED_MARKERS = ("passphrase", "encrypt", "wrong password", "incorrect password")


def _looks_encrypted(message: str) -> bool:
    low = message.lower()
    return any(marker in low for marker in _ENCRYPTED_MARKERS)


def _entry_type(entry: object) -> EntryType:
    if getattr(entry, "isdir", False):
        return EntryType.DIRECTORY
    if getattr(entry, "issym", False):
        return EntryType.SYMLINK
    if getattr(entry, "isfile", False):
        return EntryType.FILE
    return EntryType.OTHER


def _entry_mtime(entry: object) -> datetime | None:
    raw = getattr(entry, "mtime", None)
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(raw, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _normalize(path: str) -> str:
    """Path key for matching: forward slashes, no trailing slash."""
    return path.replace("\\", "/").rstrip("/")


class LibArchiveBackend(ArchiveBackend):
    """Read-only backend backed by libarchive (via libarchive-c)."""

    def __init__(self, path: Path | str) -> None:
        super().__init__(path)
        ensure_libarchive_env()

    # ------------------------------------------------------------------ read
    def info(self) -> ArchiveInfo:
        count = sum(1 for _ in self.list_entries())
        return ArchiveInfo(
            path=self.path,
            format=detect_format(self.path) or "unknown",
            writable=False,
            entry_count=count,
        )

    def list_entries(self) -> Iterator[ArchiveEntry]:
        import libarchive

        try:
            with libarchive.file_reader(str(self.path), passphrase=self._password) as archive:
                for raw in archive:
                    etype = _entry_type(raw)
                    yield ArchiveEntry(
                        path=raw.pathname.replace("\\", "/"),
                        type=etype,
                        size=int(getattr(raw, "size", 0) or 0),
                        modified=_entry_mtime(raw),
                        is_encrypted=bool(getattr(raw, "is_encrypted", False)),
                    )
        except ArchiveError:
            raise
        except Exception as exc:  # noqa: BLE001 - wrap libarchive's own errors
            raise ArchiveError(f"Could not read archive: {self.path}: {exc}") from exc

    def _stream_uncached(self, entry: ArchiveEntry, sink) -> None:  # noqa: ANN001
        import libarchive

        from archy.core.backend import _StreamCancelled

        target = _normalize(entry.path)
        pw = self._effective_password(None)
        try:
            with libarchive.file_reader(str(self.path), passphrase=pw) as archive:
                for raw in archive:
                    if _normalize(raw.pathname) != target:
                        continue
                    if getattr(raw, "isfile", False):
                        for block in raw.get_blocks():
                            sink(bytes(block))
                    return
        except (ArchiveError, _StreamCancelled):
            raise
        except Exception as exc:  # noqa: BLE001 - wrap libarchive's own errors
            if _looks_encrypted(str(exc)):
                raise EncryptedArchiveError(
                    f"Entry is encrypted or the password is wrong: {entry.path}"
                ) from exc
            raise ArchiveError(f"Could not read entry {entry.path}: {exc}") from exc
        raise ArchiveError(f"Entry not found in archive: {entry.path}")

    def _read_uncached(
        self, entry: ArchiveEntry, *, password: str | None = None, max_bytes: int | None = None
    ) -> bytes:
        import libarchive

        target = _normalize(entry.path)
        pw = self._effective_password(password)
        try:
            with libarchive.file_reader(str(self.path), passphrase=pw) as archive:
                for raw in archive:
                    if _normalize(raw.pathname) != target:
                        continue
                    if not getattr(raw, "isfile", False):
                        return b""
                    if max_bytes is None:
                        return b"".join(raw.get_blocks())
                    buf = bytearray()
                    for block in raw.get_blocks():
                        buf += block
                        if len(buf) >= max_bytes:
                            break
                    return bytes(buf[:max_bytes])
        except ArchiveError:
            raise
        except Exception as exc:  # noqa: BLE001 - wrap libarchive's own errors
            if _looks_encrypted(str(exc)):
                raise EncryptedArchiveError(
                    f"Entry is encrypted or the password is wrong: {entry.path}"
                ) from exc
            raise ArchiveError(f"Could not read entry {entry.path}: {exc}") from exc

        raise ArchiveError(f"Entry not found in archive: {entry.path}")

    # -------------------------------------------------------------- extract
    def extract(self, entries, dest_dir, *, on_progress=None, is_cancelled=None):  # noqa: ANN001, ANN201
        import libarchive

        from archy.core.extraction import ExtractResult, safe_join

        dest_dir = Path(dest_dir)
        files = [e for e in entries if not e.is_dir]
        targets = {_normalize(e.path): e for e in files}
        remaining = set(targets)
        total = sum(max(e.size, 0) for e in files)
        result = ExtractResult()
        done = 0
        pw = self._password

        try:
            with libarchive.file_reader(str(self.path), passphrase=pw) as archive:
                for raw in archive:
                    if not remaining or (is_cancelled is not None and is_cancelled()):
                        if remaining:
                            result.cancelled = True
                        break
                    key = _normalize(raw.pathname)
                    entry = targets.get(key)
                    if entry is None:
                        continue
                    remaining.discard(key)
                    if not getattr(raw, "isfile", False):
                        continue
                    dest = safe_join(dest_dir, entry.path)
                    if dest is None:
                        result.failed.append((entry.path, "onveilig pad overgeslagen"))
                        continue
                    try:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with open(dest, "wb") as out:
                            for block in raw.get_blocks():
                                if is_cancelled is not None and is_cancelled():
                                    result.cancelled = True
                                    break
                                out.write(block)
                                done += len(block)
                                result.bytes_written += len(block)
                                if on_progress is not None:
                                    on_progress(done, total, entry.path)
                        if result.cancelled:
                            dest.unlink(missing_ok=True)
                            break
                        result.extracted += 1
                    except OSError as exc:
                        result.failed.append((entry.path, str(exc)))
        except ArchiveError:
            raise
        except Exception as exc:  # noqa: BLE001 - whole-archive failure (e.g. bad passphrase)
            reason = "versleuteld/fout wachtwoord" if _looks_encrypted(str(exc)) else str(exc)
            for key in remaining:
                result.failed.append((targets[key].path, reason))

        return result
