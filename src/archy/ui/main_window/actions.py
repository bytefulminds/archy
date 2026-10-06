from __future__ import annotations

import sys

from PySide6.QtCore import (
    Qt,
)
from PySide6.QtGui import (
    QAction,
    QKeySequence,
)

from archy.i18n import tr
from archy.ui import theme


class ActionsMixin:
    """Creates the QActions / shortcuts.

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    # ------------------------------------------------------------- actions
    def _build_actions(self) -> None:
        open_action = QAction(theme.symbol_icon("folder_open"), tr("action.open"), self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_archive_dialog)

        self._save_action = QAction(tr("action.save"), self)
        self._save_action.setShortcut(QKeySequence.StandardKey.Save)
        self._save_action.triggered.connect(self.save_archive)
        self._save_action.setEnabled(False)

        self._extract_action = QAction(theme.symbol_icon("unarchive"), tr("action.extract"), self)
        self._extract_action.setShortcut(QKeySequence("Ctrl+E"))
        self._extract_action.triggered.connect(self.extract_selected)
        self._extract_action.setEnabled(False)

        self._extract_all_action = QAction(
            theme.symbol_icon("folder_zip"), tr("action.extract_all"), self
        )
        self._extract_all_action.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self._extract_all_action.triggered.connect(self.extract_all)
        self._extract_all_action.setEnabled(False)

        # File management (writable archives only).
        self._add_action = QAction(theme.symbol_icon("file_plus"), tr("action.add_files"), self)
        self._add_action.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self._add_action.triggered.connect(self.add_files_dialog)
        self._add_action.setEnabled(False)

        self._new_folder_action = QAction(
            theme.symbol_icon("folder_plus"), tr("action.new_folder"), self
        )
        self._new_folder_action.setShortcut(QKeySequence("Ctrl+Shift+N"))
        self._new_folder_action.triggered.connect(self.create_folder)
        self._new_folder_action.setEnabled(False)

        self._rename_action = QAction(tr("action.rename"), self)
        self._rename_action.setShortcut(QKeySequence(Qt.Key.Key_F2))
        self._rename_action.triggered.connect(self.rename_selected)
        self._rename_action.setEnabled(False)

        self._delete_action = QAction(tr("action.delete"), self)
        self._delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        self._delete_action.triggered.connect(self.delete_selected)
        self._delete_action.setEnabled(False)

        self._copy_path_action = QAction(tr("action.copy_path"), self)
        self._copy_path_action.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self._copy_path_action.triggered.connect(self.copy_selected_path)
        self._copy_path_action.setEnabled(False)

        self._settings_action = QAction(tr("action.settings"), self)
        self._settings_action.setShortcut(QKeySequence("Ctrl+,"))
        self._settings_action.triggered.connect(self.open_settings_dialog)
        self.addAction(self._settings_action)

        self._find_action = QAction(tr("action.find_folder"), self)
        self._find_action.setShortcut(QKeySequence.StandardKey.Find)
        self._find_action.triggered.connect(self._find_in_preview_or_folder)
        self.addAction(self._find_action)

        self._find_archive_action = QAction(tr("action.find_archive"), self)
        self._find_archive_action.setShortcut(QKeySequence("Ctrl+Shift+F"))
        self._find_archive_action.triggered.connect(lambda: self._focus_search("archive"))
        self.addAction(self._find_archive_action)

        self._fullscreen_action = QAction(
            theme.symbol_icon("fullscreen"), tr("action.fullscreen"), self
        )
        self._fullscreen_action.setShortcut(QKeySequence(Qt.Key.Key_F11))
        self._fullscreen_action.triggered.connect(self.toggle_fullscreen)
        self._fullscreen_action.setEnabled(False)
        self.addAction(self._fullscreen_action)

        slideshow_action = QAction(tr("slideshow.start"), self)
        slideshow_action.setShortcut(QKeySequence(Qt.Key.Key_F5))
        slideshow_action.triggered.connect(self.start_slideshow)
        self.addAction(slideshow_action)

        close_action = QAction(tr("action.close"), self)
        close_action.setShortcut(QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.close_archive)

        back_action = QAction(tr("nav.back"), self)
        back_action.setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.Back))
        back_action.triggered.connect(self.go_back)
        self.addAction(back_action)
        forward_action = QAction(tr("nav.forward"), self)
        forward_action.setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.Forward))
        forward_action.triggered.connect(self.go_forward)
        self.addAction(forward_action)

        self._up_action = QAction(tr("action.up"), self)
        self._up_action.setShortcut(QKeySequence(Qt.Key.Key_Backspace))
        self._up_action.triggered.connect(self.go_up)
        self.addAction(self._up_action)

        quit_action = QAction(tr("action.quit"), self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)

        menu = self.menuBar().addMenu(tr("menu.file"))
        menu.addAction(open_action)
        menu.addAction(self._save_action)
        menu.addSeparator()
        menu.addAction(self._add_action)
        menu.addAction(self._new_folder_action)
        menu.addAction(self._rename_action)
        menu.addAction(self._delete_action)
        menu.addSeparator()
        menu.addAction(self._extract_action)
        menu.addAction(self._extract_all_action)
        menu.addSeparator()
        menu.addAction(self._settings_action)
        menu.addSeparator()
        menu.addAction(self._fullscreen_action)
        menu.addSeparator()
        menu.addAction(close_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        # Keep the shortcuts working regardless of the menu bar's visibility, and
        # hide the menu bar off macOS (on macOS it lives in the global bar, so it
        # costs no window space; elsewhere it would sit above the custom titlebar).
        for act in (
            open_action,
            self._save_action,
            self._add_action,
            self._new_folder_action,
            self._rename_action,
            self._delete_action,
            self._copy_path_action,
            self._extract_action,
            self._extract_all_action,
            close_action,
            quit_action,
        ):
            self.addAction(act)
        if sys.platform != "darwin":
            self.menuBar().setVisible(False)
