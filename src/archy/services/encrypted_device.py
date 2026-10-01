from __future__ import annotations

import threading

from PySide6.QtCore import QIODevice

from archy.core.secure_temp import Blob, EncryptedTempStore

# A read-only, seekable QIODevice over one encrypted temp blob, so Qt's video
# player and PDF viewer read a spilled file straight from the encrypted store:
# decrypted piece by piece as they ask for it, never written to disk as the raw
# file and never loaded whole into memory.
#
# Qt's FFmpeg backend reads the device from its own demuxer thread. The lock
# keeps a read and a seek from interleaving; decryption itself runs without the
# Python GIL (pycryptodomex C code), so playback never stalls the UI thread.


class EncryptedBlobDevice(QIODevice):
    def __init__(self, store: EncryptedTempStore, blob: Blob, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._reader = store.open(blob)
        self._size = blob.size
        self._lock = threading.Lock()
        # Unbuffered: QIODevice would otherwise keep its own copy of what it
        # read ahead; the decrypting reader is cheap to call directly.
        self.open(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Unbuffered)

    def isSequential(self) -> bool:  # noqa: N802 - Qt override
        return False

    def size(self) -> int:
        return self._size

    def bytesAvailable(self) -> int:  # noqa: N802 - Qt override
        return max(0, self._size - self.pos()) + super().bytesAvailable()

    def readData(self, maxlen: int) -> bytes:  # noqa: N802 - Qt override
        with self._lock:
            self._reader.seek(self.pos())
            return self._reader.read(int(maxlen))

    def writeData(self, _data: bytes) -> int:  # noqa: N802 - Qt override
        return -1

    def close(self) -> None:
        with self._lock:
            self._reader.close()
        super().close()
