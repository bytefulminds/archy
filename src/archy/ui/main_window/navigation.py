from __future__ import annotations

from PySide6.QtCore import (
    QItemSelectionModel,
    QSize,
    Qt,
)
from PySide6.QtWidgets import (
    QLabel,
    QToolButton,
)

from archy.core.tree import TreeNode
from archy.i18n import tr
from archy.services.formatting import format_size
from archy.ui import theme
from archy.ui.archive_model import (
    category_of,
)
from archy.ui.main_window.constants import (
    _CATEGORIES,
)
from archy.ui.settings import view_labels


class NavigationMixin:
    """Folder navigation and the breadcrumb.

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    # ------------------------------------------------------------ navigation
    def go_up(self) -> None:
        if self._searching:
            self._exit_search()
            return
        if len(self._nav) > 1:
            self._nav.pop()
            self._refresh_folder()

    def _enter_folder(self, node: TreeNode) -> None:
        self._nav.append(node)
        self._refresh_folder()

    def _go_to_depth(self, depth: int) -> None:
        # depth 0 == archive root; clicking the current segment does nothing.
        if 0 <= depth < len(self._nav) - 1:
            del self._nav[depth + 1 :]
            self._refresh_folder()

    def _refresh_folder(self) -> None:
        current = self._nav[-1]
        self._model.set_folder(current)
        self._thumbs.clear()
        self._prefetcher.start_folder()
        # In a thumbnail view the folder is needed right away: start the folder
        # pass now (not on first click). It unpacks the folder and makes every
        # thumbnail with one indicator, so the folder then browses instantly.
        self._thumbs.set_active(self._thumb_view_active())
        self._thumbs.set_folder(self._display_folder_files())
        if self._thumb_view_active():
            self._warm_folder()
        self._rebuild_breadcrumb()
        self._show_placeholder(tr("preview.select_file"))
        self._update_counts()
        self._update_nav_state()

    def _rebuild_breadcrumb(self) -> None:
        while self._breadcrumb_layout.count():
            item = self._breadcrumb_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self._nav:
            placeholder = QLabel(tr("crumb.no_archive"))
            placeholder.setObjectName("breadcrumbMuted")
            self._breadcrumb_layout.addWidget(placeholder)
            self._breadcrumb_layout.addStretch(1)
            return

        if self._searching:
            icon = QLabel()
            icon.setPixmap(theme.symbol_icon("search", 20, theme.ACCENT).pixmap(20, 20))
            self._breadcrumb_layout.addWidget(icon)
            label = QLabel(tr("crumb.search_results"))
            label.setObjectName("crumb")
            self._breadcrumb_layout.addWidget(label)
            self._breadcrumb_layout.addStretch(1)
            self._add_breadcrumb_close()
            return

        icon = QLabel()
        icon.setPixmap(theme.symbol_icon("folder_zip", 20, theme.ACCENT).pixmap(20, 20))
        self._breadcrumb_layout.addWidget(icon)

        segments = [
            self._archive_label or tr("crumb.archive_fallback"),
            *[n.name for n in self._nav[1:]],
        ]
        last = len(segments) - 1
        for depth, name in enumerate(segments):
            if depth > 0:
                sep = QLabel("/")
                sep.setObjectName("crumbSep")
                self._breadcrumb_layout.addWidget(sep)
            crumb = QToolButton()
            crumb.setObjectName("crumb")
            crumb.setAutoRaise(True)
            crumb.setText(name)
            if depth == last:
                crumb.setEnabled(False)
            else:
                crumb.clicked.connect(lambda _c=False, d=depth: self._go_to_depth(d))
            self._breadcrumb_layout.addWidget(crumb)
        self._breadcrumb_layout.addStretch(1)
        self._add_breadcrumb_close()

    def _add_breadcrumb_close(self) -> None:
        # Close the current archive (back to the startup state, frees memory).
        close_btn = QToolButton()
        close_btn.setObjectName("crumbClose")
        close_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        close_btn.setIcon(theme.symbol_icon("x", 15, theme.TEXT_SECONDARY))
        close_btn.setIconSize(QSize(15, 15))
        close_btn.setText(tr("crumb.close"))
        close_btn.setToolTip(tr("crumb.close_tip"))
        close_btn.clicked.connect(self.close_archive)
        self._breadcrumb_layout.addWidget(close_btn)

    def _update_nav_state(self) -> None:
        can_up = len(self._nav) > 1
        self._up_action.setEnabled(can_up)

    def _update_counts(self) -> None:
        rows = [self._model.node_for_row(r) for r in range(self._model.rowCount())]
        rows = [n for n in rows if n is not None]
        totals = {key: 0 for key, _icon in _CATEGORIES}
        totals["all"] = len(rows)
        for node in rows:
            cat = category_of(node)
            if cat in totals:
                totals[cat] += 1
        for key, (_button, label) in self._cat_buttons.items():
            label.setText(str(totals.get(key, 0)))

        visible = self._proxy.rowCount()
        folder_bytes = sum(n.size for n in rows if not n.is_dir)
        self._status_items.setText(tr("count.items", count=visible))
        self._status_size.setText(format_size(folder_bytes))
        view_label = view_labels()[self._views.currentIndex()]
        self._folder_hint.setText(tr("count.items_in_folder", view=view_label, count=visible))

    def _select_path(self, path: str) -> None:
        for r in range(self._model.rowCount()):
            node = self._model.node_for_row(r)
            if node is not None and node.path == path:
                proxy_index = self._proxy.mapFromSource(self._model.index(r, 0))
                if proxy_index.isValid():
                    self._selection.setCurrentIndex(
                        proxy_index,
                        QItemSelectionModel.SelectionFlag.ClearAndSelect
                        | QItemSelectionModel.SelectionFlag.Rows,
                    )
                return
