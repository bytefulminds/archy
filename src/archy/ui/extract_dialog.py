from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from archy.i18n import tr
from archy.services.formatting import format_size
from archy.ui.soft_dialog import SoftDialog

# The extraction window: which file (its path inside the archive) is being
# written right now, a progress bar that really moves, and the numbers behind
# it (files, bytes, speed, time left). It stays open next to the main window
# (not modal), can be sent to the background (the status bar keeps showing
# progress), and turns into a summary when the extraction ends.

_RATE_WINDOW = 5.0  # seconds of history used for the speed / time-left estimate


def _format_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return tr("duration.seconds", s=seconds)
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return tr("duration.minutes", m=minutes, s=seconds)
    hours, minutes = divmod(minutes, 60)
    return tr("duration.hours", h=hours, m=minutes)


class ExtractProgressDialog(SoftDialog):
    cancel_requested = Signal()

    # Translation keys of the texts, overridden by SaveProgressDialog.
    TITLE = "progress.title"
    SOURCE_LABEL = "progress.source"
    DEST_LABEL = "progress.dest"
    BUSY_TEXT = "progress.busy"
    CANCELLING_TEXT = "progress.cancelling"

    def __init__(
        self,
        parent: QWidget | None,
        archive_name: str,
        dest: str,
        file_count: int,
        total_bytes: int,
    ) -> None:
        super().__init__(parent, tr(self.TITLE))
        self.setModal(False)
        self.setMinimumWidth(560)
        self._dest = dest
        self._file_count = max(0, int(file_count))
        self._total = max(0, int(total_bytes))
        self._done = 0
        self._current = ""
        self._seen: set[str] = set()
        self._files_started = 0
        self._samples: list[tuple[float, int]] = []
        self._finished = False
        self._elapsed = QElapsedTimer()
        self._elapsed.start()

        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)

        route = QGridLayout()
        route.setHorizontalSpacing(10)
        route.setVerticalSpacing(4)
        route.addWidget(_muted(tr(self.SOURCE_LABEL)), 0, 0)
        route.addWidget(_value(archive_name), 0, 1)
        route.addWidget(_muted(tr(self.DEST_LABEL)), 1, 0)
        route.addWidget(_value(dest), 1, 1)
        route.setColumnStretch(1, 1)
        column.addLayout(route)

        card = QFrame()
        card.setObjectName("extractCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 12)
        card_layout.setSpacing(8)
        self._state = QLabel(tr("progress.preparing"))
        self._state.setObjectName("extractState")
        self._file = QLabel("")
        self._file.setObjectName("extractFile")
        self._file.setWordWrap(True)
        self._file.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._file.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._bar = QProgressBar()
        self._bar.setObjectName("extractBar")
        self._bar.setTextVisible(False)
        self._bar.setRange(0, 0)  # busy until the first bytes are written
        # Two fixed lines instead of one wrapping line: a wrapped label does
        # not grow the (already shown) dialog, so its second line got clipped.
        self._numbers = _number_label()  # files and bytes
        self._timing = _number_label()  # speed, time left, elapsed
        card_layout.addWidget(self._state)
        card_layout.addWidget(self._file)
        card_layout.addWidget(self._bar)
        card_layout.addWidget(self._numbers)
        card_layout.addWidget(self._timing)
        column.addWidget(card)

        self._failures = QLabel("")
        self._failures.setObjectName("settingsHint")
        self._failures.setWordWrap(True)
        self._failures.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._failures.hide()
        column.addWidget(self._failures)
        self.add_body_widget(body)

        # Own buttons (SoftDialog's add_button closes the dialog on click).
        self._open_button = self._button(tr("progress.open_folder"), self._open_dest)
        self._open_button.hide()
        self._background_button = self._button(tr("progress.background"), self.hide)
        self._cancel_button = self._button(tr("progress.cancel"), self._cancel)
        self._close_button = self._button(tr("progress.close"), self.accept, primary=True)
        self._close_button.hide()

        # Time-based refresh, so speed / time left keep moving between updates.
        self._ticker = QTimer(self)
        self._ticker.setInterval(500)
        self._ticker.timeout.connect(self._refresh_numbers)
        self._ticker.start()
        self._refresh_numbers()

    # --------------------------------------------------------------- updates
    def update_progress(self, done: int, total: int, name: str, files: int = 0) -> None:
        if self._finished:
            return
        self._files_started = max(self._files_started, int(files))
        self._done = max(0, int(done))
        if total > 0:
            self._total = int(total)
        if name and name != self._current:
            self._current = name
            self._seen.add(name)
            self._file.setText(name)
            self._file.setToolTip(name)
        if self._total > 0:
            if self._bar.maximum() != 1000:
                self._bar.setRange(0, 1000)
            self._bar.setValue(min(1000, int(self._done * 1000 / self._total)))
        self._state.setText(tr(self.BUSY_TEXT))
        self._samples.append((time.monotonic(), self._done))
        cutoff = time.monotonic() - _RATE_WINDOW
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.pop(0)

    def _refresh_numbers(self) -> None:
        amounts = []
        if self._file_count:
            index = min(max(self._files_started, len(self._seen)), self._file_count)
            amounts.append(tr("progress.file_of", index=index, count=self._file_count))
        if self._total:
            amounts.append(
                tr(
                    "progress.bytes_of",
                    done=format_size(self._done),
                    total=format_size(self._total),
                )
            )
        self._numbers.setText("  ·  ".join(amounts))
        timing = []
        rate = self._rate()
        if rate > 0 and not self._finished:
            timing.append(tr("progress.speed", rate=format_size(rate)))
            if self._total > self._done:
                timing.append(
                    tr(
                        "progress.left",
                        duration=_format_duration((self._total - self._done) / rate),
                    )
                )
        if not self._finished:
            timing.append(
                tr("progress.elapsed", duration=_format_duration(self._elapsed.elapsed() / 1000))
            )
        self._timing.setText("  ·  ".join(timing))
        self._timing.setVisible(bool(timing))

    def _rate(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        (t0, d0), (t1, d1) = self._samples[0], self._samples[-1]
        return (d1 - d0) / (t1 - t0) if t1 > t0 else 0.0

    # ----------------------------------------------------------------- result
    def show_result(self, extracted: int, failed: list, cancelled: bool) -> None:
        self._finished = True
        self._ticker.stop()
        took = _format_duration(self._elapsed.elapsed() / 1000)
        self._bar.setRange(0, 1000)
        if cancelled:
            self._state.setText(tr("progress.cancelled"))
            self._file.setText(tr("progress.cancelled_text", count=extracted, took=took))
        elif failed:
            self._state.setText(tr("progress.done_errors"))
            self._file.setText(
                tr("progress.done_errors_text", count=extracted, failed=len(failed), took=took)
            )
            self._bar.setValue(1000)
            lines = [
                f"{path or tr('progress.archive_itself')}: {reason}" for path, reason in failed[:12]
            ]
            if len(failed) > 12:
                lines.append(tr("progress.and_more", count=len(failed) - 12))
            self._failures.setText("\n".join(lines))
            self._failures.show()
        else:
            self._state.setText(tr("progress.done"))
            self._file.setText(tr("progress.done_text", count=extracted, took=took))
            self._bar.setValue(1000)
        self._file.setToolTip("")
        self._refresh_numbers()
        self._cancel_button.hide()
        self._background_button.hide()
        self._open_button.setVisible(extracted > 0)
        self._close_button.show()
        self.show()
        self.raise_()

    # ---------------------------------------------------------------- actions
    def _cancel(self) -> None:
        self._cancel_button.setEnabled(False)
        self._cancel_button.setText(tr("progress.cancel_pending"))
        self._state.setText(tr(self.CANCELLING_TEXT))
        self.cancel_requested.emit()

    def _open_dest(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._dest))))

    def _button(self, label: str, slot, *, primary: bool = False) -> QPushButton:  # noqa: ANN001
        button = QPushButton(label)
        if primary:
            button.setObjectName("primaryAction")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        self._button_row.addWidget(button)
        return button

    def reject(self) -> None:  # Esc / title-bar close
        if self._finished:
            super().reject()
        else:
            self.hide()  # keep running in the background; status bar shows progress


