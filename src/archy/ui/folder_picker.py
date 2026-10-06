from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtWidgets import QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from archy.core.tree import TreeNode
from archy.i18n import tr
from archy.ui import theme

# The breadcrumb's folder picker: the folders at one level of the path, with a
# field to type in. A backup can have hundreds of folders side by side, so the
# list shows at most MAX_SHOWN and typing narrows it down.

MAX_SHOWN = 20
_ROWS_VISIBLE = 10


class FolderPicker(QFrame):
    """A small popup: a filter field over a list of folders. Enter or a
    click opens the folder; Esc or a click elsewhere closes it."""

    def __init__(
        self,
        folders: list[TreeNode],
        current: str | None,
        on_pick: Callable[[TreeNode], None],
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("folderPicker")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._folders = sorted(folders, key=lambda node: node.name.casefold())
        self._current = current
        self._on_pick = on_pick
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        self.field = QLineEdit()
        self.field.setObjectName("folderPickerField")
        self.field.setPlaceholderText(tr("crumb.filter", count=len(self._folders)))
        self.field.setClearButtonEnabled(True)
        self.field.textChanged.connect(self._refill)
        self.field.installEventFilter(self)
        self.list = QListWidget()
        self.list.setObjectName("folderPickerList")
        self.list.setUniformItemSizes(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemActivated.connect(self._pick)
        self.list.itemClicked.connect(self._pick)
        self.more = QLabel()
        self.more.setObjectName("folderPickerMore")
        layout.addWidget(self.field)
        layout.addWidget(self.list)
        layout.addWidget(self.more)
        self.setFixedWidth(300)
        self._refill("")

    def matches(self) -> list[str]:
        """The folder names shown now (for tests)."""
        return [self.list.item(i).text() for i in range(self.list.count())]

    def _refill(self, text: str) -> None:
        needle = text.strip().casefold()
        found = self._folders
        if needle:
            found = [node for node in self._folders if needle in node.name.casefold()]
        self.list.clear()
        icon = theme.symbol_icon("folder", 16, theme.ACCENT)
        start = 0
        if not needle and self._current is not None:
            # Show the folder we are in, with a few before it for context.
            names = [node.name for node in found]
            if self._current in names and names.index(self._current) >= MAX_SHOWN:
                start = max(0, min(names.index(self._current) - 5, len(found) - MAX_SHOWN))
        for node in found[start : start + MAX_SHOWN]:
            item = QListWidgetItem(icon, node.name)
            item.setData(Qt.ItemDataRole.UserRole, node)
            self.list.addItem(item)
            if node.name == self._current:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        if self.list.currentItem() is not None:
            self.list.scrollToItem(self.list.currentItem())
        hidden = len(found) - MAX_SHOWN
        if hidden > 0:
            self.more.setText(tr("crumb.more_filter", count=hidden))
        elif not found:
            self.more.setText(tr("crumb.no_match"))
        self.more.setVisible(hidden > 0 or not found)
        rows = max(1, min(self.list.count(), _ROWS_VISIBLE))
        row_height = self.list.sizeHintForRow(0) if self.list.count() else 24
        self.list.setFixedHeight(rows * row_height + 2 * self.list.frameWidth() + 4)
        self.adjustSize()

    def _pick(self, item: QListWidgetItem) -> None:
        node = item.data(Qt.ItemDataRole.UserRole)
        self.close()
        if node is not None:
            self._on_pick(node)

    def eventFilter(self, watched, event) -> bool:  # noqa: ANN001, N802 - Qt override
        if watched is self.field and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                step = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1}.get(key, 0)
                step = step or (_ROWS_VISIBLE if key == Qt.Key.Key_PageDown else -_ROWS_VISIBLE)
                row = max(0, min(self.list.count() - 1, self.list.currentRow() + step))
                self.list.setCurrentRow(row)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.list.currentItem()
                if item is not None:
                    self._pick(item)
                return True
            if key == Qt.Key.Key_Escape:
                self.close()
                return True
        return super().eventFilter(watched, event)

    def popup(self, at: QPoint) -> None:
        self.move(at)
        self.show()
        self.field.setFocus()
