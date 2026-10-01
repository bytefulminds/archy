from __future__ import annotations

from PySide6.QtCore import (
    Qt,
)
from PySide6.QtWidgets import (
    QWidget,
)

from archy.i18n import tr
from archy.ui import soft_dialog
from archy.ui.main_window.chrome import (
    ResizeGrip as _ResizeGrip,
)


class WindowFrameMixin:
    """Frameless-window chrome: resize grips, responsive layout, resize/close.

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    # ------------------------------------------------------------ window chrome
    def _build_resize_grips(self, host: QWidget) -> None:
        edges = (
            Qt.Edge.TopEdge,
            Qt.Edge.BottomEdge,
            Qt.Edge.LeftEdge,
            Qt.Edge.RightEdge,
            Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
            Qt.Edge.TopEdge | Qt.Edge.RightEdge,
            Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
            Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
        )
        self._grips = [_ResizeGrip(host, e) for e in edges]
        self._position_grips()

    def _position_grips(self) -> None:
        if not self._grips:
            return
        if self.isMaximized() or self.isFullScreen():
            for grip in self._grips:
                grip.hide()
            return
        host = self.centralWidget()
        w, h, m = host.width(), host.height(), 6
        geoms = {
            Qt.Edge.TopEdge: (m, 0, w - 2 * m, m),
            Qt.Edge.BottomEdge: (m, h - m, w - 2 * m, m),
            Qt.Edge.LeftEdge: (0, m, m, h - 2 * m),
            Qt.Edge.RightEdge: (w - m, m, m, h - 2 * m),
            Qt.Edge.TopEdge | Qt.Edge.LeftEdge: (0, 0, m, m),
            Qt.Edge.TopEdge | Qt.Edge.RightEdge: (w - m, 0, m, m),
            Qt.Edge.BottomEdge | Qt.Edge.LeftEdge: (0, h - m, m, m),
            Qt.Edge.BottomEdge | Qt.Edge.RightEdge: (w - m, h - m, m, m),
        }
        for grip in self._grips:
            x, y, gw, gh = geoms[grip._edges]
            grip.setGeometry(x, y, gw, gh)
            grip.raise_()
            grip.show()

    def _apply_responsive(self) -> None:
        # A frameless window can be dragged narrow; rather than crushing the file
        # grid, drop the preview panel first, then the sidebar, so the files keep
        # a usable width. They return automatically when the window widens again.
        width = self.width()
        if hasattr(self, "_preview_panel"):
            self._preview_panel.setVisible(width >= 940)
        if hasattr(self, "_sidebar"):
            self._sidebar.setVisible(width >= 720)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().resizeEvent(event)
        # No shadow gap when maximized (the window fills the screen).
        m = 0 if (self.isMaximized() or self.isFullScreen()) else self._SHADOW_MARGIN
        self._outer.setContentsMargins(m, m, m, m)
        self._position_grips()
        self._apply_responsive()

    # ------------------------------------------------------------- lifecycle
    def closeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if self._saving:
            # The original stays untouched until a save finishes, so stopping
            # one is always safe; it just loses the new version.
            choice = soft_dialog.confirm(
                self,
                tr("save.busy_title"),
                tr("save.quit_text"),
                [(tr("save.keep_saving"), False, True), (tr("save.cancel_and_quit"), True, False)],
            )
            if choice is not True:
                event.ignore()
                self._show_save_dialog()
                return
            self._save_worker.cancel()
            self._save_worker.wait()
            if self._backend is not None:
                self._backend.discard_changes()
        if not self._confirm_discard_if_dirty():
            event.ignore()
            return
        if self._settings.remember_window:
            self._settings.window_geometry = self.saveGeometry()
            self._settings.sync()
        # Release any preview media (unlocks a spilled temp file) before pruning.
        self._clear_preview_widget()
        self._temp.clear_preview()  # drop the last preview file regardless
        if self._temp.prune_on_exit():
            self._temp.prune()
        if self._backend is not None:
            self._backend.close()
        super().closeEvent(event)
