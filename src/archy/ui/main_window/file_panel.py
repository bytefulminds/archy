from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import (
    QSize,
    Qt,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListView,
    QSizePolicy,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from archy.i18n import tr
from archy.services.formatting import format_modified
from archy.services.preview import PreviewKind, kind_for_name
from archy.ui import theme
from archy.ui.grid_delegate import GridDelegate
from archy.ui.main_window.constants import FOOTER_HEIGHT
from archy.ui.settings import view_labels


class FilePanelMixin:
    """Builds the file list panel (table / compact / grid) and status strip.

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    # ------------------------------------------------------------- file panel
    def _build_file_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("filePanel")
        panel.setMinimumWidth(320)
        col = QVBoxLayout(panel)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self._breadcrumb = QFrame()
        self._breadcrumb.setObjectName("breadcrumb")
        self._breadcrumb_layout = QHBoxLayout(self._breadcrumb)
        self._breadcrumb_layout.setContentsMargins(12, 0, 12, 0)
        self._breadcrumb_layout.setSpacing(6)
        col.addWidget(self._breadcrumb)

        col.addWidget(self._build_archive_notice())
        col.addWidget(self._build_list_toolbar())

        self._views = QStackedWidget()
        self._table = self._build_table()
        self._compact = self._build_compact_view()
        self._grid = self._build_grid_view(large=False)
        self._large = self._build_grid_view(large=True)
        for view in (self._table, self._compact, self._grid, self._large):
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(
                lambda pos, v=view: self._show_context_menu(v, pos)
            )
            self._views.addWidget(view)
        self._views.setCurrentIndex(3)
        col.addWidget(self._views, stretch=1)
        col.addWidget(self._build_status_strip())
        return panel

    def _build_archive_notice(self) -> QWidget:
        """A noticeable (not alarming) strip about the open archive, such as a
        7z that is partly compressed. Informational: nothing to confirm."""
        notice = QFrame()
        notice.setObjectName("archiveNotice")
        row = QHBoxLayout(notice)
        row.setContentsMargins(12, 9, 8, 9)
        row.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(theme.symbol_icon("warning", 20, theme.WARNING).pixmap(20, 20))
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(2)
        self._notice_title = QLabel()
        self._notice_title.setObjectName("noticeTitle")
        self._notice_text = QLabel()
        self._notice_text.setObjectName("noticeText")
        self._notice_text.setWordWrap(True)
        texts.addWidget(self._notice_title)
        texts.addWidget(self._notice_text)
        close = QToolButton()
        close.setObjectName("noticeClose")
        close.setIcon(theme.symbol_icon("close", 16, theme.TEXT_SECONDARY))
        close.setIconSize(QSize(16, 16))
        close.setToolTip(tr("notice.hide"))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(notice.hide)
        row.addWidget(icon, alignment=Qt.AlignmentFlag.AlignTop)
        row.addLayout(texts, stretch=1)
        row.addWidget(close, alignment=Qt.AlignmentFlag.AlignTop)
        notice.hide()
        self._archive_notice = notice
        return notice

    def _update_archive_notice(self) -> None:
        """Show the mixed-archive notice when the open archive needs it."""
        mixed = self._backend.mixed_storage() if self._backend is not None else None
        if mixed is None:
            self._archive_notice.hide()
            return
        stored, compressed = mixed
        self._notice_title.setText(tr("notice.mixed_title"))
        self._notice_text.setText(
            tr("notice.mixed_text", count=compressed, total=stored + compressed)
        )
        self._archive_notice.show()

    def _build_status_strip(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("panelStatus")
        bar.setFixedHeight(FOOTER_HEIGHT)
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 0, 12, 0)
        row.setSpacing(8)
        self._status_selection = QLabel(tr("status.no_selection"))
        self._status_items = QLabel(tr("count.items", count=0))
        self._status_size = QLabel("0 B")
        sep1 = QLabel("|")
        sep2 = QLabel("|")
        sep1.setObjectName("statusDivider")
        sep2.setObjectName("statusDivider")
        row.addWidget(self._status_selection)
        row.addWidget(sep1)
        row.addWidget(self._status_items)
        row.addWidget(sep2)
        row.addWidget(self._status_size)
        row.addStretch(1)
        self._status_msg = QLabel("")
        self._status_msg.setObjectName("statusMessage")
        row.addWidget(self._status_msg)
        self._status_row = row  # progress widgets are appended here
        return bar

    def _set_status(self, text: str) -> None:
        self._status_msg.setText(text)

    def _build_list_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("listToolbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 0, 10, 0)
        row.setSpacing(8)

        self._folder_hint = QLabel("")
        self._folder_hint.setObjectName("folderHint")
        self._folder_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self._folder_hint)
        row.addStretch(1)

        switch = QFrame()
        switch.setObjectName("viewSwitch")
        switch.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        sl = QHBoxLayout(switch)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        specs = (
            ("view_list", 0),
            ("view_headline", 1),
            ("apps", 2),
            ("grid_view", 3),
        )
        labels = view_labels()
        for icon_name, index in specs:
            label = labels[index]
            button = QToolButton()
            button.setObjectName("viewButton")
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setIcon(theme.symbol_icon(icon_name, 18, theme.TEXT_SECONDARY))
            button.setIconSize(QSize(18, 18))
            button.setText(label)
            button.clicked.connect(lambda _c=False, i=index: self._switch_view(i))
            self._view_group.addButton(button, index)
            sl.addWidget(button)
        self._view_group.button(3).setChecked(True)
        row.addWidget(switch)
        return bar

    def _build_table(self) -> QTableView:
        table = QTableView()
        table.setModel(self._proxy)
        table.setSelectionModel(self._selection)
        table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(False)
        table.doubleClicked.connect(self._on_double_clicked)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        # Fixed, content-sized widths for the detail columns instead of
        # ResizeToContents: that re-measured up to 1000 rows through the
        # (Python) model on every layout pass - folder changes, status-bar
        # updates - even while the list view was hidden, freezing the UI for
        # over a second on big folders. The values are uniform (sizes, ratios,
        # dates), so a width from a representative sample fits them all, and
        # the user can still drag a column wider.
        metrics = table.fontMetrics()
        samples = {
            1: "999,9 MB",
            2: "999,9 MB",
            3: "100%",
            4: "JPEG",
            5: format_modified(datetime(2026, 12, 31, 23, 59)),
        }
        for col, sample in samples.items():
            title = str(self._model.headerData(col, Qt.Orientation.Horizontal) or "")
            width = max(metrics.horizontalAdvance(sample), metrics.horizontalAdvance(title))
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(col, width + 28)
        return table

    def _build_compact_view(self) -> QListView:
        view = QListView()
        view.setModel(self._proxy)
        view.setModelColumn(0)
        view.setSelectionModel(self._selection)
        view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        view.setSelectionRectVisible(True)  # rubber-band selection
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        view.setViewMode(QListView.ViewMode.ListMode)
        view.setFlow(QListView.Flow.TopToBottom)
        view.setWrapping(False)
        view.setUniformItemSizes(True)
        view.setSpacing(1)
        view.setIconSize(QSize(18, 18))
        view.doubleClicked.connect(self._on_double_clicked)
        return view

    def _build_grid_view(self, *, large: bool) -> QListView:
        view = QListView()
        view.setModel(self._proxy)
        view.setModelColumn(0)
        view.setSelectionModel(self._selection)
        view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        view.setSelectionRectVisible(True)  # rubber-band selection
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        view.setViewMode(QListView.ViewMode.IconMode)
        view.setFlow(QListView.Flow.LeftToRight)
        view.setWrapping(True)
        view.setResizeMode(QListView.ResizeMode.Adjust)
        view.setMovement(QListView.Movement.Static)
        view.setUniformItemSizes(True)
        view.setSpacing(6)
        view.setItemDelegate(GridDelegate(self._thumbs, view, large=large))
        view.doubleClicked.connect(self._on_double_clicked)
        return view

    def _switch_view(self, index: int) -> None:
        self._views.setCurrentIndex(index)
        view_label = view_labels()[index]
        text = self._folder_hint.text()
        if "·" in text:
            self._folder_hint.setText(f"{view_label} · {text.split('·', 1)[1].strip()}")
        current = self._selection.currentIndex()
        if current.isValid():
            self._current_view().scrollTo(current)
        # Entering a thumbnail view needs the whole folder: run the folder pass
        # (unpack + all thumbnails) if it has not run for this folder yet.
        self._thumbs.set_active(self._thumb_view_active())
        if self._thumb_view_active():
            self._warm_folder()

    def _thumb_view_active(self) -> bool:
        return self._views.currentIndex() in (2, 3)

    def _warm_folder(self) -> None:
        """Start the one-pass folder prefetch (once per folder). Videos are
        skipped: they are large, previewed on demand, and bulk-decoding one into
        memory would only slow the prefetch down."""
        files = self._current_folder_files()
        warm = [e for e in files if kind_for_name(e.name) is not PreviewKind.VIDEO]
        self._prefetcher.ensure(warm)

    def _display_folder_files(self) -> list:
        """The current folder's file entries in display (sorted) order, so
        background thumbnails fill in top to bottom."""
        files = []
        for row in range(self._proxy.rowCount()):
            source = self._proxy.mapToSource(self._proxy.index(row, 0))
            node = self._model.node_for_row(source.row())
            if node is not None and not node.is_dir and node.entry is not None:
                files.append(node.entry)
        return files

    def _current_folder_files(self) -> list:
        """Direct (non-recursive) file entries of the current folder, for the
        folder prefetch."""
        files = []
        for r in range(self._model.rowCount()):
            node = self._model.node_for_row(r)
            if node is not None and not node.is_dir and node.entry is not None:
                files.append(node.entry)
        return files

    def _current_view(self) -> QAbstractItemView:
        return self._views.currentWidget()
