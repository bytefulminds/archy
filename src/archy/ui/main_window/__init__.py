from __future__ import annotations

from PySide6.QtCore import (
    QItemSelectionModel,
    Qt,
    QTimer,
)
from PySide6.QtWidgets import (
    QFrame,
    QMainWindow,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from archy.core import memory
from archy.core.backend import ArchiveBackend
from archy.core.tree import TreeNode
from archy.i18n import tr
from archy.services.preview import PreviewKind
from archy.services.temp_files import TempManager
from archy.ui.archive_model import ArchiveTableModel, FolderSortProxy
from archy.ui.busy_overlay import BusyOverlay
from archy.ui.extract_worker import ExtractWorker
from archy.ui.folder_prefetcher import FolderPrefetcher
from archy.ui.main_window.actions import ActionsMixin
from archy.ui.main_window.apply_settings import SettingsMixin
from archy.ui.main_window.chrome import (
    FullscreenWindow as _FullscreenWindow,
)
from archy.ui.main_window.chrome import (
    ResizeGrip as _ResizeGrip,
)
from archy.ui.main_window.editing import EditingMixin
from archy.ui.main_window.extract import ExtractMixin
from archy.ui.main_window.file_panel import FilePanelMixin
from archy.ui.main_window.header import HeaderMixin
from archy.ui.main_window.locking import LockMixin
from archy.ui.main_window.navigation import NavigationMixin
from archy.ui.main_window.opening import OpeningMixin
from archy.ui.main_window.preview import PreviewMixin
from archy.ui.main_window.search import SearchMixin
from archy.ui.main_window.selection import SelectionMixin
from archy.ui.main_window.sidebar import SidebarMixin
from archy.ui.main_window.window_frame import WindowFrameMixin
from archy.ui.open_worker import OpenWorker
from archy.ui.preview_loader import PreviewLoader
from archy.ui.save_worker import SaveWorker
from archy.ui.settings import Settings
from archy.ui.thumbnail_loader import ThumbnailLoader


class MainWindow(
    HeaderMixin,
    SidebarMixin,
    FilePanelMixin,
    PreviewMixin,
    ActionsMixin,
    OpeningMixin,
    NavigationMixin,
    SelectionMixin,
    SearchMixin,
    LockMixin,
    EditingMixin,
    ExtractMixin,
    SettingsMixin,
    WindowFrameMixin,
    QMainWindow,
):
    """A three-pane archive browser: a category sidebar, the current folder's
    entries (as a details table, compact list, or thumbnail grid), and an
    in-memory preview of the selected file.

    The window's methods live in topic mixins (one file per concern: header,
    sidebar, file panel, preview, actions, opening, navigation, selection,
    search, lock and save, editing, extract, settings, window frame). This class only wires them
    together in ``__init__``; each mixin holds the methods for its concern.

    Navigation is folder-based (like 7-Zip): double-click a folder to enter, the
    breadcrumb or Backspace to go back. Single-clicking a file previews it. Text
    files in a writable archive can be edited in place and saved back.
    """

    _SHADOW_MARGIN = 0  # no transparent border; the OS shadows the rounded window

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Archy")
        self.resize(1180, 720)
        # Frameless, custom-chrome window (Soft Depth). Translucent so the
        # rounded windowRoot corners composite over the desktop.
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAcceptDrops(True)  # drag files in from the OS to add them
        self._grips: list[_ResizeGrip] = []

        self._settings = Settings()
        # Resource limits (Settings > Advanced) before anything sizes itself.
        memory.configure(self._settings.limits())
        self._temp = TempManager(self._settings)
        self._backend: ArchiveBackend | None = None
        self._nav: list[TreeNode] = []
        # Search scope: "archive" (default) searches the whole archive and shows
        # a flat results list; "folder" filters just the current folder (Ctrl+F).
        self._search_scope = "archive"
        self._searching = False
        # Debounce archive-wide search so a burst of keystrokes walks the tree
        # once, not per character.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._apply_archive_search)
        self._archive_label = ""
        self._archive_items = 0
        self._archive_bytes = 0

        # Current editable text field (None when the preview is not editable).
        self._editor: QPlainTextEdit | None = None
        self._editing_node: TreeNode | None = None
        self._edit_encoding = "utf-8"

        # Off-thread preview loading. Each selection bumps _req_id; results with
        # a stale id are ignored.
        self._loader = PreviewLoader()
        self._loader.loaded.connect(self._on_preview_loaded)
        self._loader.failed.connect(self._on_preview_failed)
        self._loader.spilled.connect(self._on_preview_spilled)
        self._loader.spill_progress.connect(self._on_spill_progress)
        self._req_id = 0
        self._req_node: TreeNode | None = None
        self._req_kind: PreviewKind | None = None
        # True once we know the archive is encrypted: editing/saving is then
        # disabled until the user unlocks it on purpose (see locking.py).
        self._archive_encrypted = False
        self._edit_unlocked = False
        self._edit_profile = None  # how the open 7z was made (sevenzip_writer)

        # Opening runs on a worker thread while a spinner shows (open_worker).
        self._open_worker = OpenWorker(self)
        self._open_worker.finished.connect(self._on_open_finished)
        self._open_token = 0
        self._opening = None  # (token, backend, path, password asked)

        # Saving a 7z rewrites it on a worker thread (save_worker).
        self._save_worker = SaveWorker(self)
        self._save_worker.progress.connect(self._on_save_progress)
        self._save_worker.finished.connect(self._on_save_finished)
        self._saving = False
        self._save_dialog = None
        self._save_dialog_timer = QTimer(self)
        self._save_dialog_timer.setSingleShot(True)
        self._save_dialog_timer.timeout.connect(self._show_save_dialog)

        # Lazy thumbnails for the grid/large views.
        self._thumbs = ThumbnailLoader()
        self._thumbs.ready.connect(self._on_thumbnail_ready)
        self._thumbs.progress.connect(self._on_thumb_progress)

        # The folder pass: one-pass decode of the current folder into memory
        # (on opening it in a thumbnail view, or on the first file opened),
        # making every thumbnail on the way, so the folder then browses
        # instantly. Repaints the grids when it finishes.
        self._prefetcher = FolderPrefetcher()
        self._prefetcher.set_thumbnails(self._thumbs)
        self._prefetcher.done.connect(self._on_thumbnail_ready)
        self._prefetcher.started.connect(self._on_prefetch_started)
        self._prefetcher.progress.connect(self._on_prefetch_progress)
        self._prefetcher.done.connect(self._on_prefetch_done)

        # Background extraction.
        self._extract_worker = ExtractWorker()
        self._extract_worker.progress.connect(self._on_extract_progress)
        self._extract_worker.finished.connect(self._on_extract_finished)
        self._extract_dest = ""
        self._extracting = False

        # Fullscreen preview state.
        self._fs_window: _FullscreenWindow | None = None
        self._fullscreenable = False

        self._model = ArchiveTableModel()
        self._proxy = FolderSortProxy()
        self._proxy.setSourceModel(self._model)
        self._proxy.sort(0, Qt.SortOrder.AscendingOrder)
        # One selection model shared by all views, so switching view keeps the
        # selection and the preview wiring in one place.
        self._selection = QItemSelectionModel(self._proxy)
        self._selection.selectionChanged.connect(self._on_selection_changed)

        # Actions first: building the preview panel shows a placeholder, which
        # touches the save action.
        self._build_actions()

        root = QFrame()
        root.setObjectName("windowRoot")
        root_col = QVBoxLayout(root)
        root_col.setContentsMargins(0, 0, 0, 0)
        root_col.setSpacing(0)
        root_col.addWidget(self._build_header())

        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setObjectName("workspace")
        self._sidebar = self._build_sidebar()
        self._file_panel = self._build_file_panel()
        self._preview_panel = self._build_preview_panel()
        self._splitter.addWidget(self._sidebar)
        self._splitter.addWidget(self._file_panel)
        self._splitter.addWidget(self._preview_panel)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 3)
        self._splitter.setStretchFactor(2, 4)
        self._splitter.setSizes([220, 560, 500])
        root_col.addWidget(self._splitter, stretch=1)
        self._busy = BusyOverlay(self._splitter)

        # No transparent breathing room around the window: the rounded windowRoot
        # fills the whole window (only the four rounded corners are transparent),
        # so there is no visible transparent border along the edges. The floating
        # drop shadow comes from the OS (macOS shadows a translucent frameless
        # window around its opaque, rounded content) rather than a painted margin.
        central = QWidget()
        self._outer = QVBoxLayout(central)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        self._outer.addWidget(root)
        self._root = root
        self.setCentralWidget(central)
        self.statusBar().hide()  # a custom bottom strip replaces the native one
        self._build_resize_grips(central)
        self._build_progress_widgets()
        self._set_status(tr("status.ready"))
        self._update_nav_state()
        self._update_open_state()
        self._apply_density()
        if self._settings.remember_window:
            geom = self._settings.window_geometry
            if geom is not None:
                self.restoreGeometry(geom)
