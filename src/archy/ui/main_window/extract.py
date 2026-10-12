from __future__ import annotations

import time

from PySide6.QtWidgets import (
    QLabel,
    QProgressBar,
    QToolButton,
)

from archy.core.tree import TreeNode, iter_file_entries
from archy.i18n import tr
from archy.services.formatting import format_size
from archy.ui import soft_dialog
from archy.ui.extract_dialog import ExtractProgressDialog


class ExtractMixin:
    """Extraction and prefetch progress.

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    # ------------------------------------------------------------- extract
    def _build_progress_widgets(self) -> None:
        self._progress_label = QLabel("")
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 1000)
        self._progress_bar.setFixedWidth(200)
        self._progress_bar.setTextVisible(False)
        self._cancel_extract_button = QToolButton()
        self._cancel_extract_button.setText(tr("extract.details"))
        self._cancel_extract_button.setToolTip(tr("extract.details_tip"))
        self._cancel_extract_button.clicked.connect(self._show_extract_dialog)
        self._extract_dialog = None

        # Separate, non-cancellable progress for the whole-folder prefetch, so a
        # slow solid-7z folder load shows "file X of N" instead of nothing.
        self._prefetch_label = QLabel("")
        self._prefetch_bar = QProgressBar()
        self._prefetch_bar.setFixedWidth(110)
        self._prefetch_bar.setTextVisible(False)
        self._prefetch_files = (0, 0)  # files decoded, files in the pass
        self._prefetch_bytes: tuple[int, int] | None = None  # compressed bytes read
        self._prefetch_since = 0.0

        # Thumbnail progress for the current folder, so a slow grid fill is
        # visible too. Shown only while the folder prefetch bar is not (one
        # indicator at a time keeps the strip calm).
        self._thumb_label = QLabel("")
        self._thumb_bar = QProgressBar()
        self._thumb_bar.setFixedWidth(120)
        self._thumb_bar.setTextVisible(False)
        self._thumb_state = (0, 0)

        # All live in the custom bottom status strip, right-aligned.
        for widget in (
            self._thumb_label,
            self._thumb_bar,
            self._prefetch_label,
            self._prefetch_bar,
            self._progress_label,
            self._progress_bar,
            self._cancel_extract_button,
        ):
            self._status_row.addWidget(widget)
            widget.hide()

    def _on_prefetch_started(self, total: int) -> None:
        # The pass makes the thumbnails from here on; tiles wait for it.
        self._thumbs.set_pass_active(True)
        if total <= 1:
            return  # a single file is not worth a folder-load indicator
        self._prefetch_files = (0, total)
        self._prefetch_bytes = None
        self._prefetch_since = time.monotonic()
        self._prefetch_label.show()
        self._prefetch_bar.show()
        self._show_prefetch()
        self._update_thumb_indicator()

    def _on_prefetch_progress(self, done: int, total: int) -> None:
        # done counts files that are decoded AND (images) thumbnailed.
        if total <= 1:
            return
        self._prefetch_files = (done, total)
        if not self._prefetch_bar.isVisible():
            self._prefetch_label.show()
            self._prefetch_bar.show()
            self._update_thumb_indicator()
        self._show_prefetch()

    def _on_prefetch_activity(self, done: int, total: int) -> None:
        """A heartbeat while the pass runs: the compressed bytes read so far
        when the backend can tell (a solid 7z), else just "still busy"."""
        if not self._prefetch_bar.isVisible():
            return
        self._prefetch_bytes = (done, total) if total > 0 else None
        self._show_prefetch()

    def _show_prefetch(self) -> None:
        """Bar and text of the folder pass. A big solid 7z can decode for
        minutes before the first file of the folder comes out: the bar then
        follows the bytes read, or moves as a busy bar, and the text says how
        far it got or how long it has been busy - never a frozen "0 / N"."""
        done, total = self._prefetch_files
        text = tr("progress.folder_loading", done=done, total=total)
        detail = ""
        if self._prefetch_bytes is not None:
            read, size = self._prefetch_bytes
            self._prefetch_bar.setRange(0, 1000)
            self._prefetch_bar.setValue(int(1000 * read / size))
            text += f" · {read * 100 // size}%"  # short: the strip is narrow
            detail = tr("progress.folder_bytes", read=format_size(read), size=format_size(size))
        elif done > 0:
            self._prefetch_bar.setRange(0, total)
            self._prefetch_bar.setValue(done)
        else:
            self._prefetch_bar.setRange(0, 0)  # busy: moves on its own
            elapsed = int(time.monotonic() - self._prefetch_since)
            if elapsed >= 3:
                text += f" · {elapsed // 60}:{elapsed % 60:02d}"
        self._prefetch_label.setText(text)
        self._prefetch_label.setToolTip(detail)
        self._prefetch_bar.setToolTip(detail)
        # "Ready" next to a folder that is still loading says the wrong thing,
        # and the strip needs the room.
        self._status_msg.setVisible(False)

    def _on_prefetch_done(self) -> None:
        self._prefetch_label.hide()
        self._prefetch_bar.hide()
        self._status_msg.setVisible(True)
        self._thumbs.set_pass_active(False)
        self._update_thumb_indicator()

    def _on_thumb_progress(self, done: int, total: int) -> None:
        self._thumb_state = (done, total)
        self._update_thumb_indicator()

    def _update_thumb_indicator(self) -> None:
        done, total = self._thumb_state
        show = total > 0 and not self._prefetch_bar.isVisible()
        if show:
            self._thumb_bar.setRange(0, total)
            self._thumb_bar.setValue(done)
            self._thumb_label.setText(tr("progress.thumbs_loading", done=done, total=total))
        self._thumb_label.setVisible(show)
        self._thumb_bar.setVisible(show)

    def _unique_files(self, nodes: list[TreeNode]) -> list:
        seen: dict[str, object] = {}
        for node in nodes:
            for entry in iter_file_entries(node):
                seen.setdefault(entry.path, entry)
        return list(seen.values())

    def extract_selected(self) -> None:
        if self._backend is None:
            return
        nodes = self._selected_nodes()
        if not nodes:
            soft_dialog.message(self, tr("extract.title"), tr("extract.select_first"))
            return
        entries = self._unique_files(nodes)
        self._choose_dest_and_extract(entries)

    def extract_all(self) -> None:
        if self._backend is None or not self._nav:
            return
        entries = iter_file_entries(self._nav[0])
        self._choose_dest_and_extract(entries)

    def _choose_dest_and_extract(self, entries: list) -> None:
        if not entries:
            soft_dialog.message(self, tr("extract.title"), tr("extract.nothing"))
            return
        if self._refuse_while_saving():
            return
        from PySide6.QtWidgets import QFileDialog

        dest = QFileDialog.getExistingDirectory(self, tr("extract.choose_dest"))
        if not dest:
            return
        self._extract_dest = dest
        self._extracting = True  # a save waits for this (it replaces the file)
        self._extract_action.setEnabled(False)
        self._extract_all_action.setEnabled(False)
        self._progress_label.setText(tr("extract.busy"))
        self._progress_bar.setRange(0, 0)  # busy until the first bytes land
        for widget in (self._progress_label, self._progress_bar, self._cancel_extract_button):
            widget.show()
        # The extraction window: current file (its path in the archive) and a
        # live progress bar. Not modal; "Op de achtergrond" hides it and the
        # status bar keeps showing progress ("Details" brings it back).
        files = [e for e in entries if not e.is_dir]
        dialog = ExtractProgressDialog(
            self,
            self._archive_label or tr("crumb.archive_fallback"),
            dest,
            len(files),
            sum(max(int(e.size or 0), 0) for e in files),
        )
        dialog.cancel_requested.connect(self._extract_worker.cancel)
        self._extract_dialog = dialog
        dialog.show()
        self._extract_worker.start(self._backend, files, dest)

    def _show_extract_dialog(self) -> None:
        if self._extract_dialog is not None:
            self._extract_dialog.show()
            self._extract_dialog.raise_()

    def _on_extract_progress(self, done: int, total: int, name: str, files: int = 0) -> None:
        if total > 0:
            if self._progress_bar.maximum() != 1000:
                self._progress_bar.setRange(0, 1000)
            self._progress_bar.setValue(int(done / total * 1000))
            percent = done * 100 // total
            self._progress_label.setText(
                tr("extract.busy_percent", percent=percent, name=name.rsplit("/", 1)[-1])
            )
        if self._extract_dialog is not None:
            self._extract_dialog.update_progress(done, total, name, files)

    def _on_extract_finished(self, result: object) -> None:
        self._extracting = False
        for widget in (self._progress_label, self._progress_bar, self._cancel_extract_button):
            widget.hide()
        self._extract_action.setEnabled(self._backend is not None)
        self._extract_all_action.setEnabled(self._backend is not None)

        extracted = getattr(result, "extracted", 0)
        failed = getattr(result, "failed", [])
        cancelled = getattr(result, "cancelled", False)
        if self._extract_dialog is not None:
            # The window turns into the summary (with any failures listed).
            self._extract_dialog.show_result(extracted, failed, cancelled)
        if cancelled:
            self._set_status(tr("extract.status_cancelled", count=extracted))
        elif failed:
            self._set_status(tr("extract.status_failed", count=extracted, failed=len(failed)))
        else:
            self._set_status(tr("extract.status_done", count=extracted, dest=self._extract_dest))
