from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from archy import __version__
from archy.i18n import tr
from archy.ui.theme import palette

# A small card shown while the window is being built, so a slow (cold) start
# does not look like Archy is not responding. It appears as soon as the
# QApplication exists and closes when the main window is shown.

_W, _H = 360, 220
_ICON = Path(__file__).resolve().parent.parent / "resources" / "icons" / "archy.png"


def _card(ratio: float) -> QPixmap:
    pixmap = QPixmap(int(_W * ratio), int(_H * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    card = QRectF(0.5, 0.5, _W - 1, _H - 1)
    body = QLinearGradient(card.topLeft(), card.bottomLeft())
    body.setColorAt(0.0, QColor(palette._WIN_TOP))
    body.setColorAt(1.0, QColor(palette._WIN_BOTTOM))
    painter.setPen(QColor(255, 255, 255, 30))
    painter.setBrush(body)
    painter.drawRoundedRect(card, 18, 18)

    logo = QPixmap(str(_ICON))
    if not logo.isNull():
        painter.drawPixmap(QRectF((_W - 72) / 2, 34, 72, 72), logo, QRectF(logo.rect()))

    title = QFont(palette.DISPLAY_FAMILY)
    title.setPixelSize(24)
    title.setWeight(QFont.Weight.DemiBold)
    painter.setFont(title)
    painter.setPen(QColor(palette.TEXT_PRIMARY))
    painter.drawText(QRectF(0, 116, _W, 32), Qt.AlignmentFlag.AlignCenter, "Archy")

    small = QFont(palette.UI_FAMILY)
    small.setPixelSize(12)
    painter.setFont(small)
    painter.setPen(QColor(palette.TEXT_SECONDARY))
    painter.drawText(
        QRectF(0, 150, _W, 20),
        Qt.AlignmentFlag.AlignCenter,
        tr("splash.starting", version=__version__),
    )

    # A thin accent line along the bottom, in the user's accent colour.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(palette.ACCENT))
    painter.drawRoundedRect(QRectF(_W / 2 - 40, 184, 80, 3), 1.5, 1.5)
    painter.end()
    return pixmap


def show_splash(app: QApplication) -> QSplashScreen:
    """Show the starting card now (and paint it before returning)."""
    screen = app.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    splash = QSplashScreen(_card(ratio))
    splash.setObjectName("archySplash")
    splash.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    splash.show()
    splash.raise_()
    app.processEvents()
    return splash
