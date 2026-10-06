from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QColorDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from archy import i18n
from archy.core import memory
from archy.i18n import tr
from archy.ui import theme
from archy.ui.settings import (
    DEFAULT_ACCENT,
    DEFAULT_ACCENT2,
    Settings,
    system_languages,
    view_labels,
)
from archy.ui.soft_dialog import SoftDialog
from archy.ui.widgets import SoftComboBox, SoftToggle

if TYPE_CHECKING:
    from collections.abc import Callable

# The Soft Depth settings screen. Reads current values from Settings, and on OK
# writes them back and returns True so the window can re-apply them live.
#
# Layout rules (shared by all tabs, so nothing jumps when you switch):
# - every tab is one grid: labels in a column of one shared width, controls in
#   the second column; hints sit under their control, in that same column
# - every control is ROW_HEIGHT tall and labels are centred on it
# - dropdowns share one width (wider only when their text needs it), number
#   fields another; only the folder field stretches
# - each tab scrolls on its own, and only when the screen is too small for it

ROW_HEIGHT = 36
COMBO_WIDTH = 260
SPIN_WIDTH = 96
HINT_WIDTH = 460
_LABEL_MIN, _LABEL_MAX = 150, 230
_DIALOG_WIDTH = 640


def _choices(prefix: str, values: tuple[str, ...]) -> list[tuple[str, str]]:
    """(label, value) pairs; the label is the translation of ``prefix.value``."""
    return [(tr(f"{prefix}.{value}"), value) for value in values]


def _combo(pairs: list[tuple[str, str]], current: str) -> SoftComboBox:
    combo = SoftComboBox()
    for label, value in pairs:
        combo.addItem(label, value)
    idx = next((i for i, (_, v) in enumerate(pairs) if v == current), 0)
    combo.setCurrentIndex(idx)
    _fit_combo(combo)
    return combo


