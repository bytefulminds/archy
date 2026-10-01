from __future__ import annotations

from PySide6.QtCore import (
    Qt,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from archy.i18n import tr
from archy.ui import theme
from archy.ui.main_window.constants import (
    _CATEGORIES,
    FOOTER_HEIGHT,
)


class SidebarMixin:
    """Builds the category sidebar and its summary.

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    # ------------------------------------------------------------- sidebar
    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("archiveSidebar")
        sidebar.setMinimumWidth(180)
        sidebar.setMaximumWidth(280)
        col = QVBoxLayout(sidebar)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        title = QLabel(tr("sidebar.title"))
        title.setObjectName("sidebarTitle")
        col.addWidget(title)

        self._category_group = QButtonGroup(self)
        self._category_group.setExclusive(True)
        self._cat_buttons: dict[str, tuple[QToolButton, QLabel]] = {}
        self._cat_icons: dict[str, tuple[QLabel, str]] = {}
        listwrap = QWidget()
        ll = QVBoxLayout(listwrap)
        ll.setContentsMargins(8, 0, 8, 0)
        ll.setSpacing(2)
        for key, icon_name in _CATEGORIES:
            button, count = self._make_category_item(icon_name, tr(f"category.{key}"), key)
            self._category_group.addButton(button)
            self._cat_buttons[key] = (button, count)
            ll.addWidget(button)
        col.addWidget(listwrap)
        self._cat_buttons["all"][0].setChecked(True)
        self._refresh_category_styles("all")

        col.addStretch(1)

        bottom = QFrame()
        bottom.setObjectName("sidebarBottom")
        bottom.setFixedHeight(FOOTER_HEIGHT)
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(2)
        self._summary_items = self._summary_row(
            bl, tr("sidebar.archive"), tr("count.items", count=0)
        )
        self._summary_size = self._summary_row(bl, tr("sidebar.size"), "0 B")
        col.addWidget(bottom)
        return sidebar

    def _make_category_item(
        self, icon_name: str, label: str, key: str
    ) -> tuple[QToolButton, QLabel]:
        button = QToolButton()
        button.setObjectName("folderItem")
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setMinimumHeight(36)
        button.clicked.connect(lambda _c=False, k=key: self._on_category_selected(k))

        lay = QHBoxLayout(button)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(theme.symbol_icon(icon_name, 22, theme.TEXT_SECONDARY).pixmap(22, 22))
        self._cat_icons[key] = (icon, icon_name)
        text = QLabel(label)
        count = QLabel("")
        count.setObjectName("folderCount")
        # Let clicks fall through the child labels to the button itself.
        for child in (icon, text, count):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(icon)
        lay.addWidget(text, stretch=1)
        lay.addWidget(count)
        return button, count

    def _refresh_category_styles(self, selected_key: str) -> None:
        """Tint the active folder's icon and count with the accent, so the
        selection reads like the Soft Depth mockup (teal icon + teal count)."""
        for key, (icon_label, icon_name) in self._cat_icons.items():
            active = key == selected_key
            color = theme.ACCENT if active else theme.TEXT_SECONDARY
            icon_label.setPixmap(theme.symbol_icon(icon_name, 22, color).pixmap(22, 22))
        for key, (_button, count) in self._cat_buttons.items():
            active = key == selected_key
            count.setStyleSheet(
                f"color: {theme.ACCENT};" if active else f"color: {theme.TEXT_DISABLED};"
            )

    def _summary_row(self, layout: QVBoxLayout, label: str, value: str) -> QLabel:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        key = QLabel(label)
        key.setObjectName("summaryKey")
        val = QLabel(value)
        val.setObjectName("summaryValue")
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(key)
        row.addWidget(val, stretch=1)
        layout.addLayout(row)
        return val

    def _on_category_selected(self, key: str) -> None:
        self._proxy.set_category(key)
        self._refresh_category_styles(key)
