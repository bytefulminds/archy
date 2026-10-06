from __future__ import annotations

import io
import tarfile
import zipfile
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime
from pathlib import Path, PurePosixPath

from archy.core.backend import ArchiveBackend, ArchiveEntry, ArchiveInfo, EntryType
from archy.core.errors import ArchiveError, EncryptedArchiveError, UnsupportedFormatError

# An archive inside an archive (a zip in a 7z backup, a tar.gz in a zip),
# opened from its bytes in memory: never written to disk, read-only.
# Experimental: zip, 7z and tar (also .tar.gz/.bz2/.xz) are read here; other
# formats when libarchive is available.

NESTED_EXT = (
    ".zip",
    ".7z",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
    ".rar",
    ".cbz",
    ".cb7",
)


def looks_nested(name: str) -> bool:
    """Whether a file in an archive can be opened as an archive itself."""
    lower = name.lower()
    return any(lower.endswith(ext) for ext in NESTED_EXT)


def nested_format(data: bytes, name: str) -> str:
    """zip / 7z / tar / libarchive, by content first."""
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        return "zip"
    if data[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*"):
            return "tar"
    except (tarfile.TarError, OSError, EOFError):
        pass
    if name.lower().endswith(".zip"):
        return "zip"
    return "libarchive"


def _norm(path: str) -> str:
    return str(PurePosixPath(path.replace("\\", "/"))).lstrip("/")


class NestedBackend(ArchiveBackend):
    """A read-only archive held in memory."""

    nested = True  # opened from inside another archive (the window shows that)

    def __init__(self, data: bytes, name: str) -> None:
        super().__init__(Path(name))
        self._data = data
        self._format = nested_format(data, name)
        self._entries: list[ArchiveEntry] | None = None
        self._sources: dict[str, str] = {}  # entry path -> name inside the archive

    # --------------------------------------------------------------- listing
    def info(self) -> ArchiveInfo:
        count = sum(1 for _ in self.list_entries())
        return ArchiveInfo(path=self.path, format=self._format, writable=False, entry_count=count)

    def set_password(self, password: str | None) -> None:
        super().set_password(password)
        if self._format == "7z":
            self._entries = None  # an encrypted index is readable now

    def list_entries(self) -> Iterator[ArchiveEntry]:
        if self._entries is None:
            self._entries = list(self._list())
        yield from self._entries

    def _list(self) -> Iterator[ArchiveEntry]:
        if self._format == "zip":
            yield from self._list_zip()
        elif self._format == "7z":
            yield from self._list_7z()
        elif self._format == "tar":
            yield from self._list_tar()
        else:
            yield from self._list_libarchive()

    def _entry(self, name: str, *, is_dir: bool, size: int, packed, modified, encrypted=False):  # noqa: ANN001, ANN202
        path = _norm(name)
        self._sources[path] = name
        return ArchiveEntry(
            path=path + ("/" if is_dir else ""),
            type=EntryType.DIRECTORY if is_dir else EntryType.FILE,
            size=int(size or 0),
            compressed_size=packed,
            modified=modified,
            is_encrypted=encrypted,
        )

    def _zip(self):  # noqa: ANN202
        try:
            import pyzipper

            archive = pyzipper.AESZipFile(io.BytesIO(self._data))
        except ImportError:
            archive = zipfile.ZipFile(io.BytesIO(self._data))
        if self._password:
            archive.setpassword(self._password.encode())
        return archive

    def _list_zip(self) -> Iterator[ArchiveEntry]:
        try:
            with self._zip() as archive:
                for info in archive.infolist():
                    try:
                        modified = datetime(*info.date_time)
                    except ValueError:
                        modified = None
                    yield self._entry(
                        info.filename,
                        is_dir=info.is_dir(),
                        size=info.file_size,
                        packed=info.compress_size,
                        modified=modified,
                        encrypted=bool(info.flag_bits & 0x1),
                    )
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"Not a valid zip archive: {self.path}: {exc}") from exc

    def _7z(self):  # noqa: ANN202
        import py7zr

        try:
            return py7zr.SevenZipFile(io.BytesIO(self._data), mode="r", password=self._password)
        except py7zr.PasswordRequired as exc:
            raise EncryptedArchiveError(f"Encrypted 7z needs a password: {self.path}") from exc
        except py7zr.Bad7zFile as exc:
            raise ArchiveError(f"Not a valid 7z archive: {self.path}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - a wrong password garbles the index
            if self._password is not None:
                raise EncryptedArchiveError(f"Wrong password: {self.path}") from exc
            raise ArchiveError(f"Could not read 7z archive: {self.path}: {exc}") from exc

    def _list_7z(self) -> Iterator[ArchiveEntry]:
        with self._7z() as archive:
            infos = archive.list()
            encrypted = bool(archive.needs_password())
        for info in infos:
            modified = getattr(info, "creationtime", None)
            yield self._entry(
                str(info.filename),
                is_dir=bool(info.is_directory),
                size=int(info.uncompressed or 0),
                packed=None,
                modified=modified if isinstance(modified, datetime) else None,
                encrypted=encrypted,
            )

    def _tar(self) -> tarfile.TarFile:
        return tarfile.open(fileobj=io.BytesIO(self._data), mode="r:*")

    def _list_tar(self) -> Iterator[ArchiveEntry]:
        with self._tar() as archive:
            for member in archive.getmembers():
                if not (member.isfile() or member.isdir()):
                    continue
                yield self._entry(
                    member.name,
                    is_dir=member.isdir(),
                    size=member.size,
                    packed=None,
                    modified=datetime.fromtimestamp(member.mtime) if member.mtime else None,
                )

    def _list_libarchive(self) -> Iterator[ArchiveEntry]:
        from archy.core.native import ensure_libarchive_env, libarchive_available

        if not libarchive_available():
            raise UnsupportedFormatError(f"This archive format needs libarchive: {self.path}")
        ensure_libarchive_env()
        import libarchive

        try:
            with libarchive.memory_reader(self._data, passphrase=self._password) as archive:
                for item in archive:
                    stamp = item.mtime
                    yield self._entry(
                        item.pathname,
                        is_dir=item.isdir,
                        size=item.size or 0,
                        packed=None,
                        modified=datetime.fromtimestamp(stamp) if stamp else None,
                    )
        except libarchive.ArchiveError as exc:
            raise ArchiveError(f"Could not read archive: {self.path}: {exc}") from exc

    # --------------------------------------------------------------- reading
    @property
    def fast_seek(self) -> bool:
        return self._format == "zip"

    def _read_uncached(
        self, entry: ArchiveEntry, *, password: str | None = None, max_bytes: int | None = None
    ) -> bytes:
        if entry.is_dir:
            return b""
        name = self._sources.get(entry.path.rstrip("/"), entry.path)
        data = self._read_member(name, password or self._password)
        return data[:max_bytes] if max_bytes is not None else data

    def _read_member(self, name: str, password: str | None) -> bytes:
        if self._format == "zip":
            try:
                with self._zip() as archive:
                    if password:
                        archive.setpassword(password.encode())
                    return archive.read(name)
            except RuntimeError as exc:  # encrypted, no or wrong password
                raise EncryptedArchiveError(f"Entry is encrypted: {name}") from exc
            except (KeyError, zipfile.BadZipFile) as exc:
                raise ArchiveError(f"Could not read {name}: {exc}") from exc
        if self._format == "7z":
            import py7zr
            from py7zr.io import BytesIOFactory

            with self._7z() as archive:
                try:
                    factory = BytesIOFactory(1 << 62)
                    archive.extract(targets=[name], factory=factory)
                except (py7zr.PasswordRequired, py7zr.exceptions.CrcError) as exc:
                    raise EncryptedArchiveError(f"Entry is encrypted: {name}") from exc
                product = factory.products.get(name)
                if product is None:
                    raise ArchiveError(f"Entry not found: {name}")
                return product.read()
        if self._format == "tar":
            with self._tar() as archive:
                handle = archive.extractfile(name)
                if handle is None:
                    raise ArchiveError(f"Entry not found: {name}")
                return handle.read()
        import libarchive

        with libarchive.memory_reader(self._data, passphrase=password) as archive:
            for item in archive:
                if item.pathname == name:
                    return b"".join(item.get_blocks())
        raise ArchiveError(f"Entry not found: {name}")

    def extract(
        self,
        entries: Iterable[ArchiveEntry],
        dest_dir: Path | str,
        *,
        on_progress: Callable[[int, int, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ):  # noqa: ANN201 - ExtractResult
        from archy.core.extraction import run_extract

        return run_extract(
            Path(dest_dir),
            entries,
            lambda entry: [self.read_bytes(entry, cache=False)],
            on_progress=on_progress,
            is_cancelled=is_cancelled,
        )
