from __future__ import annotations

from archy.ui.theme import palette

# The QSS for all the "chrome" (frameless window shell, header/titlebar, glass
# panels, buttons, segmented controls, inputs, menus, scrollbars). The "glass"
# look is faked with semi-opaque fills over a gradient window background plus
# 1px lighter edges (no backdrop blur), so it reproduces reliably in Qt.
#
# _TEMPLATE is a str.format template: "{{"/"}}" are literal CSS braces and
# "{NAME}" placeholders are filled from the palette tokens at build time, so a
# runtime accent change (set_accent) is picked up on the next stylesheet() call.
# The four "{_FILL_*}" keys are accent-tinted rgba fills computed in stylesheet()
# (str.format cannot call functions inside the template).

_TEMPLATE = """
    QWidget {{ color: {TEXT_PRIMARY}; }}
    QMainWindow {{ background: transparent; }}

    /* ---- popup dialogs in Soft Depth ---- */
    /* Custom frameless SoftDialog: same rounded shell/titlebar as the window. */
    QDialog {{ background: transparent; }}
    QFrame#dialogRoot {{
        background: qlineargradient(x1:0, y1:0, x2:0.25, y2:1,
            stop:0 {_WIN_TOP}, stop:0.55 {_WIN_MID}, stop:1 {_WIN_BOTTOM});
        border: 1px solid {_EDGE};
        border-radius: {_WINDOW_RADIUS}px;
    }}
    QFrame#dialogHeader {{
        background: {_GLASS};
        border-bottom: 1px solid {_EDGE_SOFT};
        border-top-left-radius: {_WINDOW_RADIUS}px;
        border-top-right-radius: {_WINDOW_RADIUS}px;
        min-height: 42px;
        max-height: 42px;
    }}
    QLabel#dialogTitle {{
        font-family: "{DISPLAY_FAMILY}"; font-size: 13px; font-weight: 600;
    }}
    QLabel#dialogText {{ color: {TEXT_PRIMARY}; background: transparent; font-size: 13px; }}
    QLineEdit#dialogField {{
        background: rgba(255, 255, 255, 0.035);
        border: 1px solid rgba(255, 255, 255, 0.07);
        border-radius: 17px;
        min-height: 34px;
        padding: 0 14px;
        selection-background-color: {ACCENT};
        selection-color: {ACCENT_ON};
    }}
    QLineEdit#dialogField:focus {{ border: 1px solid {ACCENT}; }}
    QCheckBox#dialogCheck {{ color: {TEXT_SECONDARY}; font-size: 12px; spacing: 8px; }}
    QCheckBox#dialogCheck::indicator {{
        width: 16px; height: 16px; border-radius: 5px;
        border: 1px solid {_EDGE_STRONG}; background: {_GLASS};
    }}
    QCheckBox#dialogCheck::indicator:checked {{
        background: {ACCENT}; border-color: {ACCENT};
    }}
    /* Any residual native message/input box still reads in-theme. */
    QMessageBox, QInputDialog {{
        background: qlineargradient(x1:0, y1:0, x2:0.25, y2:1,
            stop:0 {_WIN_TOP}, stop:0.55 {_WIN_MID}, stop:1 {_WIN_BOTTOM});
        color: {TEXT_PRIMARY};
    }}
    QMessageBox QLabel, QInputDialog QLabel {{
        color: {TEXT_PRIMARY}; background: transparent; font-size: 13px;
    }}

    /* ---- frameless window shell ---- */
    QFrame#windowRoot {{
        background: qlineargradient(x1:0, y1:0, x2:0.25, y2:1,
            stop:0 {_WIN_TOP}, stop:0.55 {_WIN_MID}, stop:1 {_WIN_BOTTOM});
        border: 1px solid {_EDGE};
        border-radius: {_WINDOW_RADIUS}px;
    }}

    QFrame#windowHeader {{
        background: {_GLASS};
        border-bottom: 1px solid {_EDGE};
        border-top-left-radius: {_WINDOW_RADIUS}px;
        border-top-right-radius: {_WINDOW_RADIUS}px;
        min-height: 54px;
        max-height: 54px;
    }}
    QLabel#brandMark {{
        /* Shows the Archy logo pixmap (its own rounded square); keep the label
           transparent. The color/font below only style the text fallback used
           when the logo resource is missing. */
        background: transparent;
        color: {ACCENT};
        font-family: "{DISPLAY_FAMILY}";
        font-weight: 700;
        font-size: 16px;
    }}
    QLabel#brandName {{
        font-family: "{DISPLAY_FAMILY}"; font-weight: 600; font-size: 15px;
    }}
    QLabel#archiveName {{ color: {TEXT_PRIMARY}; font-family: "{MONO_FAMILY}"; font-size: 12px; }}
    QFrame#headerSearch {{
        background: rgba(255, 255, 255, 0.035);
        border: 1px solid rgba(255, 255, 255, 0.07);
        border-radius: 17px;
        min-height: 34px;
        max-height: 34px;
    }}
    QFrame#headerSearch:focus-within {{ border: 1px solid {ACCENT}; }}
    QLineEdit#searchField {{
        border: none; background: transparent; padding: 0; font-size: 12px;
    }}
    QLineEdit#searchField:focus {{ border: none; }}
    QFrame#archiveChip {{
        background: {_GLASS};
        border: 1px solid {_EDGE};
        border-radius: 15px;
        max-height: 30px;
    }}
    QToolButton#headerBtn {{
        background: {_GLASS};
        color: {TEXT_SECONDARY};
        border: 1px solid {_EDGE};
        border-radius: 15px;
        padding: 0 12px;
        min-height: 30px;
        max-height: 30px;
        font-size: 12px;
        font-weight: 500;
    }}
    QToolButton#headerBtn:hover {{ background: {_GLASS_STRONG}; color: {TEXT_PRIMARY}; }}
    QToolButton#headerBtn:disabled {{ color: {TEXT_DISABLED}; border-color: {DIVIDER}; }}
    QToolButton#headerSave {{
        background: qlineargradient(x1:0, y1:0, x2:0.6, y2:1,
            stop:0 {ACCENT_HOVER}, stop:0.7 {ACCENT});
        color: {ACCENT_ON};
        border: 1px solid {_EDGE_STRONG};
        border-radius: 15px;
        padding: 0 12px;
        min-height: 30px;
        max-height: 30px;
        font-size: 12px;
        font-weight: 600;
    }}
    QToolButton#headerSave:hover {{ background: {ACCENT_HOVER}; }}
    QToolButton#headerSave:pressed {{ background: {ACCENT_PRESSED}; }}

    /* window controls (min / max / close) */
    QToolButton#winCtl, QToolButton#winClose {{
        background: transparent; border: none; border-radius: 8px;
        padding: 0; margin: 0;
    }}
    QToolButton#winCtl:hover {{ background: {_GLASS_STRONG}; }}
    QToolButton#winClose:hover {{ background: {ERROR}; }}

    /* ---- sidebar ---- */
    QFrame#archiveSidebar {{
        background: {_GLASS_SOFT};
        border-right: 1px solid {_EDGE_SOFT};
    }}
    QLabel#sidebarTitle {{
        color: {TEXT_DISABLED}; font-size: 11px; font-weight: 600;
        padding: 16px 16px 10px; letter-spacing: 1px;
    }}
    QToolButton#folderItem {{
        background: transparent; color: {TEXT_SECONDARY};
        border: none; border-radius: 9px; padding: 0; text-align: left;
        font-size: 13px;
    }}
    QToolButton#folderItem:hover {{ background: {_GLASS}; color: {TEXT_PRIMARY}; }}
    QToolButton#folderItem:checked {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {_FILL_SECONDARY_16}, stop:1 {_FILL_SECONDARY_06});
        color: {TEXT_PRIMARY};
        border-left: 2px solid {SECONDARY};
    }}
    QLabel#folderCount {{ color: {TEXT_DISABLED}; font-family: "{MONO_FAMILY}"; font-size: 11px; }}
    QFrame#sidebarBottom {{
        border-top: 1px solid {_EDGE_SOFT};
        border-bottom-left-radius: {_WINDOW_RADIUS}px;
    }}
    QLabel#summaryKey {{ color: {TEXT_SECONDARY}; font-size: 11px; }}
    QLabel#summaryValue {{ color: {TEXT_PRIMARY}; font-size: 11px; }}

    /* ---- file panel ---- */
    QFrame#filePanel {{
        background: {_GLASS};
        border-right: 1px solid {_EDGE_SOFT};
    }}
    QFrame#archiveNotice {{
        background: rgba(224, 165, 75, 0.12);
        border: none;
        border-bottom: 1px solid rgba(224, 165, 75, 0.35);
        border-left: 3px solid {WARNING};
    }}
    QLabel#noticeTitle {{ color: {WARNING}; font-weight: 600; font-size: 12px; }}
    QLabel#noticeText {{ color: {TEXT_SECONDARY}; font-size: 12px; }}
    QToolButton#noticeClose {{
        background: transparent; border: none; border-radius: 6px; padding: 3px;
    }}
    QToolButton#noticeClose:hover {{ background: {_GLASS_STRONG}; }}
    QFrame#breadcrumb {{
        background: {_GLASS_SOFT};
        border-bottom: 1px solid {DIVIDER};
        min-height: 44px;
        max-height: 44px;
    }}
    QToolButton#crumb {{
        background: transparent; color: {TEXT_SECONDARY};
        border: none; border-radius: 6px; padding: 3px 6px;
    }}
    QToolButton#crumb:hover {{ background: {_GLASS_STRONG}; color: {TEXT_PRIMARY}; }}
    QToolButton#crumb:disabled {{ color: {TEXT_PRIMARY}; }}
    QLabel#crumbSep {{ color: {TEXT_DISABLED}; }}
    QToolButton#crumbSep {{
        background: transparent; color: {TEXT_DISABLED};
        border: none; border-radius: 6px; padding: 3px 4px;
    }}
    QToolButton#crumbSep:hover {{ background: {_GLASS_STRONG}; color: {TEXT_PRIMARY}; }}
    QToolButton#crumbSep::menu-indicator {{ image: none; width: 0; }}
    QLabel#experimentalBadge {{
        background: {_FILL_SECONDARY_16}; color: {SECONDARY};
        border: 1px solid {SECONDARY}; border-radius: 8px;
        padding: 1px 7px; font-size: 10px; font-weight: 600;
    }}
    QFrame#folderPicker {{
        background: {OVERLAY}; border: 1px solid {BORDER}; border-radius: 10px;
    }}
    QLineEdit#folderPickerField {{
        background: rgba(255, 255, 255, 0.05); border: 1px solid {_EDGE_SOFT};
        border-radius: 8px; padding: 5px 8px; color: {TEXT_PRIMARY};
    }}
    QListWidget#folderPickerList {{
        background: transparent; border: none; outline: none; color: {TEXT_PRIMARY};
    }}
    QListWidget#folderPickerList::item {{ padding: 4px 6px; border-radius: 6px; }}
    QListWidget#folderPickerList::item:selected {{ background: {SELECTION_BG}; }}
    QListWidget#folderPickerList::item:hover {{ background: {_GLASS_STRONG}; }}
    QLabel#folderPickerMore {{ color: {TEXT_SECONDARY}; font-size: 11px; padding: 0 4px 2px; }}
    QToolButton#crumbNav {{
        background: transparent; border: none; border-radius: 6px; padding: 3px;
    }}
    QToolButton#crumbNav:hover {{ background: {_GLASS_STRONG}; }}
    QLabel#breadcrumbMuted {{ color: {TEXT_DISABLED}; }}
    QToolButton#crumbClose {{
        background: {_GLASS}; color: {TEXT_SECONDARY};
        border: 1px solid {_EDGE_SOFT}; border-radius: 8px;
        padding: 3px 10px; font-size: 12px;
    }}
    QToolButton#crumbClose:hover {{
        background: rgba(229, 110, 110, 0.16); color: {TEXT_PRIMARY};
        border-color: {ERROR};
    }}

    QFrame#listToolbar {{
        background: transparent;
        border-bottom: 1px solid {DIVIDER};
        min-height: 46px;
        max-height: 46px;
    }}
    QLabel#folderHint {{ color: {TEXT_SECONDARY}; font-size: 11px; }}
    QFrame#viewSwitch {{
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid {_EDGE_SOFT};
        border-radius: 16px;
    }}
    QToolButton#sortButton {{
        background: rgba(255, 255, 255, 0.03); color: {TEXT_SECONDARY};
        border: 1px solid {_EDGE_SOFT}; border-radius: 16px; padding: 0 12px 0 10px;
        min-height: 32px; max-height: 32px; font-size: 12px; font-weight: 500;
    }}
    QToolButton#sortButton:hover {{ background: {_GLASS_STRONG}; color: {TEXT_PRIMARY}; }}
    QToolButton#sortButton::menu-indicator {{ image: none; width: 0; }}
    QToolButton#viewButton {{
        background: transparent; color: {TEXT_DISABLED};
        border: none; border-radius: 13px; padding: 0 13px;
        min-height: 26px; max-height: 26px; font-size: 12px; font-weight: 500;
    }}
    QToolButton#viewButton:hover {{ background: {_GLASS_STRONG}; color: {TEXT_PRIMARY}; }}
    QToolButton#viewButton:checked {{
        background: qlineargradient(x1:0, y1:0, x2:0.6, y2:1,
            stop:0 {ACCENT_HOVER}, stop:0.75 {ACCENT});
        color: {ACCENT_ON}; font-weight: 600;
    }}

    /* ---- views ---- */
    QListView {{
        background: transparent; border: none; outline: none;
        selection-background-color: transparent;
    }}
    QListView::item {{ color: {TEXT_PRIMARY}; border-radius: 8px; }}
    QListView::item:hover {{ background: {_GLASS}; }}
    QListView::item:selected {{ background: {_ACCENT_FILL}; color: {TEXT_PRIMARY}; }}

    QTableView {{
        background: transparent;
        alternate-background-color: {_GLASS_SOFT};
        border: none;
        gridline-color: {DIVIDER};
        selection-background-color: {SELECTION_BG};
        selection-color: {TEXT_PRIMARY};
        outline: none;
    }}
    QHeaderView::section {{
        background: {_GLASS};
        color: {TEXT_SECONDARY};
        border: none;
        border-bottom: 1px solid {BORDER};
        padding: 6px 8px;
        font-weight: 600;
    }}
    QTableView QTableCornerButton::section {{ background: {_GLASS}; border: none; }}

    /* ---- preview panel ---- */
    QFrame#previewPanel {{
        background: {_GLASS_SOFT};
        border-bottom-right-radius: {_WINDOW_RADIUS}px;
    }}
    QFrame#previewHeader {{
        background: {_GLASS};
        border-bottom: 1px solid {_EDGE_SOFT};
        min-height: 52px;
        max-height: 52px;
    }}
    QLabel#previewTitle {{
        font-family: "{DISPLAY_FAMILY}"; font-size: 14px; font-weight: 600;
    }}
    QLabel#previewSub {{ color: {TEXT_DISABLED}; font-family: "{MONO_FAMILY}"; font-size: 11px; }}
    QToolButton#previewAction {{
        background: {_GLASS};
        border: 1px solid {_EDGE};
        border-radius: 8px;
        color: {TEXT_SECONDARY};
        min-width: 27px; max-width: 27px; min-height: 27px; max-height: 27px;
        padding: 0;
    }}
    QToolButton#previewAction:hover {{ background: {_GLASS_STRONG}; color: {TEXT_PRIMARY}; }}
    QToolButton#previewAction:disabled {{ color: {TEXT_DISABLED}; border-color: {DIVIDER}; }}
    QWidget#imageStage {{
        background: qradialgradient(cx:0.5, cy:0.45, radius:0.95,
            stop:0 {_FILL_ACCENT_18},
            stop:0.45 {_FILL_ACCENT_07},
            stop:1 rgba(11,15,20,0.0));
    }}
    QGraphicsView#imageViewer {{
        background: transparent;
        border: none;
    }}
    /* on-image floating controls */
    QFrame#stageControls {{
        background: {_SCRIM};
        border: 1px solid {_EDGE_STRONG};
        border-radius: 16px;
    }}
    QToolButton#stageZoom {{
        background: transparent; border: none; border-radius: 8px; padding: 4px;
    }}
    QToolButton#stageZoom:hover {{ background: {_GLASS_STRONG}; }}
    QLabel#stageZoomVal {{
        color: #CFD9DE; font-family: "{MONO_FAMILY}"; font-size: 11px; padding: 0 4px;
    }}
    QLabel#stageDivider {{ color: {_EDGE_STRONG}; }}
    QToolButton#stageNav {{
        background: {_SCRIM_SOFT};
        border: 1px solid {_EDGE_STRONG};
        border-radius: 15px;
        padding: 0;
    }}
    QToolButton#stageNav:hover {{ background: {_SCRIM}; border-color: {ACCENT}; }}

    /* video controls: same glass-pill language as the image stage controls */
    QFrame#slideshowBar {{
        background: rgba(10, 14, 18, 0.78);
        border: 1px solid {_EDGE_STRONG};
        border-radius: 22px;
    }}
    QFrame#videoControls {{
        background: {_SCRIM};
        border: 1px solid {_EDGE_STRONG};
        border-radius: 19px;
    }}
    QToolButton#videoBtn {{
        background: transparent; border: none; border-radius: 8px; padding: 4px;
    }}
    QToolButton#videoBtn:hover {{ background: {_GLASS_STRONG}; }}
    QLabel#videoTime {{ color: #CFD9DE; font-family: "{MONO_FAMILY}"; font-size: 11px; }}
    QSlider#videoSeek {{ min-height: 18px; }}
    QSlider#videoSeek::groove:horizontal {{
        height: 4px; border-radius: 2px; background: rgba(255, 255, 255, 0.16);
    }}
    QSlider#videoSeek::sub-page:horizontal {{
        height: 4px; border-radius: 2px; background: {ACCENT};
    }}
    QSlider#videoSeek::handle:horizontal {{
        width: 12px; height: 12px; margin: -4px 0; border-radius: 6px;
        background: {ACCENT_HOVER}; border: 1px solid {ACCENT_ON};
    }}
    QSlider#videoSeek::handle:horizontal:hover {{ background: #FFFFFF; }}

    QFrame#previewFooter {{
        background: {_GLASS};
        border-top: 1px solid {_EDGE_SOFT};
        border-bottom-right-radius: {_WINDOW_RADIUS}px;
    }}
    QLabel#footerKind {{ font-size: 12px; font-weight: 600; }}
    QLabel#footerMeta {{ color: {TEXT_DISABLED}; font-family: "{MONO_FAMILY}"; font-size: 11px; }}

    /* ---- file-panel status strip ---- */
    QFrame#panelStatus {{
        background: {_GLASS_SOFT};
        border-top: 1px solid {_EDGE_SOFT};
    }}
    QFrame#panelStatus QLabel {{ color: {TEXT_SECONDARY}; font-size: 10px; }}
    QLabel#statusDivider {{ color: {TEXT_DISABLED}; }}

    /* ---- shared controls ---- */
    QPushButton {{
        background: {_GLASS};
        color: {TEXT_PRIMARY};
        border: 1px solid {_EDGE};
        border-radius: 17px;
        padding: 0 14px;
        min-height: 34px;
        font-size: 12px; font-weight: 500;
    }}
    QPushButton:hover {{ background: {_GLASS_STRONG}; border-color: {_EDGE_STRONG}; }}
    QPushButton:pressed {{ background: {ELEVATED}; }}
    QPushButton:disabled {{ color: {TEXT_DISABLED}; border-color: {DIVIDER}; }}
    QPushButton::menu-indicator {{ subcontrol-position: right center; right: 8px; }}
    QPushButton#primaryAction {{
        background: qlineargradient(x1:0, y1:0, x2:0.6, y2:1,
            stop:0 {ACCENT_HOVER}, stop:0.7 {ACCENT});
        color: {ACCENT_ON}; border: 1px solid {_EDGE_STRONG}; font-weight: 600;
    }}
    QPushButton#primaryAction:hover {{ background: {ACCENT_HOVER}; }}
    QPushButton#primaryAction:pressed {{ background: {ACCENT_PRESSED}; }}
    QPushButton#primaryAction:disabled {{
        background: {ELEVATED}; color: {TEXT_DISABLED}; border-color: {DIVIDER};
    }}

    QToolButton {{
        background: transparent; color: {TEXT_PRIMARY};
        border: none; border-radius: 6px; padding: 5px 9px;
    }}
    QToolButton:hover {{ background: {_GLASS_STRONG}; }}
    QToolButton:disabled {{ color: {TEXT_DISABLED}; }}

    QPlainTextEdit, QTextEdit {{
        background: {SURFACE};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 10px;
        selection-background-color: {ACCENT};
        selection-color: {ACCENT_ON};
    }}
    QLineEdit {{
        background: {SURFACE};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 4px 8px;
        selection-background-color: {ACCENT};
        selection-color: {ACCENT_ON};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {ACCENT}; }}

    /* combo boxes + settings form (Soft Depth) */
    QComboBox {{
        background: {SURFACE};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 4px 10px;
        min-height: 26px;
    }}
    QComboBox:hover {{ border-color: {BORDER_STRONG}; }}
    QComboBox:focus {{ border-color: {ACCENT}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {OVERLAY};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 8px;
        selection-background-color: {SELECTION_BG};
        selection-color: {TEXT_PRIMARY};
        outline: none;
    }}
    QLabel#audioTitle {{
        color: {TEXT_PRIMARY}; font-family: "{DISPLAY_FAMILY}"; font-size: 16px; font-weight: 600;
    }}
    QLabel#audioArtist {{ color: {TEXT_SECONDARY}; font-size: 12px; }}
    QLabel#fullscreenToast {{
        background: rgba(10, 14, 18, 0.78); color: {TEXT_PRIMARY};
        border: 1px solid {_EDGE_STRONG}; border-radius: 14px;
        padding: 8px 16px; font-size: 13px;
    }}
    QLabel#aboutName {{
        color: {TEXT_PRIMARY}; font-family: "{DISPLAY_FAMILY}"; font-size: 20px; font-weight: 600;
    }}
    QLabel#settingsSection {{
        color: {ACCENT}; font-family: "{DISPLAY_FAMILY}"; font-size: 12px; font-weight: 600;
    }}
    QLabel#settingsField {{ color: {TEXT_SECONDARY}; font-size: 12px; }}
    QLabel#settingsHint {{ color: {TEXT_SECONDARY}; font-size: 11px; }}
    QFrame#settingsDivider {{ background: {_EDGE_SOFT}; border: none; }}
    /* settings fields: one height and one corner for dropdowns, text and numbers */
    QScrollArea#settingsScroll QComboBox {{ border-radius: 10px; min-height: 26px; }}
    QScrollArea#settingsScroll QLineEdit#dialogField {{
        border-radius: 10px; min-height: 34px; padding: 0 10px;
    }}
    QScrollArea#settingsScroll QLineEdit#dialogField:disabled {{ color: {TEXT_DISABLED}; }}
    QFrame#settingsInfo {{
        background: {_FILL_ACCENT_07};
        border: 1px solid {_FILL_ACCENT_18};
        border-radius: 10px;
    }}
    QLabel#settingsInfoText {{ color: {TEXT_PRIMARY}; font-size: 12px; background: transparent; }}
    QSpinBox#dialogSpin {{
        background: rgba(255, 255, 255, 0.035);
        border: 1px solid rgba(255, 255, 255, 0.07);
        border-radius: 10px; min-height: 34px; padding: 0 10px;
        color: {TEXT_PRIMARY};
        selection-background-color: {ACCENT}; selection-color: {ACCENT_ON};
    }}
    QSpinBox#dialogSpin:focus {{ border: 1px solid {ACCENT}; }}
    QScrollArea#settingsScroll {{ background: transparent; border: none; }}
    /* rendered HTML on white paper (pages assume a light background); */
    /* rendered Markdown in the app's own colours */
    QTextBrowser#htmlPage {{
        background: #FFFFFF; color: #1B1F23; border: 1px solid {BORDER}; border-radius: 10px;
        selection-background-color: #B3D7FF; selection-color: #1B1F23;
    }}
    QFrame#findBar {{
        background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px;
    }}
    QLineEdit#findField {{
        background: transparent; border: none; padding: 4px 4px; min-height: 22px;
    }}
    QLabel#findCount {{ color: {TEXT_SECONDARY}; font-size: 11px; padding: 0 6px; }}
    QToolButton#findButton {{ padding: 4px; border-radius: 6px; }}
    QTableView#csvTable {{
        background: {SURFACE}; alternate-background-color: {SURFACE_ALT};
        border: 1px solid {BORDER}; border-radius: 10px; gridline-color: {DIVIDER};
        selection-background-color: {SELECTION_BG}; selection-color: {TEXT_PRIMARY};
    }}
    QTableView#csvTable QHeaderView::section {{
        background: {ELEVATED}; border: none; border-right: 1px solid {DIVIDER};
        border-bottom: 1px solid {DIVIDER}; padding: 3px 8px; font-weight: 600;
    }}
    QTableView#csvTable QTableCornerButton::section {{ background: {ELEVATED}; border: none; }}
    QTextBrowser#markdownPage {{
        background: {SURFACE}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER};
        border-radius: 10px;
        selection-background-color: {ACCENT}; selection-color: {ACCENT_ON};
    }}
    QScrollArea#settingsScroll > QWidget > QWidget {{ background: transparent; }}
    QScrollArea#settingsScroll QScrollBar:vertical {{ width: 8px; margin: 0; }}
    QScrollArea#settingsScroll QScrollBar::handle:vertical {{
        background: rgba(255, 255, 255, 0.10); border-radius: 4px; margin: 0; min-height: 32px;
    }}
    QScrollArea#settingsScroll QScrollBar::handle:vertical:hover {{
        background: rgba(255, 255, 255, 0.18);
    }}
    QLabel#colorHex {{ color: {TEXT_SECONDARY}; font-family: "{MONO_FAMILY}"; font-size: 11px; }}
    QPushButton#colorSwatch {{ padding: 0; min-height: 24px; }}
    QCheckBox {{ color: {TEXT_PRIMARY}; font-size: 12px; spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border-radius: 5px;
        border: 1px solid {_EDGE_STRONG}; background: {_GLASS};
    }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

    QProgressBar {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 5px;
        min-height: 8px;
        text-align: center;
        color: {TEXT_SECONDARY};
    }}
    QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
    QProgressBar#loadingBar {{ min-height: 6px; }}
    QProgressBar#extractBar {{ min-height: 10px; max-height: 10px; border-radius: 5px; }}
    QToolButton#archiveLock {{
        background: transparent; border: none; border-radius: 9px; padding: 1px;
        min-width: 20px; min-height: 20px;
    }}
    QToolButton#archiveLock:hover {{ background: rgba(255, 255, 255, 0.08); }}
    QWidget#busyOverlay {{ background: rgba(10, 14, 18, 0.55); }}
    QFrame#busyCard {{
        background: {_GLASS};
        border: 1px solid {_EDGE};
        border-radius: 16px;
    }}
    QLabel#busyTitle {{
        color: {TEXT_PRIMARY}; font-size: 13px; font-weight: 600; background: transparent;
    }}
    QLabel#busyDetail {{ color: {TEXT_SECONDARY}; font-size: 11px; background: transparent; }}
    QFrame#extractCard {{
        background: rgba(255, 255, 255, 0.035);
        border: 1px solid rgba(255, 255, 255, 0.07);
        border-radius: 12px;
    }}
    QLabel#extractState {{ color: {TEXT_SECONDARY}; font-size: 11px; background: transparent; }}
    QLabel#extractFile {{
        color: {TEXT_PRIMARY}; font-family: "{MONO_FAMILY}"; font-size: 12px;
        background: transparent;
    }}
    QLabel#extractNumbers {{
        color: {TEXT_SECONDARY}; font-family: "{MONO_FAMILY}"; font-size: 11px;
        background: transparent;
    }}
    QLabel#extractValue {{ color: {TEXT_PRIMARY}; font-size: 12px; }}

    QMenuBar {{ background: transparent; color: {TEXT_PRIMARY}; }}
    QMenuBar::item:selected {{ background: {_GLASS_STRONG}; border-radius: 5px; }}
    QMenu {{
        background: {OVERLAY};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 4px;
    }}
    QMenu::item {{ padding: 5px 22px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {SELECTION_BG}; }}
    QMenu::separator {{ height: 1px; background: {DIVIDER}; margin: 4px 8px; }}

    QToolTip {{
        background: {OVERLAY}; color: {TEXT_PRIMARY};
        border: 1px solid {BORDER}; padding: 5px 8px;
    }}

    QSplitter::handle {{ background: transparent; }}
    QSplitter::handle:horizontal {{ width: 1px; }}
    QSplitter::handle:hover {{ background: {ACCENT}; }}

    QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
    QScrollBar::handle:vertical {{
        background: {BORDER_STRONG}; min-height: 40px; border-radius: 5px; margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {BORDER_STRONG}; min-width: 40px; border-radius: 5px; margin: 2px;
    }}
    QScrollBar::handle:hover {{ background: #4A5563; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """


def stylesheet() -> str:
    """Build the full QSS string from the current palette tokens."""
    tokens = dict(vars(palette))  # all palette module globals, read live
    tokens["_FILL_SECONDARY_16"] = palette._rgba(palette.SECONDARY, 0.16)
    tokens["_FILL_SECONDARY_06"] = palette._rgba(palette.SECONDARY, 0.06)
    tokens["_FILL_ACCENT_18"] = palette._rgba(palette.ACCENT, 0.18)
    tokens["_FILL_ACCENT_07"] = palette._rgba(palette.ACCENT, 0.07)
    return _TEMPLATE.format(**tokens)
