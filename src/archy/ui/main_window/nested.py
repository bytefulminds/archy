from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from archy.core import memory
from archy.core.backend import ArchiveBackend
from archy.core.backends.nested_backend import NestedBackend, looks_nested
from archy.core.errors import ArchiveError, EncryptedArchiveError
from archy.core.tree import TreeNode, build_tree
from archy.i18n import tr
from archy.services.formatting import format_size
from archy.ui import soft_dialog


@dataclass
class _Level:
    """An archive the user went into another archive from: what to come back
    to."""

    backend: ArchiveBackend
    label: str
    root: TreeNode
    folder: tuple[str, ...]
    history: list[tuple[str, ...]] = field(default_factory=list)
    history_index: int = -1
    encrypted: bool = False
    profile: object | None = None
    items: int = 0
    size: int = 0


class NestedMixin:
    """Archives inside an archive (experimental): a zip in a 7z backup opens
    like a folder, from memory and read-only. The breadcrumb shows the way
    back; Close closes all of them.

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    def _is_nested_candidate(self, node: TreeNode) -> bool:
        return not node.is_dir and node.entry is not None and looks_nested(node.name)

    def _open_nested(self, node: TreeNode) -> None:
        if self._backend is None or node.entry is None:
            return
        # Read into memory whole (never to disk): it has to fit comfortably.
        if node.size > memory.spare() // 3:
            soft_dialog.message(
                self,
                tr("nested.title"),
                tr("nested.too_big", name=node.name, size=format_size(node.size)),
            )
            return
        data = self._read_unlocking(node)
        if data is None:
            return
        backend = NestedBackend(data, node.name)
        entries = self._list_nested(backend, node.name)
        if entries is None:
            return

        self._outer_levels.append(
            _Level(
                backend=self._backend,
                label=self._archive_label,
                root=self._nav[0],
                folder=tuple(n.name for n in self._nav[1:]),
                history=list(self._history),
                history_index=self._history_index,
                encrypted=self._archive_encrypted,
                profile=self._edit_profile,
                items=self._archive_items,
                size=self._archive_bytes,
            )
        )
        self._req_id += 1
        self._show_level(
            backend,
            node.name,
            build_tree(entries),
            encrypted=any(e.is_encrypted for e in entries) or backend._password is not None,
            items=sum(1 for e in entries if not e.is_dir),
            size=sum(e.size for e in entries if not e.is_dir),
        )
        self._set_status(tr("nested.status", name=node.name))

    def _list_nested(self, backend: NestedBackend, name: str) -> list | None:
        """The entries of a nested archive, asking for its password when needed;
        None when it cannot be read (a message says why)."""
        while True:
            QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            error: Exception | None = None
            try:
                return list(backend.list_entries())
            except EncryptedArchiveError as exc:
                error = exc
            except (ArchiveError, OSError) as exc:
                error = exc
            finally:
                QGuiApplication.restoreOverrideCursor()
            if not isinstance(error, EncryptedArchiveError):
                soft_dialog.message(self, tr("nested.title"), tr("nested.failed", error=error))
                return None
            password = self._prompt_password(tr("password.prompt", name=name))
            if password is None:
                return None
            backend.set_password(password)

    def _show_level(
        self,
        backend: ArchiveBackend,
        label: str,
        root: TreeNode,
        *,
        folder: tuple[str, ...] = (),
        encrypted: bool = False,
        profile: object | None = None,
        items: int = 0,
        size: int = 0,
        history: list[tuple[str, ...]] | None = None,
        history_index: int = -1,
    ) -> None:
        """Make ``backend`` the archive shown (without closing the others)."""
        self._backend = backend
        self._archive_label = label
        self._archive_encrypted = encrypted
        self._edit_unlocked = False
        self._edit_profile = profile
        self._archive_items = items
        self._archive_bytes = size
        self._thumbs.set_backend(backend)
        self._prefetcher.set_backend(backend)
        self._temp.configure_backend(backend)
        nav = [root]
        for name in folder:
            child = nav[-1].children.get(name)
            if child is None or not child.is_dir:
                break
            nav.append(child)
        self._nav = nav
        if history is None:
            self._reset_history()
        else:
            self._history = list(history)
            self._history_index = history_index
            self._history_moving = True
        try:
            self._refresh_folder()
        finally:
            self._history_moving = False
        self._archive_name_label.setText(self._level_title())
        self._summary_items.setText(tr("count.items", count=items))
        self._summary_size.setText(format_size(size))
        self._update_open_state()
        self._update_edit_actions()
        self._update_lock()
        self._update_archive_notice()

    def _level_title(self) -> str:
        labels = [level.label for level in self._outer_levels] + [self._archive_label]
        return " › ".join(labels)

    def _leave_nested(self, depth: int, folder: tuple[str, ...] | None = None) -> None:
        """Back to the archive at ``depth`` (0 = the one opened from disk),
        closing the archives opened inside it."""
        if not 0 <= depth < len(self._outer_levels):
            return
        inner = self._backend
        target = self._outer_levels[depth]
        closing = [level.backend for level in self._outer_levels[depth + 1 :]]
        del self._outer_levels[depth:]
        self._req_id += 1
        self._show_level(
            target.backend,
            target.label,
            target.root,
            folder=target.folder if folder is None else folder,
            encrypted=target.encrypted,
            profile=target.profile,
            items=target.items,
            size=target.size,
            history=target.history,
            history_index=target.history_index,
        )
        for backend in [*closing, inner]:
            if backend is not None and backend is not target.backend:
                backend.close()
        self._set_status(tr("status.ready"))

    def _close_nested_levels(self) -> None:
        """Closing the archive closes the ones it was opened from as well."""
        for level in self._outer_levels:
            if level.backend is not self._backend:
                level.backend.close()
        self._outer_levels.clear()
