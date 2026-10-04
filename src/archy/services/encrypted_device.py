from __future__ import annotations

import threading

from PySide6.QtCore import QIODevice

from archy.core.backend import SeekableReader
from archy.core.secure_temp import Blob, EncryptedTempStore

# Read-only, seekable QIODevices that Qt's video player and PDF viewer read on
# demand, piece by piece, so a large file is never loaded whole into memory:
#
# - ReaderDevice: over any seekable reader, e.g. a stored file read straight
#   from a 7z archive (ArchiveBackend.open_stream), decrypted as it goes.
# - EncryptedBlobDevice: over one encrypted temp blob (a spilled file),
#   decrypted piece by piece, never written to disk as the raw file.
#
# Qt's FFmpeg backend reads the device from its own demuxer thread. The lock
# keeps a read and a seek from interleaving; decryption itself runs without the
# Python GIL (pycryptodomex C code), so playback never stalls the UI thread.


class ReaderDevice(QIODevice):
    def __init__(self, reader: SeekableReader, size: int, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._reader = reader
        self._size = size
        self._lock = threading.Lock()
        # Unbuffered: QIODevice would otherwise keep its own copy of what it
        # read ahead; the reader is cheap to call directly.
        self.open(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Unbuffered)

    def isSequential(self) -> bool:  # noqa: N802 - Qt override
        return False

    def size(self) -> int:
        return self._size

    def bytesAvailable(self) -> int:  # noqa: N802 - Qt override
        return max(0, self._size - self.pos()) + super().bytesAvailable()

    def readData(self, maxlen: int) -> bytes:  # noqa: N802 - Qt override
        with self._lock:
            try:
                self._reader.seek(self.pos())
                return self._reader.read(int(maxlen))
            except Exception:  # noqa: BLE001 - archive gone or damaged: end of data
                return b""

    def writeData(self, _data: bytes) -> int:  # noqa: N802 - Qt override
        return -1

    def close(self) -> None:
        with self._lock:
            self._reader.close()
        super().close()


class EncryptedBlobDevice(ReaderDevice):
    def __init__(self, store: EncryptedTempStore, blob: Blob, parent=None) -> None:  # noqa: ANN001
        super().__init__(store.open(blob), blob.size, parent)
