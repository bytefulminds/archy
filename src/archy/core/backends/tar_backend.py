from __future__ import annotations

import tarfile
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from archy.core.backend import ArchiveBackend, ArchiveEntry, ArchiveInfo, EntryType
from archy.core.detect import detect_format
from archy.core.errors import ArchiveError

# Read-only backend for tar and compressed tars (.tar, .tar.gz, .tar.bz2,
# .tar.xz) using Python's standard-library ``tarfile``. This needs no native
# libarchive, so these formats work on every platform out of the box (which
# matters on Windows, where a libarchive DLL is painful to ship). libarchive is
# still used - when present - for the formats tarfile cannot read (rar, cpio,
# iso, ...); see core/factory.py.
#
# Nothing is written to disk while reading: tarfile streams a member's content
# into memory on demand.


def _entry_type(member: tarfile.TarInfo) -> EntryType:
    if member.isdir():
        return EntryType.DIRECTORY
    if member.issym() or member.islnk():
        return EntryType.SYMLINK
    if member.isfile():
        return EntryType.FILE
    return EntryType.OTHER


def _entry_mtime(member: tarfile.TarInfo) -> datetime | None:
    try:
        return datetime.fromtimestamp(member.mtime, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _normalize(path: str) -> str:
    """Path key for matching: forward slashes, no trailing slash."""
    return path.replace("\\", "/").rstrip("/")


class TarBackend(ArchiveBackend):
    """Read-only backend for tar/tar.gz/tar.bz2/tar.xz via stdlib ``tarfile``."""

    def _open(self) -> tarfile.TarFile:
        # "r:*" transparently handles an uncompressed tar and gzip/bzip2/xz
        # compression. Wrap tarfile's own errors in our ArchiveError.
        try:
            return tarfile.open(self.path, mode="r:*")
        except tarfile.TarError as exc:
            raise ArchiveError(f"Could not read archive: {self.path}: {exc}") from exc
        except OSError as exc:
            raise ArchiveError(f"Could not open archive: {self.path}: {exc}") from exc

    # ------------------------------------------------------------------ read
    def info(self) -> ArchiveInfo:
        count = sum(1 for _ in self.list_entries())
        return ArchiveInfo(
            path=self.path,
            format=detect_format(self.path) or "tar",
            writable=False,
            entry_count=count,
        )

    def list_entries(self) -> Iterator[ArchiveEntry]:
        try:
            with self._open() as tar:
                for member in tar:
                    yield ArchiveEntry(
                        path=member.name.replace("\\", "/"),
                        type=_entry_type(member),
                        size=int(member.size or 0),
                        modified=_entry_mtime(member),
                    )
        except ArchiveError:
            raise
        except tarfile.TarError as exc:  # a truncated/corrupt tar mid-iteration
            raise ArchiveError(f"Could not read archive: {self.path}: {exc}") from exc

    def _stream_uncached(self, entry: ArchiveEntry, sink) -> None:  # noqa: ANN001
        target = _normalize(entry.path)
        try:
            with self._open() as tar:
                for member in tar:
                    if _normalize(member.name) != target:
                        continue
                    source = tar.extractfile(member) if member.isfile() else None
                    if source is None:
                        return
                    with source:
                        while chunk := source.read(1024 * 1024):
                            sink(chunk)
                    return
        except tarfile.TarError as exc:
            raise ArchiveError(f"Could not read entry {entry.path}: {exc}") from exc
        raise ArchiveError(f"Entry not found in archive: {entry.path}")

    def _read_uncached(
        self, entry: ArchiveEntry, *, password: str | None = None, max_bytes: int | None = None
    ) -> bytes:
        target = _normalize(entry.path)
        try:
            with self._open() as tar:
                for member in tar:
                    if _normalize(member.name) != target:
                        continue
                    if not member.isfile():
                        return b""
                    source = tar.extractfile(member)
                    if source is None:
                        return b""
                    with source:
                        return source.read() if max_bytes is None else source.read(max_bytes)
        except tarfile.TarError as exc:
            raise ArchiveError(f"Could not read entry {entry.path}: {exc}") from exc
        raise ArchiveError(f"Entry not found in archive: {entry.path}")

    def _read_group_uncached(
        self,
        entries: list[ArchiveEntry],
        *,
        password: str | None = None,
        on_item: Callable[[str, bytes], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, bytes]:
        # A compressed tar is a single stream, so reading members one at a time
        # can re-decompress from the start each time. Decoding the folder's files
        # in one forward pass avoids that. (For an uncompressed tar it is simply a
        # single scan.) Each file is handed over as soon as it is read.
        wanted = {_normalize(e.path) for e in entries if not e.is_dir}
        out: dict[str, bytes] = {}
        if not wanted:
            return out
        found = 0
        with self._open() as tar:
            for member in tar:
                if is_cancelled is not None and is_cancelled():
                    break  # folder left / archive closed
                if _normalize(member.name) not in wanted or not member.isfile():
                    continue
                source = tar.extractfile(member)
                if source is not None:
                    with source:
                        data = source.read()
                    path = member.name.replace("\\", "/")
                    if on_item is not None:
                        on_item(path, data)
                    else:
                        out[path] = data
                found += 1
                if found >= len(wanted):
                    break
        return out

    # -------------------------------------------------------------- extract
    def extract(
        self,
        entries: Iterable[ArchiveEntry],
        dest_dir: Path | str,
        *,
        on_progress: Callable[[int, int, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ):  # noqa: ANN201 - ExtractResult (imported lazily to keep core import light)
        from archy.core.extraction import ExtractResult, safe_join

        dest_dir = Path(dest_dir)
        files = [e for e in entries if not e.is_dir]
        targets = {_normalize(e.path): e for e in files}
        remaining = set(targets)
        total = sum(max(e.size, 0) for e in files)
        result = ExtractResult()
        done = 0

        try:
            with self._open() as tar:
                for member in tar:
                    if not remaining or (is_cancelled is not None and is_cancelled()):
                        if remaining:
                            result.cancelled = True
                        break
                    key = _normalize(member.name)
                    entry = targets.get(key)
                    if entry is None:
                        continue
                    remaining.discard(key)
                    if not member.isfile():
                        continue
                    dest = safe_join(dest_dir, entry.path)
                    if dest is None:
                        result.failed.append((entry.path, "onveilig pad overgeslagen"))
                        continue
                    source = tar.extractfile(member)
                    if source is None:
                        continue
                    try:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with source, open(dest, "wb") as out:
                            while True:
                                if is_cancelled is not None and is_cancelled():
                                    result.cancelled = True
                                    break
                                block = source.read(1024 * 1024)
                                if not block:
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
        except (ArchiveError, tarfile.TarError) as exc:
            for key in remaining:
                result.failed.append((targets[key].path, str(exc)))

        return result
