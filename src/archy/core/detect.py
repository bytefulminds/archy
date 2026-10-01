from __future__ import annotations

from pathlib import Path

# Magic-byte signatures checked at the start of the file.
# libarchive does the authoritative detection when an archive is opened; this
# is a cheap first pass for the UI (icons, "is this openable at all").
_MAGIC_AT_START: tuple[tuple[bytes, str], ...] = (
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),  # empty zip
    (b"PK\x07\x08", "zip"),  # spanned zip
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"Rar!\x1a\x07\x00", "rar"),  # RAR 1.5 - 4.x
    (b"Rar!\x1a\x07\x01\x00", "rar"),  # RAR 5.0+
    (b"\x1f\x8b", "gzip"),
    (b"BZh", "bzip2"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
    (b"MSCF", "cab"),
)

# "ustar" marker lives at offset 257 in a tar header.
_TAR_MAGIC_OFFSET = 257
_TAR_MAGIC = b"ustar"

# Extension fallback when magic bytes are inconclusive.
_EXTENSION_MAP: dict[str, str] = {
    ".zip": "zip",
    ".7z": "7z",
    ".rar": "rar",
    ".tar": "tar",
    ".gz": "gzip",
    ".tgz": "tar.gz",
    ".bz2": "bzip2",
    ".tbz2": "tar.bz2",
    ".xz": "xz",
    ".txz": "tar.xz",
    ".zst": "zstd",
    ".cab": "cab",
    ".iso": "iso",
}


def detect_format(path: Path | str) -> str | None:
    """Best-effort format detection by magic bytes, then extension.

    Returns a short format name (e.g. "zip", "7z", "tar") or None if nothing
    matched. This is a hint, not a guarantee; opening the archive is what
    truly validates it.
    """
    path = Path(path)
    try:
        with path.open("rb") as fh:
            head = fh.read(512)
    except OSError:
        return _by_extension(path)

    for signature, name in _MAGIC_AT_START:
        if head.startswith(signature):
            return name

    if head[_TAR_MAGIC_OFFSET : _TAR_MAGIC_OFFSET + len(_TAR_MAGIC)] == _TAR_MAGIC:
        return "tar"

    return _by_extension(path)


def _by_extension(path: Path) -> str | None:
    # Handle double extensions like ".tar.gz" first.
    lower = path.name.lower()
    for double in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst"):
        if lower.endswith(double):
            return double.lstrip(".")
    return _EXTENSION_MAP.get(path.suffix.lower())


def is_probably_archive(path: Path | str) -> bool:
    """True if the file looks like a supported archive."""
    return detect_format(path) is not None
