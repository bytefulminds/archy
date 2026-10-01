from __future__ import annotations

from archy.core.tree import TreeNode
from archy.i18n import tr


class SearchMixin:
    """In-folder and whole-archive search.

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    # --------------------------------------------------------------- search
    def _focus_search(self, scope: str) -> None:
        if self._backend is None:
            return
        self._search_scope = scope
        self._search.setPlaceholderText(
            tr("header.search_archive") if scope == "archive" else tr("header.search_folder")
        )
        self._search.setFocus()
        self._search.selectAll()
        # Re-run with the current text under the new scope.
        self._on_search_text(self._search.text())

    def _on_search_text(self, text: str) -> None:
        if self._search_scope == "archive":
            if not text.strip():
                # Clearing the box should restore the folder view immediately.
                self._search_timer.stop()
                if self._searching:
                    self._exit_search()
                return
            self._search_timer.start()  # debounced -> _apply_archive_search
        else:
            if self._searching:
                self._exit_search()
            self._proxy.set_name_filter(text)

    def _apply_archive_search(self) -> None:
        self._run_archive_search(self._search.text())

    def _run_archive_search(self, text: str) -> None:
        query = text.strip().casefold()
        if not query or self._backend is None or not self._nav:
            if self._searching:
                self._exit_search()
            return
        self._proxy.set_name_filter("")  # the flat result list is already filtered
        matches = [node for node in self._walk_nodes(self._nav[0]) if query in node.path.casefold()]
        self._searching = True
        for view in (self._grid, self._large):
            view.itemDelegate().set_search_mode(True)
        self._model.set_rows(matches)
        self._rebuild_breadcrumb()
        self._show_placeholder(tr("preview.select_result"))
        self._set_status(tr("status.search_results", count=len(matches), query=text.strip()))

    def _walk_nodes(self, root: TreeNode):  # noqa: ANN202
        for child in root.children.values():
            yield child
            if child.is_dir:
                yield from self._walk_nodes(child)

    def _exit_search(self) -> None:
        self._search_timer.stop()
        if not self._searching:
            return
        self._searching = False
        for view in (self._grid, self._large):
            view.itemDelegate().set_search_mode(False)
        self._refresh_folder()

    def _reveal_node(self, node: TreeNode) -> None:
        """Leave search and navigate to where ``node`` lives, selecting it."""
        self._search_timer.stop()
        target = node.path
        want_folder = not node.is_dir
        parts = target.rstrip("/").split("/")
        folder_parts = parts[:-1] if want_folder else parts
        self._searching = False
        self._search_scope = "archive"
        for view in (self._grid, self._large):
            view.itemDelegate().set_search_mode(False)
        self._search.blockSignals(True)
        self._search.clear()
        self._search.setPlaceholderText(tr("header.search"))
        self._search.blockSignals(False)
        self._proxy.set_name_filter("")
        node_cursor = self._nav[0]
        nav = [node_cursor]
        for part in folder_parts:
            child = node_cursor.children.get(part)
            if child is None or not child.is_dir:
                break
            nav.append(child)
            node_cursor = child
        self._nav = nav
        self._refresh_folder()
        if want_folder:
            self._select_path(target)
