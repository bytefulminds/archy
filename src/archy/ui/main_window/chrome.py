from __future__ import annotations

import contextlib

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from archy.i18n import tr
from archy.ui import theme

_BAR_IDLE_MS = 2500  # the slideshow bar hides after this long without the mouse moving


class SlideshowBar(QFrame):
    """The controls of a slideshow, over the bottom of the picture: previous,
    play/pause, next, the time per photo, and stop (back to the window)."""

    def __init__(
        self, parent: QWidget, *, on_prev, on_toggle, on_next, on_seconds, on_stop
    ) -> None:  # noqa: ANN001, E501
        super().__init__(parent)
        self.setObjectName("slideshowBar")
        self._on_seconds = on_seconds
        self._seconds = 4
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(4)

        def button(icon: str, tip: str, slot) -> QToolButton:  # noqa: ANN001
            b = QToolButton(self)
            b.setObjectName("videoBtn")
            b.setIcon(theme.symbol_icon(icon, 18, "#DFE7EC"))
            b.setIconSize(QSize(18, 18))
            b.setFixedSize(32, 32)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(slot)
            return b

        self.prev = button("chevron_left", tr("slideshow.previous"), on_prev)
        self.play = button("pause", tr("slideshow.pause"), on_toggle)
        self.next = button("chevron_right", tr("slideshow.next"), on_next)
        self.slower = button("minus", tr("slideshow.slower"), lambda: self._step_seconds(-1))
        self.seconds = QLabel(self)
        self.seconds.setObjectName("videoTime")
        self.seconds.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.seconds.setMinimumWidth(36)
        self.seconds.setToolTip(tr("slideshow.seconds_tip"))
        self.faster = button("plus", tr("slideshow.faster"), lambda: self._step_seconds(1))
        self.stop = button("stop", tr("slideshow.stop"), on_stop)
        divider = QLabel("│", self)
        divider.setObjectName("stageDivider")
        divider2 = QLabel("│", self)
        divider2.setObjectName("stageDivider")
        for widget in (
            self.prev,
            self.play,
            self.next,
            divider,
            self.slower,
            self.seconds,
            self.faster,
            divider2,
            self.stop,
        ):
            row.addWidget(widget)
        self.set_seconds(self._seconds)

    def set_playing(self, playing: bool) -> None:
        self.play.setIcon(theme.symbol_icon("pause" if playing else "play", 18, "#DFE7EC"))
        self.play.setToolTip(tr("slideshow.pause" if playing else "slideshow.play"))

    def set_seconds(self, seconds: int) -> None:
        self._seconds = seconds
        self.seconds.setText(f"{seconds} s")

    def _step_seconds(self, step: int) -> None:
        seconds = max(1, min(60, self._seconds + step))
        if seconds != self._seconds:
            self.set_seconds(seconds)
            self._on_seconds(seconds)


# Small stand-alone widgets that make the frameless main window behave like a
# native one. They are driven entirely by callbacks and hold no reference to the
# MainWindow, so they live here rather than cluttering the window class.


