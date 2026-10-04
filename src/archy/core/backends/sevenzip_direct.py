from __future__ import annotations

import threading
import zlib
from dataclasses import dataclass
from pathlib import Path

from archy.core.errors import ArchiveError, EncryptedArchiveError

# Direct reads from a 7z made with "Store" (no compression), encrypted or not.
#
# py7zr reads an entry by walking the archive's index (every one of its files,
# for every read: about a quarter second per click on a 100,000-file backup)
# and then decoding its block from the start. A stored block needs neither: the
# bytes of a file sit in the archive as they are, at an offset the index gives.
# With 7-Zip's AES-256 they are encrypted in CBC mode, which decrypts from any
# 16-byte boundary given the 16 encrypted bytes before it. So a stored file can
# be read at any offset with one seek: a photo straight away, and a video can
# play while it is read (see open_stream) instead of being loaded whole first.
#
# Only blocks made of Copy and 7zAES coders qualify; anything compressed keeps
# going through py7zr. This module only reads the index py7zr already parsed.

_COPY = b"\x00"
_AES = b"\x06\xf1\x07\x01"
_CHUNK = 8 * 1024 * 1024  # read + decrypt in pieces this size
_BLOCK = 16  # AES block size


@dataclass(frozen=True, slots=True)
class Span:
    """Where one stored file lives in the archive."""

    start: int  # absolute offset of the block's packed data in the archive
    offset: int  # offset of this file within the block's (decrypted) data
    size: int
    crc: int | None
    aes: bytes | None  # 7zAES coder properties, None when not encrypted


class StoreIndex:
    """Spans of all files in stored (Copy/AES) blocks, by archive name."""

    def __init__(self, spans: dict[str, Span], complete: bool, compressed_files: int = 0) -> None:
        self._spans = spans
        # True when every file with data can be read directly.
        self.complete = complete
        # Files with data in compressed blocks (they go through py7zr).
        self.compressed_files = compressed_files

    def __len__(self) -> int:
        return len(self._spans)

    def span(self, name: str) -> Span | None:
        return self._spans.get(name)

    def smallest_encrypted(self) -> tuple[str, Span] | None:
        candidates = [(s.size, n) for n, s in self._spans.items() if s.aes is not None and s.size]
        if not candidates:
            return None
        _size, name = min(candidates)
        return name, self._spans[name]

    @classmethod
    def build(cls, archive: object) -> StoreIndex:
        """From an open py7zr.SevenZipFile (its parsed header)."""
        spans: dict[str, Span] = {}
        complete = True
        header = archive.header  # type: ignore[attr-defined]
        streams = getattr(header, "main_streams", None)
        if streams is None:
            return cls(spans, complete=True)  # only empty files and folders
        pack = streams.packinfo
        base = int(archive.afterheader) + int(pack.packpos)  # type: ignore[attr-defined]
        positions = list(pack.packpositions)
        stream_index = 0
        compressed_files = 0
        for folder in streams.unpackinfo.folders:
            packed = int(getattr(folder, "num_packedstreams", None) or len(folder.packed_indices))
            start = base + positions[stream_index]
            stream_index += packed
            aes = _stored_folder_aes(folder, packed)
            files = list(getattr(folder, "files", None) or ())
            if aes is False:
                with_data = sum(1 for f in files if not f.emptystream)
                compressed_files += with_data
                complete = complete and not with_data
                continue
            offset = 0
            for f in files:
                if f.emptystream:
                    continue
                size = int(f.uncompressed or 0)
                name = str(f.filename).replace("\\", "/")
                crc = f.crc32
                spans[name] = Span(start, offset, size, None if crc is None else int(crc), aes)
                offset += size
        return cls(spans, complete=complete, compressed_files=compressed_files)


def _stored_folder_aes(folder: object, packed: int) -> bytes | None | bool:
    """The AES properties of a stored block, None for plain Copy, or False
    when the block is compressed (or otherwise not a simple chain)."""
    coders = list(getattr(folder, "coders", None) or ())
    if packed != 1 or not coders or len(coders) > 2:
        return False
    aes: bytes | None = None
    for coder in coders:
        if coder.get("numinstreams", 1) != 1 or coder.get("numoutstreams", 1) != 1:
            return False
        method = bytes(coder.get("method") or b"")
        if method == _COPY:
            continue
        if method == _AES and aes is None:
            aes = bytes(coder.get("properties") or b"")
            continue
        return False
    return aes


def _aes_parameters(properties: bytes) -> tuple[int, bytes, bytes]:
    """(cycles power, salt, 16-byte IV) from 7zAES coder properties."""
    if not properties:
        raise ArchiveError("Missing 7zAES properties")
    first = properties[0]
    cycles = first & 0x3F
    if first & 0xC0 == 0:
        return cycles, b"", bytes(_BLOCK)
    second = properties[1]
    salt_size = ((first >> 7) & 1) + (second >> 4)
    iv_size = ((first >> 6) & 1) + (second & 0x0F)
    salt = properties[2 : 2 + salt_size]
    iv = properties[2 + salt_size : 2 + salt_size + iv_size]
    return cycles, bytes(salt), bytes(iv).ljust(_BLOCK, b"\0")


