"""Core archive model - no Qt dependency.

Reading never writes to disk: entry bytes are decompressed straight into
memory. Only an explicit save of an edited archive touches the disk, via a
temp file plus atomic replace.
"""

from archy.core.backend import (
    ArchiveBackend,
    ArchiveEntry,
    ArchiveError,
    ArchiveInfo,
    EncryptedArchiveError,
    EntryType,
    ReadOnlyArchiveError,
    UnsupportedFormatError,
)
from archy.core.detect import detect_format, is_probably_archive
from archy.core.factory import backend_for

__all__ = [
    "ArchiveBackend",
    "ArchiveEntry",
    "ArchiveError",
    "ArchiveInfo",
    "EncryptedArchiveError",
    "EntryType",
    "ReadOnlyArchiveError",
    "UnsupportedFormatError",
    "backend_for",
    "detect_format",
    "is_probably_archive",
]
