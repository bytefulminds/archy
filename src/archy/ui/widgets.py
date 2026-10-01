from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QCheckBox, QComboBox, QFrame

from archy.ui import theme

# Two Soft Depth form controls used by the settings screen:
#  - SoftComboBox: a QComboBox that always shows a chevron (so it reads as a
#    dropdown) and opens its list downward when there is room.
#  - SoftToggle: a slide switch (track + knob) with an unmistakable on/off state,
#    instead of a small tinted checkbox.


class SoftComboBox(QComboBox):
    """QComboBox with a visible chevron affordance and a downward-opening list."""

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMaxVisibleItems(8)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        size = 14
        x = self.width() - size - 8
        y = (self.height() - size) // 2
        icon = theme.symbol_icon("chev_down", size, theme.TEXT_SECONDARY)
        icon.paint(painter, x, y, size, size)
        painter.end()

    def showPopup(self) -> None:  # noqa: N802 - Qt override
        super().showPopup()
        # Qt may drop the list upward when it thinks there is no room; force it
        # below the field whenever the screen actually has room, so it always
        # reads as a normal dropdown.
        container = self.findChild(QFrame)
        if container is None:
            return
        below = self.mapToGlobal(QPoint(0, self.height()))
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None and below.y() + container.height() <= screen.bottom():
            container.move(below.x(), below.y())


class SoftToggle(QCheckBox):
    """A checkable slide switch. Keeps the QCheckBox API (isChecked/setChecked/
    toggled), but paints a track + knob so the active state is clear at a glance,
    not only by color."""

    _TRACK_W = 42
    _TRACK_H = 22
    _KNOB = 16
    _GAP = 10  # space between switch and label text

    def __init__(self, text: str = "", parent=None) -> None:  # noqa: ANN001
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text()) if self.text() else 0
        width = self._TRACK_W + (self._GAP + text_w if text_w else 0)
        return QSize(width, max(self._TRACK_H, fm.height()))

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        on = self.isChecked()
        enabled = self.isEnabled()
        track = QRectF(0, (self.height() - self._TRACK_H) / 2, self._TRACK_W, self._TRACK_H)

        if not enabled:
            # Clearly inactive: muted grey track/knob, but keep the knob position
            # so the stored on/off state is still legible.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 14))
            painter.drawRoundedRect(track, self._TRACK_H / 2, self._TRACK_H / 2)
        elif on:
            painter.setBrush(QColor(theme.ACCENT))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(track, self._TRACK_H / 2, self._TRACK_H / 2)
        else:
            painter.setBrush(QColor(255, 255, 255, 18))
            pen = painter.pen()
            pen.setColor(QColor(255, 255, 255, 46))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawRoundedRect(track, self._TRACK_H / 2, self._TRACK_H / 2)

        # Knob
        margin = (self._TRACK_H - self._KNOB) / 2
        knob_x = track.right() - self._KNOB - margin if on else track.left() + margin
        knob = QRectF(knob_x, track.top() + margin, self._KNOB, self._KNOB)
        if not enabled:
            knob_color = QColor(theme.TEXT_DISABLED)
        else:
            knob_color = QColor(theme.ACCENT_ON) if on else QColor("#C6D0D8")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob_color)
        painter.drawEllipse(knob)

        # Label text to the right of the switch
        if self.text():
            color = QColor(theme.TEXT_PRIMARY if enabled else theme.TEXT_DISABLED)
            painter.setPen(color)
            text_rect = QRectF(
                self._TRACK_W + self._GAP,
                0,
                self.width() - self._TRACK_W - self._GAP,
                self.height(),
            )
            painter.drawText(
                text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text()
            )
        painter.end()
