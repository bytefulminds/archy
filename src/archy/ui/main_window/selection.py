from __future__ import annotations

from PySide6.QtCore import (
    QItemSelection,
    QModelIndex,
)

from archy.core.tree import TreeNode
from archy.i18n import tr
from archy.services.formatting import format_modified, format_size


class SelectionMixin:
    """Selection state and double-click handling.

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    def _selected_nodes(self) -> list[TreeNode]:
        nodes: list[TreeNode] = []
        for index in self._selection.selectedRows():
            node = self._node_at(index)
            if node is not None:
                nodes.append(node)
        if not nodes:
            current = self._selection.currentIndex()
            node = self._node_at(current) if current.isValid() else None
            if node is not None:
                nodes.append(node)
        return nodes

    # ------------------------------------------------------------ selection
    def _node_at(self, proxy_index: QModelIndex) -> TreeNode | None:
        source = self._proxy.mapToSource(proxy_index)
        return self._model.node_for_row(source.row())

    def _on_selection_changed(self, selected: QItemSelection, _deselected: QItemSelection) -> None:
        self._stage_editor()  # keep any unsaved text edits before switching files
        self._req_id += 1  # cancel any in-flight preview read
        self._update_edit_actions()

        rows = self._selection.selectedRows()
        multi = len(rows) > 1
        if multi:
            # Multi-select: summarise in the status strip; the preview follows the
            # current (last-clicked) item below.
            self._status_selection.setText(
                tr("status.selected_of", count=len(rows), total=self._model.rowCount())
            )
        elif not rows and not self._selection.currentIndex().isValid():
            self._status_selection.setText(tr("status.no_selection"))

        # The preview always follows the current item (the one just clicked), not
        # the whole multi-selection.
        current = self._selection.currentIndex()
        node = self._node_at(current) if current.isValid() else None
        if node is None and rows:
            node = self._node_at(rows[0])
        if node is None:
            return
        if node.is_dir:
            self._info_button.setEnabled(False)
            self._set_preview_title(node.name, tr("count.items", count=len(node.children)))
            self._set_footer(node)
            self._set_status(tr("status.folder_hint", name=node.name))
            self._show_placeholder(tr("preview.folder_hint"))
        else:
            self._info_button.setEnabled(True)
            # Type and size are in the footer; until the preview knows more
            # (pixels, pages, length), the line under the name shows the date.
            sub = (
                tr("details.modified", value=format_modified(node.modified))
                if node.modified is not None
                else ""
            )
            self._set_preview_title(node.name, sub)
            self._set_footer(node)
            self._set_status(f"{node.path} - {format_size(node.size)}")
            if not multi:
                self._status_selection.setText(tr("status.selected_one", name=node.name))
            self._preview_node(node, self._req_id)

    def _on_double_clicked(self, proxy_index: QModelIndex) -> None:
        node = self._node_at(proxy_index)
        if node is None:
            return
        if self._searching:
            # From a search result, jump to where the item lives.
            self._reveal_node(node)
            return
        if node.is_dir:
            self._enter_folder(node)
