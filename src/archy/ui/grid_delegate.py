from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from archy.i18n import tr
from archy.services.formatting import format_size
from archy.ui import theme
from archy.ui.archive_model import CATEGORY_ICON, NODE_ROLE, category_of

# Soft Depth grid tile:
#  - a floating rounded card (r-md) with a top-lit glass gradient body;
#  - a full-bleed cover thumbnail (or a tinted category icon) filling the top;
#  - name + mono size on a strip below;
#  - selection = accent border + 2px ring + a soft teal glow (not a flat ring);
#  - a 3D gradient check badge top-right, a rounded type badge top-left, and a
#    play badge + duration for video.

# Type badge accent colors (mockup: video=warning, doc/other=error/muted).
_BADGE_COLOR = {
    "video": theme.WARNING,
    "document": theme.ERROR,
    "other": theme.TEXT_SECONDARY,
}


class GridDelegate(QStyledItemDelegate):
    def __init__(self, loader, parent=None, *, large: bool = False) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._loader = loader
        self._large = large
        self._radius = 13
        # In archive-search results, the secondary line shows the parent path
        # instead of the size, so a deep hit stays identifiable.
        self._search_mode = False
        self._compact = False
        self._apply_metrics()

    def _apply_metrics(self) -> None:
        base = (204, 190, 56, 46) if self._large else (156, 150, 44, 42)
        if self._compact:
            w, h, icon, label = base
            self._cell_w, self._cell_h = int(w * 0.82), int(h * 0.82)
            self._icon, self._label_h = int(icon * 0.85), int(label * 0.9)
        else:
            self._cell_w, self._cell_h, self._icon, self._label_h = base

    def set_search_mode(self, on: bool) -> None:
        self._search_mode = on

    def set_compact(self, on: bool) -> None:
        if on != self._compact:
            self._compact = on
            self._apply_metrics()

    def sizeHint(self, option, index) -> QSize:  # noqa: ANN001, N802
        return QSize(self._cell_w, self._cell_h)

    def paint(self, painter, option, index) -> None:  # noqa: ANN001, N802
        node = index.data(NODE_ROLE)
        if node is None:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        r = self._radius

        card = QRectF(rect).adjusted(6, 6, -6, -6)
        image_rect = QRectF(card)
        image_rect.setHeight(card.height() - self._label_h)

        # Selection glow first (drawn behind the card): a soft teal halo.
        if selected:
            ar, ag, ab = theme.ACCENT_RGB
            for grow, alpha in ((7, 26), (4, 40), (2, 60)):
                glow = card.adjusted(-grow, -grow, grow, grow)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(ar, ag, ab, alpha))
                painter.drawRoundedRect(glow, r + grow, r + grow)
        else:
            # Soft drop shadow (stacked translucent rounded rects, no blur).
            for i, alpha in ((3, 24), (2, 30), (1, 36)):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(0, 0, 0, alpha))
                painter.drawRoundedRect(card.translated(0, i), r, r)

        # Card body: top-lit glass gradient.
        body = QLinearGradient(card.topLeft(), card.bottomLeft())
        body.setColorAt(0.0, QColor(255, 255, 255, 20 if hovered else 13))
        body.setColorAt(1.0, QColor(255, 255, 255, 6))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(body)
        painter.drawRoundedRect(card, r, r)

        cat = category_of(node)
        pixmap = self._loader.thumbnail(node.entry) if node.entry is not None else None
        top_path = self._top_rounded_path(image_rect, r)
        if pixmap is not None and not pixmap.isNull():
            self._draw_cover(painter, pixmap, image_rect, top_path)
        else:
            painter.fillPath(top_path, QColor(255, 255, 255, 8))
            self._draw_center_icon(painter, cat, image_rect, selected)

        if cat == "video" and pixmap is not None and not pixmap.isNull():
            self._draw_play(painter, image_rect)
        if cat in _BADGE_COLOR:
            self._draw_type_badge(painter, image_rect, cat)

        # Top highlight line just inside the card top edge.
        painter.setPen(QColor(255, 255, 255, 16))
        painter.drawLine(
            int(card.left() + r), int(card.top() + 1), int(card.right() - r), int(card.top() + 1)
        )

        self._draw_label(painter, node, card, selected)

        # Card outline / selection ring on top.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if selected:
            ar, ag, ab = theme.ACCENT_RGB
            pen = painter.pen()
            pen.setColor(QColor(ar, ag, ab, 200))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRoundedRect(card.adjusted(1, 1, -1, -1), r - 1, r - 1)
            self._draw_check(painter, image_rect)
        else:
            painter.setPen(QColor(255, 255, 255, 20))
            painter.drawRoundedRect(card, r, r)
        painter.restore()

    # ---------------------------------------------------------------- helpers
    def _top_rounded_path(self, rect: QRectF, r: int) -> QPainterPath:
        path = QPainterPath()
        path.moveTo(rect.left(), rect.bottom())
        path.lineTo(rect.left(), rect.top() + r)
        path.quadTo(rect.left(), rect.top(), rect.left() + r, rect.top())
        path.lineTo(rect.right() - r, rect.top())
        path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + r)
        path.lineTo(rect.right(), rect.bottom())
        path.closeSubpath()
        return path

    def _draw_cover(self, painter, pixmap, rect: QRectF, clip: QPainterPath) -> None:  # noqa: ANN001
        target = rect.toRect()
        scaled = pixmap.scaled(
            target.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        sx = (scaled.width() - target.width()) // 2
        sy = (scaled.height() - target.height()) // 2
        painter.save()
        painter.setClipPath(clip)
        painter.drawPixmap(target, scaled, QRect(sx, sy, target.width(), target.height()))
        painter.restore()

    def _draw_center_icon(self, painter, cat: str, rect: QRectF, selected: bool) -> None:  # noqa: ANN001
        color = theme.TEXT_SECONDARY if selected else theme.TEXT_DISABLED
        icon = theme.symbol_icon(CATEGORY_ICON.get(cat, "doc"), self._icon, color)
        cx = int(rect.center().x() - self._icon / 2)
        cy = int(rect.center().y() - self._icon / 2)
        icon.paint(painter, QRect(cx, cy, self._icon, self._icon))

    def _draw_play(self, painter, rect: QRectF) -> None:  # noqa: ANN001
        r = 17
        c = rect.center()
        painter.setPen(QColor(255, 255, 255, 72))
        painter.setBrush(QColor(12, 17, 22, 140))
        painter.drawEllipse(c, r, r)
        glyph = 15
        theme.symbol_icon("play", glyph, "#FFFFFF").paint(
            painter, QRect(int(c.x() - glyph / 2 + 1), int(c.y() - glyph / 2), glyph, glyph)
        )

    def _draw_type_badge(self, painter, rect: QRectF, cat: str) -> None:  # noqa: ANN001
        size = 20
        x = int(rect.left()) + 7
        y = int(rect.top()) + 7
        painter.setPen(QColor(255, 255, 255, 26))
        painter.setBrush(QColor(10, 14, 18, 140))
        painter.drawRoundedRect(QRect(x, y, size, size), 7, 7)
        icon_name = {"video": "video", "document": "doc", "other": "camera"}.get(cat, "doc")
        theme.symbol_icon(icon_name, 12, _BADGE_COLOR[cat]).paint(
            painter, QRect(x + 4, y + 4, 12, 12)
        )

    def _draw_check(self, painter, rect: QRectF) -> None:  # noqa: ANN001
        size = 18
        x = int(rect.right()) - size - 7
        y = int(rect.top()) + 7
        # 3D gradient badge: accent-hi -> accent, with a top highlight.
        grad = QLinearGradient(x, y, x + size, y + size)
        grad.setColorAt(0.0, QColor(theme.ACCENT_HOVER))
        grad.setColorAt(1.0, QColor(theme.ACCENT))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 90))
        painter.drawEllipse(QRect(x, y + 1, size, size))  # drop shadow
        painter.setBrush(grad)
        painter.drawEllipse(QRect(x, y, size, size))
        painter.setPen(QColor(255, 255, 255, 90))  # inner top highlight
        painter.drawArc(QRect(x + 1, y + 1, size - 2, size - 2), 30 * 16, 120 * 16)
        theme.symbol_icon("check", 11, theme.ACCENT_ON).paint(painter, QRect(x + 3, y + 3, 12, 12))

    def _draw_label(self, painter, node, card: QRectF, selected: bool) -> None:  # noqa: ANN001
        left = int(card.left()) + 9
        width = int(card.width()) - 18
        name_y = int(card.bottom() - self._label_h + 7)
        painter.setPen(QColor(theme.TEXT_PRIMARY))
        name_font = painter.font()
        name_font.setPointSizeF(9.0)
        painter.setFont(name_font)
        fm = painter.fontMetrics()
        name = fm.elidedText(node.name, Qt.TextElideMode.ElideMiddle, width)
        painter.drawText(QRect(left, name_y, width, 16), Qt.AlignmentFlag.AlignVCenter, name)
        subtitle = self._subtitle(node)
        if subtitle is not None:
            # On the teal selection the muted grey disappears; use the light
            # text colour, a little softer than the name.
            muted = QColor(theme.TEXT_PRIMARY if selected else theme.TEXT_DISABLED)
            if selected:
                muted.setAlpha(205)
            painter.setPen(muted)
            mono = QFont(theme.MONO_FAMILY)
            mono.setPointSizeF(8.0)
            painter.setFont(mono)
            sub = painter.fontMetrics().elidedText(subtitle, Qt.TextElideMode.ElideLeft, width)
            painter.drawText(
                QRect(left, name_y + 17, width, 14), Qt.AlignmentFlag.AlignVCenter, sub
            )

    def _subtitle(self, node) -> str | None:  # noqa: ANN001
        if self._search_mode:
            parent = node.path.rstrip("/").rsplit("/", 1)[0] if "/" in node.path.rstrip("/") else ""
            return f"/{parent}" if parent else tr("grid.root")
        if not node.is_dir:
            return format_size(node.size)
        return None
