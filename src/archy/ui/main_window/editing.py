from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QItemSelectionModel,
    Qt,
)
from PySide6.QtGui import (
    QGuiApplication,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QMenu,
)

from archy.core import ArchiveError
from archy.core.tree import build_tree
from archy.i18n import tr
from archy.ui import soft_dialog


class EditingMixin:
    """Add / delete / rename / save and drag-drop editing.

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    # ------------------------------------------------------------- editing
    def _can_edit(self) -> bool:
        """Writable, unlocked archive: file management and saving are allowed."""
        return (
            self._backend is not None
            and self._backend.writable
            and not self._is_locked()
            and not self._saving
        )

    def _current_prefix(self) -> str:
        """Path prefix of the folder currently shown (empty at the archive root)."""
        return self._nav[-1].path.rstrip("/") if self._nav else ""

    def _update_save_action(self) -> None:
        self._update_dirty_state()

    def _update_dirty_state(self) -> None:
        editor_dirty = self._editor is not None and self._editor.document().isModified()
        backend_dirty = self._backend is not None and self._backend.has_unsaved_changes
        dirty = self._can_edit() and (editor_dirty or backend_dirty)
        self._save_action.setEnabled(dirty)
        self._save_button.setVisible(dirty)
        self._update_title()

    def _update_title(self) -> None:
        base = tr("open.window_title", name=self._archive_label) if self._archive_label else "Archy"
        editor_dirty = self._editor is not None and self._editor.document().isModified()
        backend_dirty = self._backend is not None and self._backend.has_unsaved_changes
        self.setWindowTitle(f"{base}  •" if (editor_dirty or backend_dirty) else base)

    def _update_edit_actions(self) -> None:
        can = self._can_edit()
        nodes = self._selected_nodes() if self._backend is not None else []
        self._add_action.setEnabled(can)
        self._new_folder_action.setEnabled(can)
        self._delete_action.setEnabled(can and bool(nodes))
        self._rename_action.setEnabled(can and len(nodes) == 1)
        self._copy_path_action.setEnabled(bool(nodes))

    def _stage_editor(self) -> None:
        """Stage the text editor's current content into pending (uncommitted), so
        it is not lost on navigation and shows as an unsaved change."""
        if self._editor is None or self._editing_node is None or self._backend is None:
            return
        if not self._editor.document().isModified() or not self._can_edit():
            return
        text = self._editor.toPlainText()
        try:
            data = text.encode(self._edit_encoding)
        except (LookupError, UnicodeEncodeError):
            data = text.encode("utf-8")
        self._backend.write_bytes(self._editing_node.path, data)
        self._editor.document().setModified(False)

    def add_files_dialog(self) -> None:
        if not self._can_edit():
            return
        from PySide6.QtWidgets import QFileDialog

        paths, _ = QFileDialog.getOpenFileNames(self, tr("edit.add_dialog_title"))
        if paths:
            self._add_paths([Path(p) for p in paths])

    def create_folder(self) -> None:
        if self._backend is None or not self._can_edit():
            return
        name = soft_dialog.prompt_text(
            self,
            tr("edit.new_folder_title"),
            tr("edit.new_folder_label"),
            initial=tr("edit.new_folder_default"),
            ok_label=tr("edit.new_folder_ok"),
        )
        if not name:
            return
        if "/" in name or "\\" in name:
            soft_dialog.message(self, tr("edit.new_folder_title"), tr("edit.folder_bad_name"))
            return
        prefix = self._current_prefix()
        arc = f"{prefix}/{name}" if prefix else name
        existing_dirs = {
            e.path.rstrip("/") for e in self._backend.list_effective_entries() if e.is_dir
        }
        if arc in existing_dirs:
            soft_dialog.message(
                self, tr("edit.new_folder_title"), tr("edit.exists_here", name=name)
            )
            return
        self._backend.add_dir(arc)
        self._reload_preserving_location(None)
        self._update_dirty_state()
        self._update_edit_actions()
        self._set_status(tr("edit.folder_created", name=name))

    def _unique_arcname(self, arc: str, existing: set[str]) -> str:
        """A free name for a "keep both" add: test.txt -> test_1.txt -> test_2..."""
        from pathlib import PurePosixPath

        if arc not in existing:
            return arc
        parent = f"{arc.rsplit('/', 1)[0]}/" if "/" in arc else ""
        name = arc.rsplit("/", 1)[-1]
        p = PurePosixPath(name)
        stem, suffix = p.stem, p.suffix
        i = 1
        while True:
            candidate = f"{parent}{stem}_{i}{suffix}"
            if candidate not in existing:
                return candidate
            i += 1

    def _ask_conflict(self, arc: str) -> tuple[object | None, bool]:
        name = arc.rsplit("/", 1)[-1]
        return soft_dialog.confirm_with_toggle(
            self,
            tr("edit.conflict_title"),
            tr("edit.conflict_text", name=name),
            tr("edit.conflict_apply_all"),
            [
                (tr("button.cancel"), "cancel", False),
                (tr("edit.keep_both"), "keep", False),
                (tr("edit.overwrite"), "overwrite", True),
            ],
        )

    def _add_paths(self, paths: list[Path]) -> None:
        if self._backend is None or not self._can_edit():
            return
        prefix = self._current_prefix()
        jobs: list[tuple[Path, str]] = []
        for path in paths:
            if path.is_dir():
                for f in sorted(path.rglob("*")):
                    if f.is_file():
                        rel = f.relative_to(path.parent).as_posix()
                        jobs.append((f, f"{prefix}/{rel}" if prefix else rel))
            elif path.is_file():
                jobs.append((path, f"{prefix}/{path.name}" if prefix else path.name))
        if not jobs:
            return

        existing = {e.path for e in self._backend.list_effective_entries()}
        bulk_choice: object | None = None
        final: list[tuple[Path, str]] = []
        for source, arc in jobs:
            if arc in existing:
                choice = bulk_choice
                if choice is None:
                    choice, apply_all = self._ask_conflict(arc)
                    if apply_all and choice is not None:
                        bulk_choice = choice
                if choice is None or choice == "cancel":
                    continue  # skip this file
                if choice == "keep":
                    arc = self._unique_arcname(arc, existing)
                # "overwrite": arc stays the same (replaces the existing entry)
            final.append((source, arc))
            existing.add(arc)
        if not final:
            return

        added = 0
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for source, arc in final:
                self._backend.add(str(source), arc)
                added += 1
        except (ArchiveError, OSError) as exc:
            QGuiApplication.restoreOverrideCursor()
            soft_dialog.message(self, tr("edit.add_failed"), str(exc))
            return
        QGuiApplication.restoreOverrideCursor()
        if added:
            self._reload_preserving_location(None)
            self._update_dirty_state()
            self._update_edit_actions()
            self._set_status(tr("edit.files_added", count=added))

    def delete_selected(self) -> None:
        if self._backend is None or not self._can_edit():
            return
        nodes = self._selected_nodes()
        if not nodes:
            return
        effective = self._backend.list_effective_entries()
        to_remove: set[str] = set()
        for node in nodes:
            pre = node.path.rstrip("/")
            to_remove.add(node.path)
            if node.is_dir:
                for e in effective:
                    ep = e.path.rstrip("/")
                    if ep == pre or e.path.startswith(pre + "/"):
                        to_remove.add(e.path)
        for path in to_remove:
            self._backend.remove(path)
        self._reload_preserving_location(None)
        self._update_dirty_state()
        self._update_edit_actions()
        self._set_status(tr("edit.items_deleted", count=len(nodes)))

    def rename_selected(self) -> None:
        if self._backend is None or not self._can_edit():
            return
        nodes = self._selected_nodes()
        if len(nodes) != 1:
            return
        node = nodes[0]
        new_name = soft_dialog.prompt_text(
            self, tr("edit.rename_title"), tr("edit.rename_label"), initial=node.name
        )
        if not new_name or new_name == node.name:
            return
        if "/" in new_name or "\\" in new_name:
            soft_dialog.message(self, tr("edit.rename_title"), tr("edit.bad_name"))
            return
        stripped = node.path.rstrip("/")
        parent = stripped.rsplit("/", 1)[0] if "/" in stripped else ""
        new_path = f"{parent}/{new_name}" if parent else new_name
        try:
            if node.is_dir:
                old_pre = stripped
                for e in self._backend.list_effective_entries():
                    if e.is_dir:
                        continue
                    if e.path == old_pre or e.path.startswith(old_pre + "/"):
                        self._backend.rename(e.path, new_path + e.path[len(old_pre) :])
            else:
                self._backend.rename(node.path, new_path)
        except (ArchiveError, OSError) as exc:
            soft_dialog.message(self, tr("edit.rename_failed"), str(exc))
            return
        self._reload_preserving_location(None if node.is_dir else new_path)
        self._update_dirty_state()
        self._update_edit_actions()

    def copy_selected_path(self) -> None:
        nodes = self._selected_nodes()
        if not nodes:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText("\n".join("/" + n.path for n in nodes))
        self._set_status(tr("edit.path_copied"))

    def _show_context_menu(self, view: QAbstractItemView, pos) -> None:  # noqa: ANN001
        index = view.indexAt(pos)
        # Right-clicking an item selects it, but only when it is not already part
        # of the current (multi-)selection - so a right-click inside a multi-
        # selection keeps that selection intact and the menu acts on all of it.
        if index.isValid() and not self._selection.isSelected(index):
            self._selection.setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
        self._update_edit_actions()
        node = self._node_at(index) if index.isValid() else None
        menu = QMenu(self)
        if node is not None:
            if node.is_dir:
                menu.addAction(tr("menu.open_folder"), lambda: self._enter_folder(node))
            menu.addAction(self._extract_action)
            menu.addAction(self._copy_path_action)
        if self._can_edit():
            menu.addSeparator()
            menu.addAction(self._add_action)
            menu.addAction(self._new_folder_action)
            if node is not None:
                menu.addAction(self._rename_action)
                menu.addAction(self._delete_action)
        if not menu.isEmpty():
            menu.exec(view.viewport().mapToGlobal(pos))

    def _confirm_discard_if_dirty(self) -> bool:
        """Ask to save/discard when there are unsaved changes. Returns True to
        proceed (saved or discarded), False to cancel the pending action."""
        if self._backend is None:
            return True
        editor_dirty = self._editor is not None and self._editor.document().isModified()
        if not (self._backend.has_unsaved_changes or editor_dirty):
            return True
        resp = soft_dialog.confirm(
            self,
            tr("unsaved.title"),
            tr("unsaved.text"),
            [
                (tr("button.cancel"), "cancel", False),
                (tr("unsaved.discard"), "discard", False),
                (tr("button.save"), "save", True),
            ],
        )
        if resp == "save":
            self.save_archive(blocking=True)
            return not self._backend.has_unsaved_changes
        if resp == "discard":
            self._backend.discard_changes()
            if self._editor is not None:
                self._editor.document().setModified(False)
            return True
        return False

    # ------------------------------------------------------------- drag-in
    def dragEnterEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        paths = [
            Path(u.toLocalFile())
            for u in event.mimeData().urls()
            if u.isLocalFile() and u.toLocalFile()
        ]
        paths = [p for p in paths if p.exists()]
        if not paths:
            return
        if self._backend is None:
            self.open_archive(paths[0])
            event.acceptProposedAction()
            return
        if self._can_edit():
            self._add_paths(paths)
            event.acceptProposedAction()

    def _reload_preserving_location(self, select_path: str | None) -> None:
        if self._backend is None:
            return
        try:
            entries = self._backend.list_effective_entries()
        except ArchiveError as exc:
            soft_dialog.message(self, tr("edit.reload_failed"), str(exc))
            return

        self._thumbs.clear()
        root = build_tree(entries)
        names = [n.name for n in self._nav[1:]]
        self._nav = [root]
        current = root
        for name in names:
            child = current.children.get(name)
            if child is not None and child.is_dir:
                self._nav.append(child)
                current = child
            else:
                break
        self._refresh_folder()
        if select_path is not None:
            self._select_path(select_path)
