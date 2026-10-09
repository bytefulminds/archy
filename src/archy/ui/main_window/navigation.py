from __future__ import annotations

from PySide6.QtCore import (
    QEvent,
    QItemSelectionModel,
    QObject,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QSizePolicy,
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


class MouseNavFilter(QObject):
    """The mouse's back and forward buttons go back and forward through the
    folders, wherever the pointer is in the window."""

    def __init__(self, on_step) -> None:  # noqa: ANN001 - (step: int) -> None
        super().__init__()
        self._on_step = on_step

    def eventFilter(self, _obj, event) -> bool:  # noqa: ANN001, N802 - Qt override
        if event.type() == QEvent.Type.MouseButtonPress:
            button = event.button()
            if button == Qt.MouseButton.BackButton:
                self._on_step(-1)
                return True
            if button == Qt.MouseButton.ForwardButton:
                self._on_step(1)
                return True
        return False


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
        elif self._outer_levels:
            self._leave_nested(len(self._outer_levels) - 1)  # up out of it

    def _enter_folder(self, node: TreeNode) -> None:
        self._nav.append(node)
        self._refresh_folder()

    def _go_to_depth(self, depth: int) -> None:
        # depth 0 == archive root; clicking the current segment does nothing.
        if 0 <= depth < len(self._nav) - 1:
            del self._nav[depth + 1 :]
            self._refresh_folder()

    # --------------------------------------------------------------- history
    def _reset_history(self) -> None:
        self._history: list[tuple[str, ...]] = []
        self._history_index = -1
        # Per folder (by path): the item that was current when it was left,
        # so coming back puts you where you were (see _place_after_move).
        self._folder_marks: dict[tuple[str, ...], str] = {}
        self._shown_path: tuple[str, ...] | None = None

    def _record_history(self) -> None:
        """Remember the folder now shown (by path, so it survives a reload)."""
        if getattr(self, "_history_moving", False) or not self._nav:
            return
        if not hasattr(self, "_history"):
            self._reset_history()
        path = tuple(node.name for node in self._nav[1:])
        index = self._history_index
        if 0 <= index < len(self._history) and self._history[index] == path:
            return
        del self._history[self._history_index + 1 :]
        self._history.append(path)
        self._history_index = len(self._history) - 1

    def go_back(self) -> None:
        if self._searching:
            self._exit_search()
            return
        self._step_history(-1)

    def go_forward(self) -> None:
        self._step_history(1)

    def _step_history(self, step: int) -> None:
        history = getattr(self, "_history", [])
        target = self._history_index + step
        if not self._nav or not 0 <= target < len(history):
            return
        nav = [self._nav[0]]
        for name in history[target]:
            child = nav[-1].children.get(name)
            if child is None or not child.is_dir:
                break  # gone since (an edit): as deep as it still exists
            nav.append(child)
        self._history_index = target
        self._nav = nav
        self._history_moving = True
        try:
            self._refresh_folder()
        finally:
            self._history_moving = False

    def _refresh_folder(self) -> None:
        current = self._nav[-1]
        self._remember_place()
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
        self._record_history()
        self._rebuild_breadcrumb()
        # Another folder: the model reset cleared the selection without a
        # selection signal, so reset what still described the old one.
        self._status_selection.setText(tr("status.no_selection"))
        self._set_status(tr("status.ready"))
        self._info_button.setEnabled(False)
        self._show_placeholder(tr("preview.select_file"))
        self._update_counts()
        self._update_nav_state()
        self._place_after_move()

    # ------------------------------------------------- where you were in a folder
    def _remember_place(self) -> None:
        """Before leaving the folder shown: note its current item."""
        shown = getattr(self, "_shown_path", None)
        if shown is None:
            return
        index = self._selection.currentIndex()
        node = self._node_at(index) if index.isValid() else None
        if node is not None:
            self._folder_marks[shown] = node.path

    def _place_after_move(self) -> None:
        """In the folder just opened, show the item you were at, in the
        middle of the view: going up (the path bar, Back, the Up key), the
        folder you came out of, selected; otherwise the item that was current
        when you last left this folder. So a long folder does not jump back
        to the top every time."""
        if not hasattr(self, "_folder_marks"):
            self._reset_history()
        previous = self._shown_path
        path = tuple(node.name for node in self._nav[1:])
        self._shown_path = path
        target = None
        if previous is not None and len(previous) > len(path) and previous[: len(path)] == path:
            child = self._nav[-1].children.get(previous[len(path)])
            target = child.path if child is not None else None
        if target is None:
            target = self._folder_marks.get(path)
        index = self._index_of_path(target) if target is not None else None
        if index is None:
            return
        node = self._node_at(index)
        if node is not None and node.is_dir:
            self._selection.setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
        else:
            # A file: only put the cursor there. Selecting it would open its
            # preview (a large video, say) just for passing through.
            self._selection.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        view = self._current_view()
        center = QAbstractItemView.ScrollHint.PositionAtCenter
        view.scrollTo(index, center)
        # The thumbnail views lay out their tiles after the model reset; scroll
        # again once that has happened.
        QTimer.singleShot(0, lambda: self._scroll_to_current(view, center))

    def _scroll_to_current(self, view: QAbstractItemView, hint) -> None:  # noqa: ANN001
        index = self._selection.currentIndex()
        if index.isValid() and view is self._current_view():
            view.scrollTo(index, hint)

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

        self._add_history_buttons()
        icon = QLabel()
        icon.setPixmap(theme.symbol_icon("folder_zip", 20, theme.ACCENT).pixmap(20, 20))
        self._breadcrumb_layout.addWidget(icon)
        # The archives this one was opened from: click one to go back to it.
        for depth, level in enumerate(self._outer_levels):
            outer = QToolButton()
            outer.setObjectName("crumb")
            outer.setAutoRaise(True)
            outer.setText(level.label)
            outer.setToolTip(tr("nested.back_to", name=level.label))
            outer.clicked.connect(lambda _c=False, d=depth: self._leave_nested(d))
            self._breadcrumb_layout.addWidget(outer)
            sep = QLabel("›")
            sep.setObjectName("crumbSep")
            self._breadcrumb_layout.addWidget(sep)

        segments = [
            self._archive_label or tr("crumb.archive_fallback"),
            *[n.name for n in self._nav[1:]],
        ]
        last = len(segments) - 1
        for depth, name in enumerate(segments):
            if depth > 0:
                # The separator lists the folders next to this one.
                self._breadcrumb_layout.addWidget(self._crumb_menu_button(depth - 1))
            crumb = QToolButton()
            crumb.setObjectName("crumb")
            crumb.setAutoRaise(True)
            crumb.setText(name)
            if depth == last:
                crumb.setEnabled(False)
            else:
                crumb.clicked.connect(lambda _c=False, d=depth: self._go_to_depth(d))
            self._breadcrumb_layout.addWidget(crumb)
            if depth == 0 and self._outer_levels:
                badge = QLabel(tr("nested.badge"))
                badge.setObjectName("experimentalBadge")
                badge.setToolTip(tr("nested.badge_tip"))
                badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                self._breadcrumb_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        if any(child.is_dir for child in self._nav[-1].children.values()):
            self._breadcrumb_layout.addWidget(self._crumb_menu_button(last))
        self._breadcrumb_layout.addStretch(1)
        self._add_breadcrumb_close()

    def _add_history_buttons(self) -> None:
        history = getattr(self, "_history", [])
        for icon, tip, step in (
            ("chevron_left", "nav.back", -1),
            ("chevron_right", "nav.forward", 1),
        ):
            button = QToolButton()
            button.setObjectName("crumbNav")
            button.setIcon(theme.symbol_icon(icon, 18, theme.TEXT_SECONDARY))
            button.setIconSize(QSize(18, 18))
            button.setToolTip(tr(tip))
            target = self._history_index + step
            button.setEnabled(0 <= target < len(history))
            button.clicked.connect(lambda _c=False, s=step: self._step_history(s))
            self._breadcrumb_layout.addWidget(button)

    def _crumb_menu_button(self, depth: int) -> QToolButton:
        """A "/" between crumbs that opens the folders of the folder at
        ``depth`` (with a field to type in), to jump sideways."""
        button = QToolButton()
        button.setObjectName("crumbSep")
        button.setText("/")
        button.setToolTip(tr("crumb.subfolders"))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _c=False, b=button, d=depth: self._open_folder_picker(b, d))
        return button

    def _folder_picker(self, depth: int):  # noqa: ANN202 - FolderPicker | None
        from archy.ui.folder_picker import FolderPicker

        if depth >= len(self._nav):
            return None
        parent = self._nav[depth]
        current = self._nav[depth + 1].name if depth + 1 < len(self._nav) else None
        folders = [child for child in parent.children.values() if child.is_dir]
        return FolderPicker(folders, current, lambda node, d=depth: self._jump_to(d, node), self)

    def _open_folder_picker(self, button: QToolButton, depth: int) -> None:
        picker = self._folder_picker(depth)
        if picker is not None:
            picker.popup(button.mapToGlobal(button.rect().bottomLeft()))

    def _jump_to(self, depth: int, node: TreeNode) -> None:
        del self._nav[depth + 1 :]
        self._nav.append(node)
        self._refresh_folder()

    def _add_breadcrumb_close(self) -> None:
        # Close the current archive (back to the startup state, frees memory).
        close_btn = QToolButton()
        close_btn.setObjectName("crumbClose")
        close_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        close_btn.setIcon(theme.symbol_icon("x", 15, theme.TEXT_SECONDARY))
        close_btn.setIconSize(QSize(15, 15))
        close_btn.setText(tr("crumb.close"))
        close_btn.setToolTip(tr("crumb.close_tip"))
        if self._outer_levels:
            # A longer path: the button keeps its icon (the tooltip says it).
            close_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
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

    def _index_of_path(self, path: str):  # noqa: ANN202 - QModelIndex | None
        """The view (proxy) index of ``path`` in the folder shown; None when
        it is not there or filtered out."""
        for r in range(self._model.rowCount()):
            node = self._model.node_for_row(r)
            if node is not None and node.path == path:
                proxy_index = self._proxy.mapFromSource(self._model.index(r, 0))
                return proxy_index if proxy_index.isValid() else None
        return None

    def _select_path(self, path: str) -> None:
        proxy_index = self._index_of_path(path)
        if proxy_index is not None:
            self._selection.setCurrentIndex(
                proxy_index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