def _number_label() -> QLabel:
    label = QLabel("")
    label.setObjectName("extractNumbers")
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("settingsField")
    return label


class _ElidedLabel(QLabel):
    """Shows a long path shortened in the middle to fit (full text as tooltip),
    so both the drive/start and the file name stay visible."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self._full = text
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().resizeEvent(event)
        metrics = self.fontMetrics()
        self.setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideMiddle, self.width()))


def _value(text: str) -> QLabel:
    label = _ElidedLabel(text)
    label.setObjectName("extractValue")
    return label


class SaveProgressDialog(ExtractProgressDialog):
    """Progress of saving an archive: the new version is written next to the
    original, checked, and only then replaces it."""

    TITLE = "saveprogress.title"
    SOURCE_LABEL = "progress.source"
    DEST_LABEL = "saveprogress.dest"
    BUSY_TEXT = "saveprogress.busy"
    CANCELLING_TEXT = "saveprogress.cancelling"

    def update_progress(self, done: int, total: int, name: str, files: int = 0) -> None:
        super().update_progress(done, total, name, files)
        if not name and total and done >= total and not self._finished:
            self._verifying = True
            self._state.setText(tr("saveprogress.verifying"))
            self._file.setText(tr("saveprogress.verifying_text"))
            self._bar.setRange(0, 0)  # busy: the check has no byte progress
            self._refresh_numbers()

    def _refresh_numbers(self) -> None:
        super()._refresh_numbers()
        if getattr(self, "_verifying", False) and not self._finished:
            self._timing.setText(
                tr("progress.elapsed", duration=_format_duration(self._elapsed.elapsed() / 1000))
            )

    def show_save_result(self, *, saved: bool, cancelled: bool, error: str | None) -> None:
        self._finished = True
        self._ticker.stop()
        took = _format_duration(self._elapsed.elapsed() / 1000)
        self._bar.setRange(0, 1000)
        if saved:
            self._state.setText(tr("saveprogress.saved"))
            self._file.setText(tr("saveprogress.saved_text", took=took))
            self._bar.setValue(1000)
        elif cancelled:
            self._state.setText(tr("progress.cancelled"))
            self._file.setText(tr("saveprogress.cancelled_text"))
        else:
            self._state.setText(tr("saveprogress.failed"))
            self._file.setText(tr("saveprogress.failed_text", error=error or ""))
        self._file.setToolTip("")
        self._refresh_numbers()
        self._cancel_button.hide()
        self._background_button.hide()
        self._close_button.show()
        self.show()
        self.raise_()
