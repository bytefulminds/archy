from __future__ import annotations

import contextlib
import secrets
import shutil
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Encrypted temp storage: whatever Archy puts on disk (a folder unpacked to
# disk instead of RAM, a too-large video or PDF being previewed) is written
# ENCRYPTED, never as the raw file. Privacy is the point: nothing a user
# browses ever lands on disk in readable form.
#
# - AES-256 in CTR mode with a random key that exists only in this process's
#   memory (never written anywhere). After Archy closes - or crashes - leftover
#   files are unreadable noise, even when they were not cleaned up.
# - Every blob gets its own random nonce and a random file name (no archive
#   names, no extensions), so the temp folder reveals nothing but sizes.
# - CTR allows reading any byte range without decrypting what comes before it,
#   which a video player needs for seeking.
#
# pycryptodomex is already installed (pyzipper and py7zr depend on it); its C
# code runs without the Python GIL, so decrypting never stalls the UI thread.
#
# No integrity tag: these files are written and read back by the same process
# within one session; confidentiality is what matters here.

_BLOCK = 16
_NONCE_BYTES = 8  # CTR: 8-byte nonce + 8-byte block counter
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Blob:
    """One encrypted file in the store."""

    path: Path
    nonce: bytes
    size: int  # plaintext size


class EncryptedTempStore:
    """A per-session, encrypted scratch area on disk. Thread-safe."""

    def __init__(self, base_dir: Callable[[], Path] | Path | None = None) -> None:
        self._key = secrets.token_bytes(32)
        self._base_dir = base_dir
        self._root: Path | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ location
    def _base(self) -> Path:
        base = self._base_dir() if callable(self._base_dir) else self._base_dir
        return Path(base) if base is not None else Path(tempfile.gettempdir())

    def base_dir(self) -> Path:
        return self._base()

    def in_use(self) -> bool:
        """True while any blob of this store is still on disk."""
        with self._lock:
            root = self._root
        return root is not None and root.exists() and any(root.iterdir())

    def root(self) -> Path:
        """The store's own directory (created on first use)."""
        with self._lock:
            if self._root is None or not self._root.exists():
                self._root = Path(tempfile.mkdtemp(prefix="archy-", dir=str(self._base())))
            return self._root

    def free_disk(self) -> int:
        try:
            return shutil.disk_usage(self.root()).free
        except OSError:
            return 0

    # --------------------------------------------------------------- write
    def _cipher(self, nonce: bytes, block: int = 0):  # noqa: ANN202
        from Cryptodome.Cipher import AES

        return AES.new(self._key, AES.MODE_CTR, nonce=nonce, initial_value=block)

    def writer(self) -> BlobWriter:
        """Stream a blob in: write() chunks, then close() for the Blob."""
        return BlobWriter(self, self.root() / secrets.token_hex(16))

    def put(self, data: bytes) -> Blob:
        writer = self.writer()
        try:
            view = memoryview(data)
            for start in range(0, len(view), _CHUNK):
                writer.write(view[start : start + _CHUNK])
            return writer.close()
        except BaseException:
            writer.abort()
            raise

    # ---------------------------------------------------------------- read
    def read(self, blob: Blob, offset: int = 0, size: int | None = None) -> bytes:
        """Decrypt ``size`` bytes from ``offset`` (to the end when None)."""
        with self.open(blob) as reader:
            reader.seek(offset)
            return reader.read(size)

    def open(self, blob: Blob) -> BlobReader:
        """A seekable reader over one blob (one per reading thread)."""
        return BlobReader(self, blob)

    # ------------------------------------------------------------- cleanup
    def delete(self, blob: Blob) -> None:
        # Windows: fails while a reader still has it open; clear() gets it later.
        with contextlib.suppress(OSError):
            blob.path.unlink(missing_ok=True)

    def clear(self) -> None:
        """Remove everything this store wrote (best-effort)."""
        with self._lock:
            root, self._root = self._root, None
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)


class BlobWriter:
    def __init__(self, store: EncryptedTempStore, path: Path) -> None:
        self._store = store
        self._path = path
        self._nonce = secrets.token_bytes(_NONCE_BYTES)
        self._cipher = store._cipher(self._nonce)
        self._size = 0
        self._fh = open(path, "wb")  # noqa: SIM115 - closed in close()/abort()

    def write(self, chunk: bytes | memoryview) -> None:
        if chunk:
            self._fh.write(self._cipher.encrypt(bytes(chunk)))
            self._size += len(chunk)

    def close(self) -> Blob:
        self._fh.close()
        return Blob(self._path, self._nonce, self._size)

    def abort(self) -> None:
        try:
            self._fh.close()
        finally:
            self._path.unlink(missing_ok=True)


class BlobReader:
    """Random-access decrypting reader. Not shared between threads."""

    def __init__(self, store: EncryptedTempStore, blob: Blob) -> None:
        self._store = store
        self._blob = blob
        self._fh = open(blob.path, "rb")  # noqa: SIM115 - closed in close()
        self._pos = 0

    @property
    def size(self) -> int:
        return self._blob.size

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        """File-like seek (whence 0/1/2), so libraries such as PyAV can read a
        blob directly."""
        base = {0: 0, 1: self._pos, 2: self._blob.size}.get(whence, 0)
        self._pos = max(0, min(base + int(offset), self._blob.size))
        return self._pos

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def read(self, size: int | None = None) -> bytes:
        remaining = self._blob.size - self._pos
        if size is None or size < 0 or size > remaining:
            size = remaining
        if size <= 0:
            return b""
        block, skip = divmod(self._pos, _BLOCK)
        self._fh.seek(block * _BLOCK)
        cipher = self._store._cipher(self._blob.nonce, block)
        data = cipher.decrypt(self._fh.read(skip + size))[skip : skip + size]
        self._pos += len(data)
        return data

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> BlobReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def disk_reserve() -> int:
    """Free disk space that the store always leaves alone (2 GB)."""
    return 2 * 1024 * 1024 * 1024


__all__ = ["Blob", "BlobReader", "BlobWriter", "EncryptedTempStore", "disk_reserve"]
