from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

from archy.core.tree import TreeNode
from archy.i18n import tr
from archy.services.formatting import format_modified, format_ratio, format_size
from archy.services.name_stamp import name_timestamp
from archy.services.preview import PreviewKind, kind_for_name
from archy.ui import theme

# A details table over the children of one folder in the archive tree. The
# window drives navigation (which folder is current); this model just lists that
# folder's entries. Sorting is handled by FolderSortProxy, which keeps folders
# above files regardless of the sort column.

_COLUMNS = (
    "column.name",
    "column.size",
    "column.compressed",
    "column.ratio",
    "column.type",
    "column.modified",
    "column.name_time",  # hidden in the table; a sort choice in the sort menu
)
_NAME, _SIZE, _COMPRESSED, _RATIO, _TYPE, _MODIFIED, _NAME_TIME = range(7)
NAME_TIME_COLUMN = _NAME_TIME

SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
IS_DIR_ROLE = int(Qt.ItemDataRole.UserRole) + 2
NODE_ROLE = int(Qt.ItemDataRole.UserRole) + 3
CATEGORY_ROLE = int(Qt.ItemDataRole.UserRole) + 4

_DOC_EXT = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
    ".csv",
    ".tsv",
    ".md",
    ".markdown",
    ".rst",
    ".txt",
    ".log",
    # Web pages and data files: read like documents, previewed as one.
    ".html",
    ".htm",
    ".xhtml",
    ".xml",
    ".json",
    ".jsonl",
    ".geojson",
    ".yaml",
    ".yml",
    ".toml",
}

# Shared empty index for default parent args (avoids a call in defaults; B008).
_ROOT_INDEX = QModelIndex()

# Category -> Material Symbols icon name (shared by the table/list decoration and
# the grid delegate so every view uses the same iconography).
CATEGORY_ICON = {
    "dir": "folder",
    "image": "image",
    "video": "video",
    "document": "doc",
    "other": "doc",
}


def icon_of(node: TreeNode) -> str:
    """The icon for a node in the lists and tiles: by category, with a note
    for music (which the sidebar counts under Other)."""
    cat = category_of(node)
    if cat == "other" and kind_for_name(node.name) is PreviewKind.AUDIO:
        return "music"
    return CATEGORY_ICON.get(cat, "doc")


def category_of(node: TreeNode) -> str:
    """Coarse category for the sidebar filter: dir/image/video/document/other."""
    if node.is_dir:
        return "dir"
    kind = kind_for_name(node.name)
    if kind is PreviewKind.IMAGE:
        return "image"
    if kind is PreviewKind.VIDEO:
        return "video"
    dot = node.name.rfind(".")
    ext = node.name[dot:].lower() if dot != -1 else ""
    if kind is PreviewKind.PDF or ext in _DOC_EXT:
        return "document"
    return "other"


def _type_label(node: TreeNode) -> str:
    if node.is_dir:
        return tr("kind.dir")
    name = node.name
    if "." in name:
        return name.rsplit(".", 1)[-1].upper()
    return tr("kind.other")


def _compressed_of(node: TreeNode) -> int | None:
    if node.entry is None:
        return None
    return node.entry.compressed_size


class ArchiveTableModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._rows: list[TreeNode] = []

    def set_folder(self, folder: TreeNode) -> None:
        self.beginResetModel()
        self._rows = list(folder.children.values())
        self.endResetModel()

    def set_rows(self, nodes: list[TreeNode]) -> None:
        """Show an explicit flat list of nodes (used for archive-wide search
        results, which are not the children of a single folder)."""
        self.beginResetModel()
        self._rows = list(nodes)
        self.endResetModel()

    def node_for_row(self, row: int) -> TreeNode | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    # ------------------------------------------------------------- Qt model
    def rowCount(self, parent: QModelIndex = _ROOT_INDEX) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = _ROOT_INDEX) -> int:
        return len(_COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = self._rows[index.row()]
        col = index.column()
        compressed = _compressed_of(node)

        if role == Qt.ItemDataRole.DisplayRole:
            if col == _NAME:
                return node.name
            if col == _TYPE:
                return _type_label(node)
            if node.is_dir:
                return ""
            if col == _SIZE:
                return format_size(node.size)
            if col == _COMPRESSED:
                return format_size(compressed) if compressed is not None else ""
            if col == _RATIO:
                return format_ratio(node.size, compressed)
            if col == _MODIFIED:
                return format_modified(node.modified)
            if col == _NAME_TIME:
                stamp = name_timestamp(node.name)
                return "" if stamp is None else format_modified(datetime.fromtimestamp(stamp))
            return None

        if role == SORT_ROLE:
            if col == _NAME:
                return node.name.casefold()
            if col == _SIZE:
                return node.size
            if col == _COMPRESSED:
                return compressed if compressed is not None else -1
            if col == _RATIO:
                if compressed is not None and node.size > 0:
                    return compressed / node.size
                return -1.0
            if col == _TYPE:
                return _type_label(node).casefold()
            if col == _MODIFIED:
                return node.modified.timestamp() if node.modified is not None else 0.0
            if col == _NAME_TIME:
                # Names with a time first, oldest to newest; the others by name.
                stamp = name_timestamp(node.name)
                return (stamp is not None, stamp or 0.0, node.name.casefold())
            return None

        if role == IS_DIR_ROLE:
            return node.is_dir

        if role == NODE_ROLE:
            return node

        if role == CATEGORY_ROLE:
            return category_of(node)

        if role == Qt.ItemDataRole.TextAlignmentRole and col in (_SIZE, _COMPRESSED, _RATIO):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if (
            role == Qt.ItemDataRole.ForegroundRole
            and col == _RATIO
            and not node.is_dir
            and compressed is not None
            and node.size > 0
        ):
            return theme.ratio_color(compressed / node.size)

        if role == Qt.ItemDataRole.DecorationRole and col == _NAME:
            cat = category_of(node)
            color = theme.ACCENT if cat == "dir" else theme.TEXT_SECONDARY
            return theme.symbol_icon(icon_of(node), 18, color)

        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(_COLUMNS[section])
        return None


class FolderSortProxy(QSortFilterProxyModel):
    """Sorts by the active column but always keeps folders above files."""

    def __init__(self) -> None:
        super().__init__()
        self.setSortRole(SORT_ROLE)
        self._category = "all"
        self._name_filter = ""

    def set_category(self, category: str) -> None:
        if category != self._category:
            self._category = category
            self.invalidateFilter()

    def set_name_filter(self, text: str) -> None:
        text = text.strip().casefold()
        if text != self._name_filter:
            self._name_filter = text
            self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        if self._name_filter:
            name = model.data(index, Qt.ItemDataRole.DisplayRole) or ""
            if self._name_filter not in name.casefold():
                return False
        if self._category == "all":
            return True
        node_category = model.data(index, CATEGORY_ROLE)
        # Folders always show so navigation still works under any filter.
        return node_category == "dir" or node_category == self._category

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        model = self.sourceModel()
        left_dir = bool(model.data(model.index(left.row(), 0), IS_DIR_ROLE))
        right_dir = bool(model.data(model.index(right.row(), 0), IS_DIR_ROLE))
        if left_dir != right_dir:
            # Folders first in ascending order; the view flips for descending,
            # but folders-on-top still reads naturally either way.
            top = self.sortOrder() == Qt.SortOrder.AscendingOrder
            return left_dir if top else right_dir

        lv = model.data(left, SORT_ROLE)
        rv = model.data(right, SORT_ROLE)
        if left.column() == _NAME_TIME and lv[0] != rv[0]:
            # Names with a time above the others, in either order (like folders).
            top = self.sortOrder() == Qt.SortOrder.AscendingOrder
            return lv[0] if top else rv[0]
        if lv == rv and left.column() != 0:
            # Equal sizes or dates (folders have none): A to Z by name, also
            # when sorting descending, so the order is stable and readable.
            lname = model.data(model.index(left.row(), 0), SORT_ROLE)
            rname = model.data(model.index(right.row(), 0), SORT_ROLE)
            if self.sortOrder() == Qt.SortOrder.DescendingOrder:
                return rname < lname
            return lname < rname
        try:
            return lv < rv
        except TypeError:
            return str(lv) < str(rv)
