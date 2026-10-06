from __future__ import annotations

import shutil
from html import escape as html_escape
from pathlib import Path

from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtGui import QGuiApplication

from archy.core import ArchiveError, EncryptedArchiveError
from archy.i18n import tr
from archy.services.formatting import format_size
from archy.ui import soft_dialog, theme
from archy.ui.extract_dialog import SaveProgressDialog

# The edit lock. Archives Archy only reads show a lock next to their name, with
# a tooltip that says why. An encrypted 7z made with Store (no compression)
# can be unlocked on purpose: one click, a dialog that explains what saving
# will do, and editing is allowed for this session. Saving then writes a new,
# equally encrypted version next to the original and replaces the original
# only after checking it (core/backends/sevenzip_writer.py). An encrypted,
# compressed 7z stays locked: rewriting it means compressing everything again,
# which takes hours on a large archive.
#
# Saving a 7z runs on a worker thread with a progress window (SaveWorker);
# that window only appears when the save takes longer than a moment.

_SAVE_DIALOG_DELAY_MS = 600

LOCKED = "locked"  # read-only, can be unlocked
FIXED = "fixed"  # read-only, cannot be unlocked
UNLOCKED = "unlocked"  # was locked, editing allowed for this session
NONE = "none"  # freely editable (no lock shown)


def _qt(html: str) -> str:
    """Rich-text tooltip that wraps at a readable width."""
    return f"<qt><div style='max-width:360px'>{html}</div></qt>"


