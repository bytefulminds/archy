from __future__ import annotations


class ArchiveError(Exception):
    """Base class for all archive related errors."""


class UnsupportedFormatError(ArchiveError):
    """The file is not a supported archive format."""


class EncryptedArchiveError(ArchiveError):
    """The entry or archive is encrypted and no (correct) password was given."""


class ReadOnlyArchiveError(ArchiveError):
    """A write operation was attempted on a read-only backend."""
