from __future__ import annotations

from pathlib import Path

from archy.core.backend import ArchiveBackend
from archy.core.detect import detect_format
from archy.core.errors import ArchiveError, UnsupportedFormatError
from archy.i18n import tr

# Choose a backend for a given file. Prefer a format-specific backend when we
# have one: zip (zipfile), 7z (py7zr), and tar/tar.gz/tar.bz2/tar.xz (stdlib
# tarfile). All of those work on every platform with no native dependency. The
# broad libarchive reader handles everything else (rar-read, cpio, iso, cab,
# xar, ar, bare gzip/bzip2/xz, ...) - but only when libarchive is available on
# the system. libarchive is optional (it is painful to ship on Windows), so when
# it is absent those formats are reported as unsupported rather than failing
# obscurely. Opening never writes to disk; it only inspects the file to pick the
# right reader.

_ZIP_FORMATS = {"zip"}
_SEVENZIP_FORMATS = {"7z"}
# Formats that MIGHT be a tar the stdlib can read: an uncompressed tar, or a
# gzip/bzip2/xz stream (which is either a compressed tar or a bare compressed
# file). detect_format reports a compressed tar by its compression magic
# ("gzip"/"bzip2"/"xz"), so we confirm with tarfile.is_tarfile before routing to
# the TarBackend; a bare compressed file falls through to libarchive.
_TARFILE_CANDIDATES = {"tar", "tar.gz", "tar.bz2", "tar.xz", "gzip", "bzip2", "xz"}


def backend_for(path: Path | str) -> ArchiveBackend:
    """Return an unopened backend suited to the archive at ``path``.

    Raises ArchiveError if the file does not exist, and UnsupportedFormatError if
    the format needs libarchive and libarchive is not available here. Format
    detection is a hint; the backend validates the archive when its entries are
    read.
    """
    path = Path(path)
    if not path.exists():
        raise ArchiveError(f"File does not exist: {path}")
    if not path.is_file():
        raise ArchiveError(f"Not a file: {path}")

    fmt = detect_format(path)

    if fmt in _ZIP_FORMATS:
        from archy.core.backends.zip_backend import ZipBackend

        return ZipBackend(path)
    if fmt in _SEVENZIP_FORMATS:
        from archy.core.backends.sevenzip_backend import SevenZipBackend

        return SevenZipBackend(path)
    if fmt in _TARFILE_CANDIDATES:
        import tarfile

        # is_tarfile transparently detects gzip/bzip2/xz-compressed tars and
        # returns False for a bare compressed file (which needs libarchive).
        try:
            is_tar = tarfile.is_tarfile(path)
        except OSError:
            is_tar = False
        if is_tar:
            from archy.core.backends.tar_backend import TarBackend

            return TarBackend(path)

    # Everything else needs libarchive. Use it only if it can actually be loaded.
    from archy.core.native import libarchive_available

    if not libarchive_available():
        raise UnsupportedFormatError(
            tr("error.needs_libarchive", format=fmt or tr("error.unknown_format"))
        )

    from archy.core.backends.libarchive_backend import LibArchiveBackend

    return LibArchiveBackend(path)
