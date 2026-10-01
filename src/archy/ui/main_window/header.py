from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QSize,
    Qt,
)
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from archy.i18n import tr
from archy.ui import theme
from archy.ui.main_window.chrome import (
    DragBar as _DragBar,
)

# The app logo, shown as the header brand mark. Lives in resources/icons next to
# the package (bundled by the PyInstaller spec too).
_LOGO_PATH = Path(__file__).resolve().parent.parent.parent / "resources" / "icons" / "archy.png"


# Size of the square header controls (settings, minimise, maximise, close).
_CONTROL = 32


class HeaderMixin:
    """Builds the custom title bar / header row.

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    # ------------------------------------------------------------- header
    def _build_header(self) -> QWidget:
        header = _DragBar(self._toggle_max_restore)
        header.setObjectName("windowHeader")
        row = QHBoxLayout(header)
        # Right margin: the close button's centre sits as far from the right edge
        # as the logo's centre from the left edge (both 29 px), so the header
        # reads as one balanced bar.
        row.setContentsMargins(14, 0, 12, 0)
        row.setSpacing(10)

        brand_mark = QLabel()
        brand_mark.setObjectName("brandMark")
        brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_mark.setFixedSize(28, 28)
        if _LOGO_PATH.exists():
            pixmap = QPixmap(str(_LOGO_PATH))
            brand_mark.setPixmap(
                pixmap.scaled(
                    28,
                    28,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            brand_mark.setText("A")  # fallback if the logo resource is missing
        brand_name = QLabel("Archy")
        brand_name.setObjectName("brandName")
        row.addWidget(brand_mark)
        row.addWidget(brand_name)

        # "Openen" lives on the left, next to the brand: it starts the app's flow
        # (open an archive -> it shows as the chip -> browse/search).
        self._open_button = self._header_button(
            "folder_open", tr("header.open"), self.open_archive_dialog
        )
        row.addSpacing(8)
        row.addWidget(self._open_button)

        # Open archive as a rounded chip (folder icon + name), hidden until open.
        self._archive_chip = QFrame()
        self._archive_chip.setObjectName("archiveChip")
        self._archive_chip.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        chip = QHBoxLayout(self._archive_chip)
        chip.setContentsMargins(10, 5, 12, 5)
        chip.setSpacing(8)
        archive_icon = QLabel()
        archive_icon.setPixmap(theme.symbol_icon("folder_zip", 18, theme.ACCENT).pixmap(18, 18))
        self._archive_icon = archive_icon
        self._archive_name_label = QLabel("")
        self._archive_name_label.setObjectName("archiveName")
        chip.addWidget(archive_icon)
        chip.addWidget(self._archive_name_label)
        # The edit lock (see locking.py): shown for archives Archy only reads,
        # with a tooltip that says why and, where possible, how to unlock.
        self._lock_button = QToolButton()
        self._lock_button.setObjectName("archiveLock")
        self._lock_button.setIconSize(QSize(16, 16))
        self._lock_button.setAutoRaise(True)
        self._lock_button.clicked.connect(self._on_lock_clicked)
        self._lock_button.hide()
        chip.addWidget(self._lock_button)
        self._archive_chip.hide()
        row.addSpacing(6)
        row.addWidget(self._archive_chip)

        # Search takes the middle space.
        search_box = QFrame()
        search_box.setObjectName("headerSearch")
        search_box.setMinimumWidth(300)
        search_box.setMaximumWidth(340)
        search_box.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        sl = QHBoxLayout(search_box)
        sl.setContentsMargins(10, 0, 10, 0)
        sl.setSpacing(8)
        search_icon = QLabel()
        search_icon.setPixmap(theme.symbol_icon("search", 18, theme.TEXT_SECONDARY).pixmap(18, 18))
        self._search = QLineEdit()
        self._search.setObjectName("searchField")
        self._search.setPlaceholderText(tr("header.search"))
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_text)
        self._search.textChanged.connect(lambda _t: self._update_counts())
        sl.addWidget(search_icon)
        sl.addWidget(self._search, stretch=1)
        row.addSpacing(6)
        row.addWidget(search_box)
        row.addStretch(1)

        # Visible "unsaved changes" affordance: a teal Save button that only
        # appears when there is something to commit (the frameless window has no
        # native title bar to show a dirty marker in).
        self._save_button = QToolButton()
        self._save_button.setObjectName("headerSave")
        self._save_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._save_button.setIcon(theme.symbol_icon("download", 18, theme.ACCENT_ON))
        self._save_button.setIconSize(QSize(18, 18))
        self._save_button.setText(tr("header.save"))
        self._save_button.setToolTip(tr("header.save_tip"))
        self._save_button.clicked.connect(self.save_archive)
        self._save_button.hide()
        row.addWidget(self._save_button)

        # "Verversen" stays on the right (acts on the already-open archive).
        self._refresh_button = self._header_button(
            "refresh", tr("header.refresh"), self.refresh_current
        )
        self._refresh_button.setEnabled(False)
        row.addWidget(self._refresh_button)

        # Settings (gear), always available.
        self._settings_button = QToolButton()
        self._settings_button.setObjectName("winCtl")
        self._settings_button.setIcon(theme.symbol_icon("cog", 18, theme.TEXT_SECONDARY))
        self._settings_button.setIconSize(QSize(18, 18))
        self._settings_button.setFixedSize(_CONTROL, _CONTROL)
        self._settings_button.setToolTip(tr("header.settings"))
        self._settings_button.clicked.connect(self.open_settings_dialog)
        row.addWidget(self._settings_button)

        # Custom window controls (frameless window). Drawn as simple shapes so
        # they never depend on the icon font's glyph coverage.
        self._min_button = self._window_control("min", tr("window.minimize"), self.showMinimized)
        self._max_button = self._window_control(
            "max", tr("window.maximize"), self._toggle_max_restore
        )
        self._close_button = self._window_control(
            "close", tr("window.close"), self.close, close=True
        )
        controls = QHBoxLayout()
        controls.setContentsMargins(6, 0, 0, 0)
        controls.setSpacing(2)
        controls.addWidget(self._min_button)
        controls.addWidget(self._max_button)
        controls.addWidget(self._close_button)
        row.addLayout(controls)

        return header

    def _header_button(self, icon_name, label, slot) -> QToolButton:  # noqa: ANN001
        button = QToolButton()
        button.setObjectName("headerBtn")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIcon(theme.symbol_icon(icon_name, 18, theme.TEXT_SECONDARY))
        button.setIconSize(QSize(18, 18))
        button.setText(label)
        button.clicked.connect(slot)
        return button

    _WIN_ICON = {"min": "minus", "max": "square", "restore": "restore", "close": "x"}

    def _window_control(self, kind, tip, slot, *, close=False) -> QToolButton:  # noqa: ANN001
        button = QToolButton()
        button.setObjectName("winClose" if close else "winCtl")
        button.setAutoRaise(True)
        button.setIcon(theme.symbol_icon(self._WIN_ICON[kind], 16, theme.TEXT_SECONDARY))
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(_CONTROL, _CONTROL)
        button.setToolTip(tip)
        button.clicked.connect(slot)
        return button

    def _toggle_max_restore(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self._max_button.setIcon(theme.symbol_icon("square", 16, theme.TEXT_SECONDARY))
            self._max_button.setToolTip(tr("window.maximize"))
        else:
            self.showMaximized()
            self._max_button.setIcon(theme.symbol_icon("restore", 16, theme.TEXT_SECONDARY))
            self._max_button.setToolTip(tr("window.restore"))
