from __future__ import annotations

import contextlib

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from archy.i18n import tr

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

    def show_toast(self, text: str, ms: int = 1800) -> None:
        self._toast.setText(text)
        self._toast.adjustSize()
        self._toast.move(
            (self.width() - self._toast.width()) // 2, self.height() - self._toast.height() - 48
        )
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
