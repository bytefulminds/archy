from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from archy.ui import theme

# A "busy" layer over a panel: a dimmed backdrop with a card holding a spinner
# and a line of text. Used while an archive opens (listing 100,000 entries of an
# encrypted 7z takes a few seconds), so the window shows that Archy is working
# instead of looking frozen. The listing runs on a worker thread, so the GUI
# thread is free to animate the spinner.

_SHOW_DELAY_MS = 150  # a quick open never flashes the overlay


class Spinner(QWidget):
    """A rotating arc in the accent colour."""

    def __init__(self, parent: QWidget | None = None, size: int = 34) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = max(3.0, self.width() / 10)
        rect = QRectF(width, width, self.width() - 2 * width, self.height() - 2 * width)
        track = QColor(theme.TEXT_SECONDARY)
        track.setAlpha(50)
        pen = QPen(track, width)
        painter.setPen(pen)
        painter.drawEllipse(rect)
        pen = QPen(QColor(theme.ACCENT), width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        # Qt angles are in 1/16 degree, counter-clockwise from 3 o'clock.
        painter.drawArc(rect, int(-self._angle * 16), int(100 * 16))


class BusyOverlay(QWidget):
    """Covers ``target`` while busy. ``show_busy`` / ``hide_busy``.

    It is a sibling on top of ``target`` (not a child): a child of a QSplitter
    would become an extra splitter pane."""

    def __init__(self, target: QWidget) -> None:
        super().__init__(target.parentWidget())
        self._target = target
        self.setObjectName("busyOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card = QFrame()
        card.setObjectName("busyCard")
        column = QVBoxLayout(card)
        column.setContentsMargins(26, 20, 26, 20)
        column.setSpacing(12)
        column.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spinner = Spinner()
        self._title = QLabel("")
        self._title.setObjectName("busyTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail = QLabel("")
        self._detail.setObjectName("busyDetail")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail.setWordWrap(True)
        self._detail.setMaximumWidth(360)
        column.addWidget(self._spinner, alignment=Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(self._title)
        column.addWidget(self._detail)
        layout.addWidget(card)
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.setInterval(_SHOW_DELAY_MS)
        self._delay.timeout.connect(self._reveal)
        target.installEventFilter(self)
        self.hide()

    @property
    def busy(self) -> bool:
        return self._delay.isActive() or self.isVisible()

    def show_busy(self, title: str, detail: str = "") -> None:
        self._title.setText(title)
        self._detail.setText(detail)
        self._detail.setVisible(bool(detail))
        if not self.isVisible() and not self._delay.isActive():
            self._delay.start()

    def hide_busy(self) -> None:
        self._delay.stop()
        self._spinner.stop()
        self.hide()

    def _reveal(self) -> None:
        self.setGeometry(self._target.geometry())
        self.raise_()
        self.show()
        self._spinner.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if watched is self._target and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self.setGeometry(self._target.geometry())
        return False
