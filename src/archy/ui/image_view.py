from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QImage, QMouseEvent, QPixmap, QResizeEvent, QTransform, QWheelEvent
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

# In-memory image viewer. The image is loaded from bytes (QImage.fromData); no
# temp file is written. Interaction:
#   - starts fitted to the window (never upscaled past 100%);
#   - left-click toggles fit <-> 100%, zooming in on the clicked point;
#   - the mouse wheel zooms continuously between fit and 100%, at the cursor;
#   - dragging pans (hand cursor) whenever the image is larger than the view;
#   - rotate_cw / rotate_ccw rotate the view by 90 degrees (viewing only, the
#     file is never changed).

_CLICK_SLOP = 4  # px of movement below which a press+release counts as a click
_WHEEL_STEP = 1.25


class ImageView(QGraphicsView):
    scale_changed = Signal(int)  # current zoom percentage (relative to actual pixels)

    def __init__(self) -> None:
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item: QGraphicsPixmapItem | None = None
        self._base_image: QImage | None = None
        self._angle = 0
        self._mode = "fit"
        self._fill_mode = "contain"  # "contain" (whole image) | "cover" (fill, crop)
        self._press_pos: QPoint | None = None

        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_fit_mode()

    # --------------------------------------------------------------- loading
    def set_image(self, image: QImage) -> None:
        self._base_image = image
        self._angle = 0
        self._render()
        self._apply_fill()

    def set_fill_mode(self, mode: str) -> None:
        """Initial display: "contain" fits the whole image; "cover" fills the
        viewport (cropping overflow). The user can still click/zoom afterwards."""
        self._fill_mode = mode if mode in ("contain", "cover") else "contain"
        self._apply_fill()

    def _apply_fill(self) -> None:
        if self._fill_mode == "cover":
            self._cover()
        else:
            self.fit()

    def _cover(self) -> None:
        if self._item is None:
            return
        rect = self._item.boundingRect()
        view = self.viewport().rect()
        if rect.isEmpty():
            return
        self.resetTransform()
        factor = max(view.width() / rect.width(), view.height() / rect.height())
        self.scale(factor, factor)
        self._mode = "actual"  # larger than the viewport, so dragging pans
        self._apply_actual_mode()
        self.centerOn(self._item)
        self._emit_scale()

    def release(self) -> None:
        """Drop the image and scene contents to free memory when disposed."""
        self._scene.clear()
        self._item = None
        self._base_image = None

    def _render(self) -> None:
        if self._base_image is None:
            return
        image = self._base_image
        if self._angle:
            image = image.transformed(QTransform().rotate(self._angle))
        self._scene.clear()
        self._item = self._scene.addPixmap(QPixmap.fromImage(image))
        self._scene.setSceneRect(self._item.boundingRect())

    # -------------------------------------------------------------- rotation
    def rotate_cw(self) -> None:
        self._rotate(90)

    def rotate_ccw(self) -> None:
        self._rotate(-90)

    def _rotate(self, delta: int) -> None:
        if self._base_image is None:
            return
        self._angle = (self._angle + delta) % 360
        self._render()
        self.fit()

    # ----------------------------------------------------------------- zoom
    def _current_scale(self) -> float:
        return float(self.transform().m11())

    def _emit_scale(self) -> None:
        self.scale_changed.emit(round(self._current_scale() * 100))

    def zoom_percent(self) -> int:
        return round(self._current_scale() * 100)

    def zoom_in(self) -> None:
        self._zoom_by(_WHEEL_STEP)

    def zoom_out(self) -> None:
        self._zoom_by(1 / _WHEEL_STEP)

    def _zoom_by(self, step: float) -> None:
        if self._item is None:
            return
        fit_scale = self._fit_scale()
        current = self._current_scale()
        target = max(fit_scale, min(current * step, 1.0))  # clamp fit .. 100%
        if abs(target - current) > 1e-4:
            self.scale(target / current, target / current)
        self._mode = "fit" if target <= fit_scale + 1e-6 else "actual"
        self._refresh_mode()
        self._emit_scale()

    def _fit_scale(self) -> float:
        if self._item is None:
            return 1.0
        rect = self._item.boundingRect()
        if rect.isEmpty():
            return 1.0
        view = self.viewport().rect()
        return min(view.width() / rect.width(), view.height() / rect.height(), 1.0)

    def _fits_at_actual(self) -> bool:
        if self._item is None:
            return True
        rect = self._item.boundingRect()
        view = self.viewport().rect()
        return rect.width() <= view.width() and rect.height() <= view.height()

    def _apply_fit_mode(self) -> None:
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)

    def _apply_actual_mode(self) -> None:
        if self._fits_at_actual():
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)

    def _refresh_mode(self) -> None:
        if self._mode == "fit":
            self._apply_fit_mode()
        else:
            self._apply_actual_mode()

    def fit(self) -> None:
        if self._item is None:
            return
        self.resetTransform()
        factor = self._fit_scale()
        self.scale(factor, factor)
        self._mode = "fit"
        self._apply_fit_mode()
        self._emit_scale()

    def to_actual(self, center_on_scene_point: object | None = None) -> None:
        if self._item is None:
            return
        self.resetTransform()  # scale 1.0 == 100%
        self._mode = "actual"
        self._apply_actual_mode()
        if center_on_scene_point is not None:
            self.centerOn(center_on_scene_point)
        self._emit_scale()

    # --------------------------------------------------------------- events
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._mode == "fit":
            self.fit()
        else:
            self._apply_actual_mode()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._item is None:
            return
        fit_scale = self._fit_scale()
        current = self._current_scale()
        step = _WHEEL_STEP if event.angleDelta().y() > 0 else 1 / _WHEEL_STEP
        target = max(fit_scale, min(current * step, 1.0))  # clamp fit .. 100%
        if abs(target - current) > 1e-4:
            self.scale(target / current, target / current)
        self._mode = "fit" if target <= fit_scale + 1e-6 else "actual"
        self._refresh_mode()
        self._emit_scale()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton or self._press_pos is None:
            return
        pos = event.position().toPoint()
        moved = (pos - self._press_pos).manhattanLength()
        self._press_pos = None
        if moved > _CLICK_SLOP:
            return  # a drag (pan), not a click
        if self._mode == "fit":
            self.to_actual(self.mapToScene(pos))
        else:
            self.fit()