class FullscreenWindow(QWidget):
    """Shows one preview widget fullscreen. Esc/F11 exits (returning the current
    content); Left/Right ask the window to show the previous/next file."""

    def __init__(self, content: QWidget, on_exit, on_navigate, on_space=None) -> None:  # noqa: ANN001
        super().__init__()
        self._content = content
        self._on_exit = on_exit
        self._on_navigate = on_navigate
        self._on_space = on_space  # () -> bool: handled (the slideshow)
        self.setWindowTitle(tr("window.fullscreen_title"))
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(content)
        # A short message over the picture (slideshow started / paused).
        self._toast = QLabel(self)
        self._toast.setObjectName("fullscreenToast")
        self._toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast.hide)
        # The slideshow bar: shown while the mouse moves, hidden when it rests.
        # The cursor is polled (the picture's own widgets take the mouse
        # events, and their children change with every photo).
        self.bar: SlideshowBar | None = None
        self._last_cursor = QCursor.pos()
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self._hide_bar)
        self._cursor_poll = QTimer(self)
        self._cursor_poll.setInterval(150)
        self._cursor_poll.timeout.connect(self._check_cursor)

    def enable_slideshow_bar(self, **callbacks) -> SlideshowBar:  # noqa: ANN003
        if self.bar is None:
            self.bar = SlideshowBar(self, **callbacks)
            self._cursor_poll.start()
        self.show_bar()
        return self.bar

    def show_bar(self) -> None:
        if self.bar is None:
            return
        self.bar.adjustSize()
        self.bar.move(
            (self.width() - self.bar.width()) // 2, self.height() - self.bar.height() - 32
        )
        self.bar.show()
        self.bar.raise_()
        self._idle.start(_BAR_IDLE_MS)

    def _hide_bar(self) -> None:
        if self.bar is not None and not self.bar.underMouse():
            self.bar.hide()
        elif self.bar is not None:
            self._idle.start(_BAR_IDLE_MS)  # the pointer rests on the bar: keep it

    def _check_cursor(self) -> None:
        position = QCursor.pos()
        if position != self._last_cursor:
            self._last_cursor = position
            self.show_bar()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().resizeEvent(event)
        if self.bar is not None and self.bar.isVisible():
            self.show_bar()

    def show_toast(self, text: str, ms: int = 1800) -> None:
        self._toast.setText(text)
        self._toast.adjustSize()
        # At the top: the slideshow bar has the bottom.
        self._toast.move((self.width() - self._toast.width()) // 2, 48)
        self._toast.show()
        self._toast.raise_()
        self._toast_timer.start(ms)

    @property
    def content(self) -> QWidget:
        return self._content

    def set_content(self, widget: QWidget) -> None:
        old = self._content
        self._layout.removeWidget(old)
        # Same release path as the side panel: a video must hand its player to
        # the GIL-free teardown before its widget goes (see providers).
        cleanup = getattr(old, "_zx_cleanup", None)
        if callable(cleanup):
            with contextlib.suppress(Exception):  # cleanup is best-effort
                cleanup()
        old.deleteLater()
        self._content = widget
        self._layout.addWidget(widget)
        self._toast.raise_()
        if self.bar is not None:
            self.bar.raise_()

    def keyPressEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_F11):
            self.close()
        elif event.key() == Qt.Key.Key_Left:
            self._on_navigate(-1)
        elif event.key() == Qt.Key.Key_Right:
            self._on_navigate(1)
        elif event.key() == Qt.Key.Key_Space and self._on_space is not None and self._on_space():
            event.accept()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        self._cursor_poll.stop()
        self._idle.stop()
        self._on_exit(self._content)
        super().closeEvent(event)


class DragBar(QFrame):
    """The header doubles as the window's title bar: dragging an empty part of it
    moves the frameless window, and a double-click toggles maximize. Clicks on
    child widgets (buttons, search) are handled by those widgets, so only empty
    header area starts a move."""

    def __init__(self, on_toggle_max) -> None:  # noqa: ANN001
        super().__init__()
        self._on_toggle_max = on_toggle_max

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_toggle_max()
        super().mouseDoubleClickEvent(event)


class ResizeGrip(QWidget):
    """An invisible edge/corner grip that starts a native window resize. Eight of
    these sit on the window edges of a frameless window so it resizes like a
    normal one (drag, snap and maximize all handled by the OS)."""

    _CURSORS = {
        Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
        Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
        Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.TopEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeFDiagCursor,
        Qt.Edge.BottomEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeFDiagCursor,
        Qt.Edge.TopEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeBDiagCursor,
        Qt.Edge.BottomEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeBDiagCursor,
    }

    def __init__(self, parent: QWidget, edges: Qt.Edge) -> None:
        super().__init__(parent)
        self._edges = edges
        self.setCursor(self._CURSORS[edges])
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemResize(self._edges)
