from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    Qt,
)
from PySide6.QtGui import (
    QGuiApplication,
)

from archy.core import ArchiveError, EncryptedArchiveError, backend_for
from archy.core.backend import ArchiveBackend
from archy.core.tree import TreeNode, build_tree
from archy.i18n import tr
from archy.services.formatting import format_size
from archy.ui import soft_dialog
from archy.ui.main_window.constants import (
    open_filter,
)
from archy.ui.open_worker import OpenResult


class OpeningMixin:
    """Opens and closes archives, incl. the password flow.

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    # --------------------------------------------------------------- opening
    def open_archive_dialog(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path_str, _ = QFileDialog.getOpenFileName(self, tr("open.dialog_title"), "", open_filter())
        if path_str:
            self.open_archive(Path(path_str))

    def open_archive(self, path: Path) -> None:
        """Open ``path``. The index is read on a worker thread (see
        open_worker.py) while a spinner shows; the archive is shown in
        _on_open_finished."""
        if self._refuse_while_saving() or not self._confirm_discard_if_dirty():
            return
        try:
            backend = backend_for(path)
        except ArchiveError as exc:
            soft_dialog.message(self, tr("open.failed_title"), str(exc))
            return
        # A newer open supersedes one still running (its result is dropped).
        if self._opening is not None:
            self._opening[1].close()
        self._open_token += 1
        self._opening = (self._open_token, backend, path, False)
        self._busy.show_busy(tr("open.busy_title", name=path.name), tr("open.busy_detail"))
        self._set_status(tr("open.busy_title", name=path.name))
        self._open_worker.start(self._open_token, backend)

    @property
    def is_opening(self) -> bool:
        """True while an archive is being opened (tests and scripts wait on it)."""
        return self._opening is not None

    def _on_open_finished(self, result: OpenResult) -> None:
        opening = self._opening
        if opening is None or result.token != opening[0]:
            if opening is None or result.backend is not opening[1]:
                result.backend.close()  # a superseded open
            return
        _token, backend, path, encrypted = opening
        if result.needs_password:
            self._busy.hide_busy()
            pw = self._prompt_password(tr("password.prompt", name=path.name))
            if pw is None:
                self._opening = None
                backend.close()
                self._set_status(
                    tr("status.ready") if self._backend is None else self._archive_label
                )
                return
            backend.set_password(pw)
            self._open_token += 1
            self._opening = (self._open_token, backend, path, True)
            self._busy.show_busy(tr("open.busy_title", name=path.name), tr("open.busy_password"))
            self._open_worker.start(self._open_token, backend)
            return
        self._opening = None
        self._busy.hide_busy()
        if result.error is not None:
            backend.close()
            title = tr("open.unexpected_title" if result.unexpected else "open.failed_title")
            text = (
                tr("open.unexpected_text", error=result.error)
                if result.unexpected
                else result.error
            )
            soft_dialog.message(self, title, text)
            self._set_status(tr("status.ready") if self._backend is None else self._archive_label)
            return
        self._show_opened(path, backend, result, encrypted)

    def _show_opened(self, path: Path, backend: ArchiveBackend, result, encrypted: bool) -> None:  # noqa: ANN001
        entries = result.entries
        self._close_nested_levels()  # archives opened inside the previous one
        previous = self._backend
        self._req_id += 1  # a preview still loading from the previous archive stops
        self._backend = backend
        # Per-archive state: encryption, and the edit lock (always closed again).
        self._archive_encrypted = encrypted or any(e.is_encrypted for e in entries)
        self._edit_unlocked = False
        self._edit_profile = result.profile
        self._thumbs.set_backend(backend)
        self._prefetcher.set_backend(backend)
        if previous is not None and previous is not backend:
            previous.close()  # frees its folder buffer and encrypted temp files
        self._temp.configure_backend(backend)  # temp-file mode for the folder pass
        self._archive_label = path.name
        self._archive_items = sum(1 for e in entries if not e.is_dir)
        self._archive_bytes = sum(e.size for e in entries if not e.is_dir)
        root = result.root if result.root is not None else build_tree(entries)
        self._nav = [root]
        self._reset_history()  # back/forward start over in a new archive
        self._search_timer.stop()
        self._searching = False
        self._search_scope = "archive"
        for view in (self._grid, self._large):
            view.itemDelegate().set_search_mode(False)
        self._search.setPlaceholderText(tr("header.search"))
        if self._search.text():
            self._search.clear()
        start = self._settings.start_view
        self._view_group.button(start).setChecked(True)
        self._views.setCurrentIndex(start)
        self._refresh_folder()
        self._status_selection.setText(tr("status.no_selection"))

        self.setWindowTitle(tr("open.window_title", name=path.name))
        self._archive_name_label.setText(path.name)
        self._archive_chip.show()
        self._summary_items.setText(tr("count.items", count=self._archive_items))
        self._summary_size.setText(format_size(self._archive_bytes))
        self._set_status(tr("status.archive_files", name=path.name, count=self._archive_items))
        self._extract_action.setEnabled(True)
        self._extract_all_action.setEnabled(True)
        self._update_open_state()
        self._update_edit_actions()
        self._update_dirty_state()
        self._update_lock()
        self._update_archive_notice()

    def _prompt_password(self, message: str) -> str | None:
        return soft_dialog.prompt_text(
            self, tr("password.title"), message, password=True, ok_label=tr("password.unlock")
        )

    def _read_unlocking(self, node: TreeNode, *, max_bytes: int | None = None) -> bytes | None:
        """Read an entry synchronously, prompting for a password if needed.

        Returns bytes, or None if the user cancelled or the read failed (a
        placeholder is shown in that case).
        """
        assert self._backend is not None and node.entry is not None
        while True:
            QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                data = self._backend.read_bytes(node.entry, max_bytes=max_bytes)
                QGuiApplication.restoreOverrideCursor()
                return data
            except EncryptedArchiveError:
                QGuiApplication.restoreOverrideCursor()
                pw = self._prompt_password(tr("password.prompt", name=node.name))
                if pw is None:
                    self._show_placeholder(tr("preview.encrypted_no_password", name=node.name))
                    return None
                self._backend.set_password(pw)
                self._archive_encrypted = True
                self._update_lock()
            except ArchiveError as exc:
                QGuiApplication.restoreOverrideCursor()
                self._show_placeholder(tr("preview.read_failed", name=node.name, error=exc))
                return None

    def close_archive(self) -> None:
        if self._refuse_while_saving() or not self._confirm_discard_if_dirty():
            return
        # Make any preview still loading for this archive stale (it stops), and
        # stop its folder pass (close() does that), so the next archive opens
        # without waiting for them.
        self._req_id += 1
        self._close_nested_levels()
        if self._backend is not None:
            self._backend.close()
        self._backend = None
        # Free any temp files spilled for this archive's previews.
        self._temp.clear_preview()
        self._nav = []
        self._reset_history()
        self._search_timer.stop()
        self._searching = False
        self._search_scope = "archive"
        for view in (self._grid, self._large):
            view.itemDelegate().set_search_mode(False)
        self._search.setPlaceholderText(tr("header.search"))
        self._archive_encrypted = False
        self._edit_unlocked = False
        self._edit_profile = None
        self._archive_label = ""
        self._thumbs.set_backend(None)
        self._prefetcher.set_backend(None)
        self._model.set_folder(build_tree([]))
        self._rebuild_breadcrumb()
        self.setWindowTitle("Archy")
        self._archive_chip.hide()
        self._summary_items.setText(tr("count.items", count=0))
        self._summary_size.setText("0 B")
        if self._search.text():
            self._search.clear()
        self._show_placeholder(tr("preview.select_file"))
        self._set_status(tr("status.ready"))
        # Nothing of the closed archive stays in the status strip: no
        # selection, no folder or thumbnail progress (an extraction keeps its
        # own progress until it ends).
        self._status_selection.setText(tr("status.no_selection"))
        self._on_prefetch_done()
        self._on_thumb_progress(0, 0)
        self._extract_action.setEnabled(False)
        self._extract_all_action.setEnabled(False)
        self._update_counts()
        self._update_nav_state()
        self._update_open_state()
        self._update_edit_actions()
        self._update_lock()
        self._update_archive_notice()

    def refresh_current(self) -> None:
        if self._backend is None:
            return
        current = self._selection.currentIndex()
        node = self._node_at(current) if current.isValid() else None
        select_path = node.path if node is not None and not node.is_dir else None
        self._reload_preserving_location(select_path)

    def _update_open_state(self) -> None:
        has_archive = self._backend is not None
        self._refresh_button.setEnabled(has_archive)
        self._extract_menu_button.setEnabled(has_archive)