class LockMixin:
    """The edit lock and the (background) save.

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    # ------------------------------------------------------------ the lock
    def _is_locked(self) -> bool:
        """Editing/saving is off for encrypted archives until unlocked."""
        encrypted = self._archive_encrypted or (
            self._backend is not None and self._backend.password is not None
        )
        return encrypted and not self._edit_unlocked

    def _lock_profile(self):  # noqa: ANN202 - EditProfile | None
        backend = self._backend
        if self._edit_profile is None and backend is not None and backend.password is not None:
            probe = getattr(backend, "edit_profile", None)
            if probe is not None:
                try:
                    self._edit_profile = probe()
                except Exception:  # noqa: BLE001 - stays locked
                    self._edit_profile = None
        return self._edit_profile

    def _lock_state(self) -> tuple[str, str, str]:
        """(state, heading, explanation) for the lock next to the archive name."""
        backend = self._backend
        if backend is None:
            return NONE, "", ""
        if getattr(backend, "nested", False):
            return FIXED, tr("lock.read_only"), tr("lock.nested_text")
        if not backend.writable:
            kind = Path(self._archive_label).suffix.lstrip(".").upper() or tr("lock.format_this")
            return FIXED, tr("lock.read_only"), tr("lock.format_readonly", kind=kind)
        encrypted = self._archive_encrypted or backend.password is not None
        if not encrypted:
            return NONE, "", ""
        if self._edit_unlocked:
            return (
                UNLOCKED,
                tr("lock.unlocked_heading"),
                tr("lock.unlocked_text"),
            )
        if not hasattr(backend, "edit_profile"):
            return (
                FIXED,
                tr("lock.read_only"),
                tr("lock.zip_text"),
            )
        profile = self._lock_profile()
        if profile is not None and not profile.store_only:
            return (
                FIXED,
                tr("lock.read_only"),
                tr("lock.compressed_text"),
            )
        size = format_size(profile.packed_size) if profile is not None else tr("lock.size_unknown")
        return (
            LOCKED,
            tr("lock.read_only"),
            tr("lock.locked_text", size=size),
        )

    def _update_lock(self) -> None:
        button = getattr(self, "_lock_button", None)
        if button is None:
            return
        state, heading, text = self._lock_state()
        if state == NONE:
            button.hide()
            return
        tip = _qt(f"<b>{heading}</b><br>" + text.replace("\n\n", "<br><br>"))
        icon, color = {
            LOCKED: ("lock", theme.SECONDARY),
            FIXED: ("lock", theme.TEXT_SECONDARY),
            UNLOCKED: ("lock_open", theme.ACCENT),
        }[state]
        button.setIcon(theme.symbol_icon(icon, 16, color))
        button.setToolTip(tip)
        button.setProperty("lockState", state)
        button.setEnabled(not self._saving)
        button.setCursor(
            Qt.CursorShape.PointingHandCursor
            if state in (LOCKED, UNLOCKED)
            else Qt.CursorShape.WhatsThisCursor
        )
        button.show()

    def _on_lock_clicked(self) -> None:
        state, heading, text = self._lock_state()
        if state == FIXED:
            soft_dialog.message(self, heading, text)
        elif state == LOCKED:
            self._unlock_for_editing()
        elif state == UNLOCKED:
            if not self._confirm_discard_if_dirty():
                return
            self._edit_unlocked = False
            self._set_status(tr("lock.status_locked", name=self._archive_label))
            self._after_lock_change()

    def _unlock_for_editing(self) -> None:
        backend = self._backend
        if backend is None:
            return
        if backend.password is None and not self._ask_and_check_password():
            return
        profile = self._lock_profile()
        if profile is None or not profile.store_only:
            self._update_lock()
            return
        free = shutil.disk_usage(Path(backend.path).parent).free
        if not self._confirm_unlock(profile, free):
            return
        self._edit_unlocked = True
        self._set_status(tr("lock.status_unlocked", name=self._archive_label))
        self._after_lock_change()

    def _confirm_unlock(self, profile, free: int) -> bool:  # noqa: ANN001
        """Explain what unlocking and saving mean; True when the user agrees."""
        from PySide6.QtWidgets import QLabel

        size = format_size(profile.packed_size)
        names = tr("lock.dialog_names") if profile.header_encrypted else ""
        html = tr(
            "lock.dialog_html",
            name=html_escape(self._archive_label),
            size=size,
            names=names,
            free=format_size(free),
        )
        dlg = soft_dialog.SoftDialog(self, tr("lock.dialog_title"))
        label = QLabel(html)
        label.setObjectName("dialogText")
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setFixedWidth(500)  # a fixed width makes the wrapped height exact
        label.ensurePolished()  # the stylesheet's font decides the height
        label.setMinimumHeight(label.heightForWidth(500))
        dlg.add_body_widget(label)
        dlg.add_button(tr("button.cancel"), False)
        dlg.add_button(tr("lock.unlock_button"), True, primary=True)
        dlg.adjustSize()
        dlg.exec()
        return dlg.value() is True

    def _ask_and_check_password(self) -> bool:
        """The names are readable but the data needs a password: ask for it and
        check it on the smallest file before unlocking."""
        backend = self._backend
        assert backend is not None
        files = [e for e in backend.list_entries() if not e.is_dir and e.size]
        while True:
            pw = self._prompt_password(tr("lock.password_for_edit", name=self._archive_label))
            if pw is None:
                return False
            backend.set_password(pw)
            self._archive_encrypted = True
            self._edit_profile = None
            if not files:
                return True
            QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                backend.read_bytes(min(files, key=lambda e: e.size), cache=False)
                return True
            except EncryptedArchiveError:
                backend.set_password(None)
            except ArchiveError as exc:
                soft_dialog.message(self, tr("lock.read_failed_title"), str(exc))
                return False
            finally:
                QGuiApplication.restoreOverrideCursor()

    def _after_lock_change(self) -> None:
        self._update_lock()
        self._update_edit_actions()
        self._update_dirty_state()
        current = self._selection.currentIndex()
        if current.isValid():
            # The preview decides whether text is editable; show it again.
            self._on_selection_changed(self._selection.selection(), self._selection.selection())

    # ------------------------------------------------------------ saving
    def save_archive(self, *, blocking: bool = False) -> None:
        """Commit all staged changes (edits, adds, deletes, renames) at once.

        A 7z is saved on a worker thread (the whole archive is rewritten);
        ``blocking`` waits for it while the window stays responsive (used by
        the "save before closing?" question)."""
        backend = self._backend
        if backend is None or not self._can_edit():
            return
        self._stage_editor()
        if not backend.has_unsaved_changes:
            return
        if self._extracting:
            soft_dialog.message(
                self,
                tr("save.extract_busy_title"),
                tr("save.extract_busy_text"),
            )
            return
        plan_of = getattr(backend, "save_plan", None)
        if plan_of is None:
            self._save_now()
            return
        try:
            plan = plan_of()
        except ArchiveError as exc:
            soft_dialog.message(self, tr("save.failed_title"), str(exc))
            return
        folder = Path(backend.path).parent
        free = shutil.disk_usage(folder).free
        if free < plan["needed"]:
            soft_dialog.message(
                self,
                tr("save.no_space_title"),
                tr(
                    "save.no_space_text",
                    needed=format_size(plan["needed"]),
                    free=format_size(free),
                ),
            )
            return
        dialog = SaveProgressDialog(
            self, self._archive_label, str(folder), plan["files"], plan["bytes"]
        )
        dialog.cancel_requested.connect(self._save_worker.cancel)
        self._save_dialog = dialog
        self._save_dialog_timer.start(_SAVE_DIALOG_DELAY_MS)
        self._saving = True
        self._set_status(tr("save.status_busy"))
        self._update_edit_actions()
        self._update_lock()
        self._save_worker.start(backend)
        if blocking:
            loop = QEventLoop()
            self._save_worker.finished.connect(loop.quit)
            if self._saving:
                loop.exec()
            self._save_worker.finished.disconnect(loop.quit)

    def _show_save_dialog(self) -> None:
        if self._saving and self._save_dialog is not None:
            self._save_dialog.show()
            self._save_dialog.raise_()

    def _on_save_progress(self, done: int, total: int, name: str, files: int) -> None:
        if self._save_dialog is not None:
            self._save_dialog.update_progress(done, total, name, files)
        if total:
            self._set_status(tr("save.status_percent", percent=int(done * 100 / total)))

    def _on_save_finished(self, saved: bool, cancelled: bool, error: str) -> None:
        self._saving = False
        self._save_dialog_timer.stop()
        dialog, self._save_dialog = self._save_dialog, None
        if dialog is not None:
            if saved and not dialog.isVisible():
                dialog.deleteLater()  # a quick save needs no window
            else:
                dialog.show_save_result(saved=saved, cancelled=cancelled, error=error or None)
        if saved:
            self._after_save()
        else:
            self._set_status(tr("save.status_cancelled" if cancelled else "save.failed_title"))
            if error and dialog is None:
                soft_dialog.message(self, tr("save.failed_title"), error)
        self._update_edit_actions()
        self._update_dirty_state()
        self._update_lock()

    def _save_now(self) -> None:
        """Synchronous save (zip: small archives are rebuilt in memory)."""
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._backend.save()
        except (ArchiveError, OSError) as exc:
            QGuiApplication.restoreOverrideCursor()
            soft_dialog.message(self, tr("save.failed_title"), str(exc))
            return
        QGuiApplication.restoreOverrideCursor()
        self._after_save()

    def _after_save(self) -> None:
        current = self._selection.currentIndex()
        node = self._node_at(current) if current.isValid() else None
        self._archive_items = sum(1 for e in self._backend.list_effective_entries() if not e.is_dir)
        self._summary_items.setText(tr("count.items", count=self._archive_items))
        self._set_status(tr("save.status_saved"))
        self._reload_preserving_location(node.path if node is not None else None)
        self._update_dirty_state()
        self._update_edit_actions()

    def _refuse_while_saving(self) -> bool:
        """True (and a message) when a save is running."""
        if not self._saving:
            return False
        soft_dialog.message(
            self,
            tr("save.busy_title"),
            tr("save.busy_text"),
        )
        self._show_save_dialog()
        return True