def _key(password: str, cycles: int, salt: bytes) -> bytes:
    # py7zr's key derivation; Archy caches it (see _install_key_cache).
    from py7zr.compressor import calculate_key

    return calculate_key(password.encode("utf-16LE"), cycles, salt, "sha256")


class _Cipher:
    """Decrypts one stored block from any offset."""

    def __init__(self, properties: bytes, password: str | None) -> None:
        if password is None:
            raise EncryptedArchiveError("This file is encrypted; a password is needed")
        cycles, salt, iv = _aes_parameters(properties)
        self._key = _key(password, cycles, salt)
        self._iv = iv

    def decryptor(self, previous_block: bytes | None):  # noqa: ANN202
        from Cryptodome.Cipher import AES

        return AES.new(self._key, AES.MODE_CBC, previous_block or self._iv)


def read_span(
    fh,  # noqa: ANN001 - an open binary file of the archive
    span: Span,
    password: str | None,
    offset: int = 0,
    length: int | None = None,
) -> bytes:
    """``length`` bytes (default: to the end) of the file at ``offset``."""
    offset = max(0, min(offset, span.size))
    end = span.size if length is None else min(span.size, offset + max(0, length))
    if end <= offset:
        return b""
    first = span.offset + offset  # within the block's data
    last = span.offset + end
    if span.aes is None:
        fh.seek(span.start + first)
        return _read_exact(fh, last - first)

    cipher = _Cipher(span.aes, password)
    aligned = first - first % _BLOCK
    previous = None
    if aligned:
        fh.seek(span.start + aligned - _BLOCK)
        previous = _read_exact(fh, _BLOCK)
    else:
        fh.seek(span.start)
    decrypt = cipher.decryptor(previous).decrypt
    wanted = last - aligned
    padded = wanted + (-wanted % _BLOCK)
    # One buffer, read into and decrypted in place: no per-chunk copies (the
    # copies hold the GIL, which the UI thread needs).
    out = bytearray(padded)
    view = memoryview(out)
    for start in range(0, padded, _CHUNK):
        piece = view[start : start + _CHUNK]
        if fh.readinto(piece) != len(piece):
            raise ArchiveError("The archive ends unexpectedly (truncated or damaged)")
        decrypt(piece, output=piece)
    skip = first - aligned
    size = end - offset
    if skip == 0 and size == padded:
        return bytes(out)
    return bytes(view[skip : skip + size])


def _read_exact(fh, size: int) -> bytes:  # noqa: ANN001
    data = fh.read(size)
    if len(data) != size:
        raise ArchiveError("The archive ends unexpectedly (truncated or damaged)")
    return data


def read_whole(path: str | Path, span: Span, password: str | None) -> bytes:
    """The whole file, checked against its CRC. A mismatch on an encrypted
    file means the password is wrong."""
    with open(path, "rb") as fh:
        data = read_span(fh, span, password)
    if span.crc is not None and zlib.crc32(data) != span.crc:
        if span.aes is not None:
            raise EncryptedArchiveError("The password is wrong (checksum mismatch)")
        raise ArchiveError("Checksum mismatch: the archive is damaged")
    return data


def copy_to(
    path: str | Path,
    span: Span,
    password: str | None,
    sink,  # noqa: ANN001 - called with each chunk
    is_cancelled=None,  # noqa: ANN001
) -> None:
    """Hand the whole file to ``sink`` in chunks, checked against its CRC at
    the end; never holds more than one chunk."""
    crc = 0
    with open(path, "rb") as fh:
        position = 0
        while position < span.size:
            if is_cancelled is not None and is_cancelled():
                raise ArchiveError("Cancelled")
            chunk = read_span(fh, span, password, position, _CHUNK)
            crc = zlib.crc32(chunk, crc)
            sink(chunk)
            position += len(chunk)
    if span.crc is not None and crc != span.crc:
        if span.aes is not None:
            raise EncryptedArchiveError("The password is wrong (checksum mismatch)")
        raise ArchiveError("Checksum mismatch: the archive is damaged")


class SpanStream:
    """A seekable, read-only file over one stored file, for players that read
    on demand (video, PDF). Keeps its own handle on the archive; thread-safe."""

    def __init__(self, path: str | Path, span: Span, password: str | None) -> None:
        if span.aes is not None and password is None:
            raise EncryptedArchiveError("This file is encrypted; a password is needed")
        self._fh = open(path, "rb")  # noqa: SIM115 - closed by close()
        self._span = span
        self._password = password
        self._pos = 0
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        return self._span.size

    def seek(self, offset: int, whence: int = 0) -> int:
        with self._lock:
            if whence == 1:
                offset += self._pos
            elif whence == 2:
                offset += self._span.size
            self._pos = max(0, offset)
            return self._pos

    def tell(self) -> int:
        return self._pos

    def read(self, size: int = -1) -> bytes:
        with self._lock:
            if self._fh.closed:
                return b""
            length = None if size is None or size < 0 else size
            data = read_span(self._fh, self._span, self._password, self._pos, length)
            self._pos += len(data)
            return data

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def close(self) -> None:
        with self._lock:
            self._fh.close()

    def __enter__(self) -> SpanStream:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