def _fit_combo(combo: SoftComboBox) -> None:
    combo.setMinimumWidth(COMBO_WIDTH)
    combo.setFixedHeight(ROW_HEIGHT)
    combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class _Page:
    """One settings tab: a two-column grid inside its own scroll area."""

    def __init__(self) -> None:
        self.scroll = QScrollArea()
        self.scroll.setObjectName("settingsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.widget = QWidget()
        self.widget.setObjectName("settingsPage")
        self.widget.setAutoFillBackground(False)
        self.grid = QGridLayout(self.widget)
        self.grid.setContentsMargins(0, 0, 14, 4)  # right: room for the scrollbar
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(10)
        self.grid.setColumnStretch(1, 1)
        self.scroll.setWidget(self.widget)
        self.labels: list[QLabel] = []
        self._row = 0

    def section(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("settingsSection")
        # Air above every section but the first of the tab.
        label.setContentsMargins(0, 14 if self._row else 0, 0, 0)
        self.grid.addWidget(label, self._row, 0, 1, 2)
        self._row += 1

    def row(self, text: str, widget: QWidget) -> None:
        label = QLabel(text)
        label.setObjectName("settingsField")
        label.setWordWrap(True)
        label.setMinimumHeight(ROW_HEIGHT)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.labels.append(label)
        self.grid.addWidget(label, self._row, 0, Qt.AlignmentFlag.AlignTop)
        self.control(widget)

    def control(self, widget: QWidget) -> None:
        """A control in the field column, without a label of its own."""
        widget.setMinimumHeight(max(widget.minimumHeight(), ROW_HEIGHT))
        self.grid.addWidget(widget, self._row, 1, Qt.AlignmentFlag.AlignVCenter)
        self._row += 1

    def hint(self, text: str) -> QLabel:
        label = _hint(text)
        self.grid.addWidget(label, self._row, 1)
        self._row += 1
        return label

    def wide(self, widget: QWidget) -> None:
        """A widget across both columns (callouts)."""
        self.grid.addWidget(widget, self._row, 0, 1, 2)
        self._row += 1

    def finish(self) -> None:
        self.grid.setRowStretch(self._row, 1)

    def content_height(self, width: int) -> int:
        layout = self.widget.layout()
        height = layout.totalHeightForWidth(width) if layout.hasHeightForWidth() else -1
        return max(height, layout.totalSizeHint().height())


def _callout(icon: str, text: str) -> QFrame:
    """A tinted note with an icon (the same look as the "applies later" bar)."""
    box = QFrame()
    box.setObjectName("settingsInfo")
    layout = QHBoxLayout(box)
    layout.setContentsMargins(12, 9, 12, 9)
    layout.setSpacing(10)
    pic = QLabel()
    pic.setPixmap(theme.symbol_icon(icon, 16, theme.ACCENT).pixmap(16, 16))
    pic.setAlignment(Qt.AlignmentFlag.AlignTop)
    words = QLabel(text)
    words.setObjectName("settingsInfoText")
    words.setWordWrap(True)
    layout.addWidget(pic)
    layout.addWidget(words, stretch=1)
    return box


def _color_picker(
    dlg: QWidget, state: dict[str, str], key: str
) -> tuple[QWidget, Callable[[], None]]:
    """A swatch + hex label that opens a color dialog. Writes the chosen hex into
    ``state[key]`` and returns (row widget, refresh callback)."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    swatch = QPushButton()
    swatch.setObjectName("colorSwatch")
    swatch.setFixedSize(34, 24)
    swatch.setCursor(Qt.CursorShape.PointingHandCursor)
    hexlabel = QLabel()
    hexlabel.setObjectName("colorHex")

    def refresh() -> None:
        value = state[key]
        swatch.setStyleSheet(
            f"background:{value}; border:1px solid rgba(255,255,255,0.22); border-radius:7px;"
        )
        hexlabel.setText(value.upper())

    def pick() -> None:
        chosen = QColorDialog.getColor(QColor(state[key]), dlg, tr("settings.pick_color"))
        if chosen.isValid():
            state[key] = chosen.name().upper()
            refresh()

    swatch.clicked.connect(pick)
    layout.addWidget(swatch)
    layout.addWidget(hexlabel)
    layout.addStretch(1)
    refresh()
    return row, refresh


def open_settings(
    parent: QWidget, settings: Settings, on_prune: Callable[[], None] | None = None
) -> bool:
    dlg = SoftDialog(parent, tr("settings.title"))

    host = QWidget()
    outer = QVBoxLayout(host)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(12)

    # Tabs: the same segmented switch as the file views.
    switch = QFrame()
    switch.setObjectName("viewSwitch")
    switch.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    switch_row = QHBoxLayout(switch)
    switch_row.setContentsMargins(3, 3, 3, 3)
    switch_row.setSpacing(2)
    stack = QStackedWidget()
    group = QButtonGroup(dlg)
    group.setExclusive(True)
    pages: list[_Page] = []

    def add_page(title: str) -> _Page:
        button = QToolButton()
        button.setObjectName("viewButton")
        button.setCheckable(True)
        button.setText(title)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        index = stack.count()
        button.clicked.connect(lambda _c=False, i=index: stack.setCurrentIndex(i))
        group.addButton(button, index)
        switch_row.addWidget(button)
        page = _Page()
        stack.addWidget(page.scroll)
        pages.append(page)
        return page

    general = add_page(tr("settings.tab.general"))
    temp_page = add_page(tr("settings.tab.temp"))
    advanced = add_page(tr("settings.tab.advanced"))
    about_page = add_page(tr("settings.tab.about"))
    _fill_about(dlg, about_page)
    group.button(0).setChecked(True)
    outer.addWidget(switch, 0, Qt.AlignmentFlag.AlignHCenter)
    outer.addWidget(stack, stretch=1)
    divider = QFrame()
    divider.setObjectName("settingsDivider")
    divider.setFixedHeight(1)
    outer.addWidget(divider)

    # ================================================================ General
    general.section(tr("settings.section.view"))
    system = i18n.resolve("auto", system_languages())
    system_name = dict(i18n.available_languages()).get(system, system)
    lang_combo = _combo(
        [(tr("settings.language_auto", name=system_name), "auto"), *i18n_choices()],
        settings.language,
    )
    general.row(tr("settings.language"), lang_combo)
    general.hint(tr("settings.language_hint", folder=str(i18n.user_language_dir())))
    start_combo = _combo(
        [(label, str(i)) for i, label in enumerate(view_labels())], str(settings.start_view)
    )
    general.row(tr("settings.start_view"), start_combo)
    fill_combo = _combo(_choices("settings.fill", ("contain", "cover")), settings.preview_fill)
    general.row(tr("settings.preview_fill"), fill_combo)
    density_check = SoftToggle(tr("settings.compact_rows"))
    density_check.setChecked(settings.compact_density)
    general.control(density_check)

    general.section(tr("settings.section.accent"))
    colors = {"accent": settings.accent, "accent2": settings.accent2}
    primary_row, refresh_primary = _color_picker(dlg, colors, "accent")
    secondary_row, refresh_secondary = _color_picker(dlg, colors, "accent2")
    general.row(tr("settings.primary"), primary_row)
    general.row(tr("settings.secondary"), secondary_row)
    reset_colors = QPushButton(tr("settings.reset_colors"))

    def _reset_colors() -> None:
        colors["accent"] = DEFAULT_ACCENT
        colors["accent2"] = DEFAULT_ACCENT2
        refresh_primary()
        refresh_secondary()

    reset_colors.clicked.connect(_reset_colors)
    general.control(_row(reset_colors))

    general.section(tr("settings.section.window"))
    remember_check = SoftToggle(tr("settings.remember_window"))
    remember_check.setChecked(settings.remember_window)
    general.row(tr("settings.on_quit"), remember_check)
    general.finish()

    # ========================================================= Temporary files
    temp_page.section(tr("settings.section.workmode"))
    modes = ("memory", "overflow", "disk")
    mode_combo = _combo(_choices("settings.mode", modes), settings.temp_mode)
    temp_page.row(tr("settings.mode"), mode_combo)
    mode_hint = temp_page.hint("")

    def _sync_mode_hint() -> None:
        mode_hint.setText(tr(f"settings.mode_hint.{mode_combo.currentData()}"))

    _sync_mode_hint()
    mode_combo.currentIndexChanged.connect(lambda _i: _sync_mode_hint())

    temp_page.section(tr("settings.section.folder"))
    loc_combo = _combo(_choices("settings.loc", ("system", "custom")), settings.temp_location)
    temp_page.row(tr("settings.location"), loc_combo)
    dir_row = QWidget()
    dir_layout = QHBoxLayout(dir_row)
    dir_layout.setContentsMargins(0, 0, 0, 0)
    dir_layout.setSpacing(8)
    dir_edit = QLineEdit(settings.temp_dir)
    dir_edit.setObjectName("dialogField")
    dir_edit.setPlaceholderText(tr("settings.choose_folder"))
    dir_edit.setFixedHeight(ROW_HEIGHT)
    browse = QPushButton(tr("settings.browse"))
    dir_layout.addWidget(dir_edit, stretch=1)
    dir_layout.addWidget(browse)
    temp_page.row(tr("settings.custom_folder"), dir_row)
    prune_check = SoftToggle(tr("settings.prune_on_exit"))
    prune_check.setChecked(settings.prune_on_exit)
    temp_page.control(prune_check)
    if on_prune is not None:
        clean_now = QPushButton(tr("settings.prune_now"))
        clean_now.clicked.connect(lambda: on_prune())
        temp_page.control(_row(clean_now))
    temp_page.wide(_callout("lock", tr("settings.temp_security")))
    temp_page.finish()

    def _sync_temp_enabled() -> None:
        custom = loc_combo.currentData() == "custom"
        dir_edit.setEnabled(custom)
        browse.setEnabled(custom)

    _sync_temp_enabled()
    loc_combo.currentIndexChanged.connect(lambda _i: _sync_temp_enabled())

    def _browse() -> None:
        from PySide6.QtWidgets import QFileDialog

        path = QFileDialog.getExistingDirectory(
            dlg, tr("settings.choose_temp_folder"), dir_edit.text()
        )
        if path:
            dir_edit.setText(path)

    browse.clicked.connect(_browse)

    # =============================================================== Advanced
    auto = _auto_values()
    ram_gb = max(1, round(memory.total() / _GB))
    advanced.section(tr("settings.section.memory"))

    cap_mode = _combo(_choices("settings.cap", ("percent", "fixed")), settings.cap_mode)
    cap_percent = _spin(10, 95, settings.res_int("res/cap_percent") or 75, " %")
    cap_gb = _spin(1, ram_gb, settings.res_int("res/cap_gb") or min(8, ram_gb), " GB")

    def _sync_cap() -> None:
        fixed = cap_mode.currentData() == "fixed"
        cap_percent.setVisible(not fixed)
        cap_gb.setVisible(fixed)

    cap_mode.currentIndexChanged.connect(lambda _i: _sync_cap())
    _sync_cap()
    advanced.row(tr("settings.cap"), _row(cap_mode, cap_percent, cap_gb))

    reserve = _AutoFixed(settings.res_int("res/reserve_gb"), 1, ram_gb, " GB", auto["reserve"])
    advanced.row(tr("settings.reserve"), reserve.widget)

    advanced.section(tr("settings.section.buffers"))
    folder = _AutoFixed(settings.res_int("res/folder_mb"), 64, 262144, " MB", auto["folder"])
    thumbs = _AutoFixed(settings.res_int("res/thumb_mb"), 32, 16384, " MB", auto["thumbs"])
    reads = _AutoFixed(settings.res_int("res/read_mb"), 32, 16384, " MB", auto["reads"])
    video = _AutoFixed(settings.res_int("res/video_mb"), 64, 262144, " MB", auto["video"])
    advanced.row(tr("settings.folder_buffer"), folder.widget)
    advanced.row(tr("settings.thumb_cache"), thumbs.widget)
    advanced.row(tr("settings.read_cache"), reads.widget)
    advanced.row(tr("settings.video_memory"), video.widget)

    advanced.section(tr("settings.section.cpu"))
    threads = _AutoFixed(settings.res_int("res/threads"), 1, 32, "", auto["threads"])
    advanced.row(tr("settings.thumb_threads"), threads.widget)
    advanced.hint(tr("settings.advanced_hint"))

    reset_resources = QPushButton(tr("settings.reset_resources"))

    def _reset_resources() -> None:
        cap_mode.setCurrentIndex(0)
        cap_percent.setValue(75)
        for control in (reserve, folder, thumbs, reads, video, threads):
            control.set_auto()

    reset_resources.clicked.connect(_reset_resources)
    advanced.control(_row(reset_resources))
    advanced.finish()

    # One label width for every tab, so the controls line up when switching;
    # long labels (other languages) wrap instead of pushing the column out.
    label_width = max(
        (label.sizeHint().width() for page in pages for label in page.labels), default=0
    )
    label_width = min(_LABEL_MAX, max(_LABEL_MIN, label_width))
    for page in pages:
        page.grid.setColumnMinimumWidth(0, label_width)
        for label in page.labels:
            label.setFixedWidth(label_width)

    # The mode description changes with the mode; reserve the room of the
    # longest one, so the rows below it never move.
    content_width = _DIALOG_WIDTH - 2 * 18 - 14  # dialog margins, scrollbar gap
    hint_width = min(HINT_WIDTH, content_width - label_width - 14)
    mode_hint.ensurePolished()
    shown = mode_hint.text()
    tallest_hint = 0
    for mode in modes:
        mode_hint.setText(tr(f"settings.mode_hint.{mode}"))
        tallest_hint = max(tallest_hint, mode_hint.heightForWidth(hint_width))
    mode_hint.setText(shown)
    mode_hint.setMinimumHeight(tallest_hint)

    # Most settings apply the moment you save. These few decide how a folder is
    # unpacked, so they apply from the next unpack: say so, but only once one
    # of them was actually changed.
    info = _callout("info", "")
    info_text = info.findChild(QLabel, "settingsInfoText")
    info.hide()
    outer.addWidget(info)

    def _current() -> dict[str, object]:
        # Keyed by the translation key of the setting's name.
        return {
            "settings.changed.workmode": mode_combo.currentData(),
            "settings.changed.temp_folder": (loc_combo.currentData(), dir_edit.text().strip()),
            "settings.changed.folder_buffer": folder.value(),
        }

    initial = _current()
    initial_language = lang_combo.currentData()

    def _update_info() -> None:
        messages = []
        changed = [tr(key) for key, value in _current().items() if value != initial[key]]
        if changed:
            names = changed[-1]
            if len(changed) > 1:
                names = ", ".join(changed[:-1]) + tr("settings.list_and") + names
            messages.append(tr("settings.changed_reopen", names=names))
        if lang_combo.currentData() != initial_language:
            messages.append(tr("settings.changed_restart"))
        info_text.setText("\n\n".join(messages))
        info.setVisible(bool(messages))

    mode_combo.currentIndexChanged.connect(lambda _i: _update_info())
    loc_combo.currentIndexChanged.connect(lambda _i: _update_info())
    dir_edit.textChanged.connect(lambda _t: _update_info())
    folder.changed.connect(_update_info)
    lang_combo.currentIndexChanged.connect(lambda _i: _update_info())

    dlg.add_body_widget(host)
    dlg.add_button(tr("button.cancel"), None, primary=False)
    dlg.add_button(tr("button.save"), "__ok__", primary=True)
    dlg.setMinimumWidth(_DIALOG_WIDTH)

    # Tall enough that no tab scrolls on a normal screen; on a small screen
    # the tabs that do not fit scroll, each on its own.
    # (About scrolls on its own: its list of components should not make the
    # whole dialog taller.)
    tallest = max(page.content_height(content_width) for page in pages if page is not about_page)
    screen = parent.screen() if parent is not None else None
    avail = screen.availableGeometry().height() if screen is not None else 900
    stack.setMinimumHeight(min(tallest + 2, max(280, avail - 260)))
    dlg.exec()

    if dlg.value() != "__ok__":
        return False

    settings.set("ui/language", lang_combo.currentData())
    settings.set("view/start", int(start_combo.currentData()))
    settings.set("view/compact_density", density_check.isChecked())
    settings.set("preview/fill", fill_combo.currentData())
    settings.set("window/remember", remember_check.isChecked())
    settings.set("temp/mode", mode_combo.currentData())
    settings.set("temp/location", loc_combo.currentData())
    settings.set("temp/dir", dir_edit.text())
    settings.set("temp/prune_on_exit", prune_check.isChecked())
    settings.set("res/cap_mode", cap_mode.currentData())
    settings.set("res/cap_percent", cap_percent.value())
    settings.set("res/cap_gb", cap_gb.value())
    settings.set("res/reserve_gb", reserve.value())
    settings.set("res/folder_mb", folder.value())
    settings.set("res/thumb_mb", thumbs.value())
    settings.set("res/read_mb", reads.value())
    settings.set("res/video_mb", video.value())
    settings.set("res/threads", threads.value())
    settings.set("theme/accent", colors["accent"])
    settings.set("theme/accent2", colors["accent2"])
    settings.sync()
    return True


# -------------------------------------------------------------------- about
def _link(url: str, label: str | None = None) -> str:
    return f'<a href="{url}" style="color:{theme.ACCENT}; text-decoration:none">{label or url}</a>'


def _rich(text: str, object_name: str = "settingsField") -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setOpenExternalLinks(True)  # opens the system browser; Archy loads nothing
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    return label


def _fill_about(dlg: QWidget, page: _Page) -> None:
    """Settings > About: version, maker, license and the third-party notices."""
    from pathlib import Path

    from PySide6.QtGui import QPixmap

    from archy import about

    head = QWidget()
    row = QHBoxLayout(head)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(14)
    logo = QLabel()
    icon = Path(__file__).resolve().parents[1] / "resources" / "icons" / "archy.png"
    pixmap = QPixmap(str(icon))
    if not pixmap.isNull():
        ratio = dlg.devicePixelRatioF()
        pixmap = pixmap.scaled(
            int(56 * ratio),
            int(56 * ratio),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pixmap.setDevicePixelRatio(ratio)
        logo.setPixmap(pixmap)
    row.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
    words = QVBoxLayout()
    words.setSpacing(2)
    name = QLabel("Archy")
    name.setObjectName("aboutName")
    version = QLabel(tr("about.version", version=about.version()))
    version.setObjectName("settingsField")
    maker = _rich(
        tr("about.made_by", maker=about.MAKER) + " · " + _link(about.WEBSITE, about.WEBSITE_LABEL)
    )
    words.addWidget(name)
    words.addWidget(version)
    words.addWidget(maker)
    row.addLayout(words, 1)
    page.wide(head)

    page.section(tr("about.section.license"))
    page.wide(_rich(tr("about.license", source=_link(about.SOURCE))))

    page.section(tr("about.section.third_party"))
    page.wide(_rich(tr("about.third_party_intro")))
    lines = "".join(
        f"<tr><td style='padding:2px 14px 2px 0'>{c.name}</td>"
        f"<td style='padding:2px 0; color:{theme.TEXT_DISABLED}'>{c.license}</td></tr>"
        for c in about.COMPONENTS
    )
    page.wide(_rich(f"<table cellspacing='0'>{lines}</table>"))
    show = QPushButton(tr("about.show_licenses"))
    show.clicked.connect(lambda: _show_notices(dlg))
    page.wide(_row(show))
    page.finish()


def _show_notices(parent: QWidget) -> None:
    from PySide6.QtWidgets import QPlainTextEdit

    from archy import about
    from archy.services.providers import text_font

    dlg = SoftDialog(parent, tr("about.licenses_title"))
    text = QPlainTextEdit()
    text.setReadOnly(True)
    text.setFont(text_font())
    text.setPlainText(about.notices_text())
    text.setMinimumSize(640, 460)
    dlg.add_body_widget(text)
    dlg.add_button(tr("button.ok"), True, primary=True)
    dlg.exec()


# ------------------------------------------------------------------ helpers
_GB = 1024 * 1024 * 1024


def i18n_choices() -> list[tuple[str, str]]:
    """(name in that language, code) for every available language."""
    return [(name, code) for code, name in i18n.available_languages()]


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("settingsHint")
    label.setWordWrap(True)
    label.setMaximumWidth(HINT_WIDTH)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    return label


def _row(*widgets: QWidget) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch(1)
    return row


def _spin(minimum: int, maximum: int, value: int, suffix: str) -> QSpinBox:
    spin = QSpinBox()
    spin.setObjectName("dialogSpin")
    spin.setRange(minimum, max(minimum, maximum))
    spin.setValue(min(max(value, minimum), max(minimum, maximum)))
    spin.setSuffix(suffix)
    spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    spin.setAlignment(Qt.AlignmentFlag.AlignRight)
    spin.setFixedSize(SPIN_WIDTH, ROW_HEIGHT)
    return spin


class _AutoFixed(QObject):
    """'Automatic (now ...)' or a fixed value. value() is 0 for Automatic."""

    changed = Signal()

    def __init__(
        self, current: int, minimum: int, maximum: int, suffix: str, auto_text: str
    ) -> None:
        super().__init__()
        self.widget = QWidget()
        layout = QHBoxLayout(self.widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._combo = SoftComboBox()
        self._combo.addItem(tr("settings.auto_now", value=auto_text), "auto")
        self._combo.addItem(tr("settings.fixed"), "fixed")
        _fit_combo(self._combo)
        self._spin = _spin(minimum, maximum, current or minimum, suffix)
        layout.addWidget(self._combo)
        layout.addWidget(self._spin)
        layout.addStretch(1)
        self._combo.setCurrentIndex(1 if current > 0 else 0)
        self._combo.currentIndexChanged.connect(lambda _i: self._sync())
        self._combo.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self._spin.valueChanged.connect(lambda _v: self.changed.emit())
        self._sync()

    def _sync(self) -> None:
        self._spin.setVisible(self._combo.currentData() == "fixed")

    def set_auto(self) -> None:
        self._combo.setCurrentIndex(0)

    def value(self) -> int:
        return self._spin.value() if self._combo.currentData() == "fixed" else 0


def _auto_values() -> dict[str, str]:
    """What Automatic amounts to right now, shown next to each choice."""
    from PySide6.QtCore import QThread

    from archy.core import backend as core_backend
    from archy.services.formatting import format_size
    from archy.ui import thumbnail_loader as thumbs

    def approx(value: int) -> str:
        return "≈ " + format_size(max(0, int(value)))

    reserve = max(2 * _GB, int(memory.total() * 0.20))
    return {
        "reserve": approx(reserve),
        "folder": approx(
            memory.budget(
                core_backend._PREFETCH_FRACTION,
                floor=core_backend._PREFETCH_MIN_BUDGET,
                ceiling=core_backend._PREFETCH_MAX_BUDGET,
            )
        ),
        "thumbs": approx(
            memory.budget(
                thumbs._CACHE_RAM_FRACTION,
                floor=thumbs._CACHE_MIN_BYTES,
                ceiling=thumbs._CACHE_MAX_BYTES,
            )
        ),
        "reads": approx(
            memory.budget(
                core_backend._READ_CACHE_FRACTION,
                floor=core_backend._READ_CACHE_MIN_TOTAL,
                ceiling=core_backend._READ_CACHE_MAX_TOTAL,
            )
        ),
        "video": approx(memory.spare() // 3),
        "threads": str(max(2, min(6, QThread.idealThreadCount() - 2))),
    }
