from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from archy.core.backend import ArchiveBackend
from archy.core.errors import ArchiveError, EncryptedArchiveError
from archy.core.tree import TreeNode, build_tree

# Opening an archive means reading its whole index. For an encrypted 7z with
# 100,000 files that is a few seconds of work, which on the GUI thread froze the
# window. The index is now read here, on a worker thread, together with the
# tree and (for 7z) the edit profile; the window shows a spinner meanwhile.


@dataclass
class OpenResult:
    token: int
    backend: ArchiveBackend
    entries: list = field(default_factory=list)
    root: TreeNode | None = None
    profile: object | None = None  # sevenzip_writer.EditProfile for 7z
    needs_password: bool = False
    error: str | None = None
    unexpected: bool = False


class OpenWorker(QObject):
    finished = Signal(object)  # OpenResult

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)  # a superseded open may still be finishing

    def start(self, token: int, backend: ArchiveBackend) -> None:
        self._pool.start(_OpenTask(self, token, backend))

    def wait(self, msecs: int = 30000) -> bool:
        return self._pool.waitForDone(msecs)


class _OpenTask(QRunnable):
    def __init__(self, owner: OpenWorker, token: int, backend: ArchiveBackend) -> None:
        super().__init__()
        self._owner = owner
        self._token = token
        self._backend = backend

    def run(self) -> None:
        result = OpenResult(token=self._token, backend=self._backend)
        try:
            result.entries = list(self._backend.list_entries())
            result.root = build_tree(result.entries)
            profile = getattr(self._backend, "edit_profile", None)
            if profile is not None and self._backend.password is not None:
                try:
                    result.profile = profile()
                except Exception:  # noqa: BLE001 - the lock just stays closed
                    result.profile = None
        except EncryptedArchiveError:
            result.needs_password = True
        except ArchiveError as exc:
            result.error = str(exc)
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected
            result.error = str(exc)
            result.unexpected = True
        self._owner.finished.emit(result)
