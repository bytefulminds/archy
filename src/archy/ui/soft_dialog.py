from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from archy.i18n import tr
from archy.ui import theme

# Custom Soft Depth dialogs: frameless, translucent, with the same rounded
# windowRoot, custom titlebar (drag to move + close button), fonts and
# search-bar-style inputs as the main window. Used for the app's own prompts
# (confirm, text input, password, file-name conflicts) so nothing falls back to
# the platform's native message box. The OS file picker stays native by design.


class _DialogTitleBar(QFrame):
    """Drag anywhere on the titlebar to move the frameless dialog."""

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
        super().mousePressEvent(event)


class SoftDialog(QDialog):
    def __init__(self, parent: QWidget | None, title: str) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self._value: object | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        root = QFrame()
        root.setObjectName("dialogRoot")
        outer.addWidget(root)

        self._root_col = QVBoxLayout(root)
        self._root_col.setContentsMargins(0, 0, 0, 0)
        self._root_col.setSpacing(0)

        bar = _DialogTitleBar()
        bar.setObjectName("dialogHeader")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 0, 8, 0)
        title_label = QLabel(title)
        title_label.setObjectName("dialogTitle")
        close = QToolButton()
        close.setObjectName("winClose")
        close.setIcon(theme.symbol_icon("x", 16, theme.TEXT_SECONDARY))
        close.setIconSize(_qsize(16))
        close.clicked.connect(self.reject)
        bl.addWidget(title_label)
        bl.addStretch(1)
        bl.addWidget(close)
        self._root_col.addWidget(bar)

        self._body = QVBoxLayout()
        self._body.setContentsMargins(18, 16, 18, 10)
        self._body.setSpacing(12)
        self._root_col.addLayout(self._body)

        self._button_row = QHBoxLayout()
        self._button_row.setContentsMargins(18, 4, 18, 16)
        self._button_row.setSpacing(8)
        self._button_row.addStretch(1)
        self._root_col.addLayout(self._button_row)

    # ---------------------------------------------------------- building
    def add_body_widget(self, widget: QWidget) -> None:
        self._body.addWidget(widget)

    def add_button(self, label: str, value: object, *, primary: bool = False) -> QPushButton:
        button = QPushButton(label)
        if primary:
            button.setObjectName("primaryAction")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumWidth(96)
        button.clicked.connect(lambda: self._choose(value))
        self._button_row.addWidget(button)
        return button

    def _choose(self, value: object) -> None:
        self._value = value
        self.accept()

    def value(self) -> object | None:
        return self._value


def _qsize(n: int):  # noqa: ANN202 - tiny local helper
    from PySide6.QtCore import QSize

    return QSize(n, n)


# ------------------------------------------------------------------ helpers
def message(parent: QWidget | None, title: str, text: str, *, ok_label: str | None = None) -> None:
    """A themed one-button information/error dialog."""
    dlg = SoftDialog(parent, title)
    label = QLabel(text)
    label.setObjectName("dialogText")
    label.setWordWrap(True)
    dlg.add_body_widget(label)
    dlg.add_button(ok_label or tr("button.ok"), True, primary=True)
    dlg.setMinimumWidth(380)
    dlg.exec()


def confirm(
    parent: QWidget | None,
    title: str,
    text: str,
    buttons: Sequence[tuple[str, object, bool]],
) -> object | None:
    """A themed confirm dialog. ``buttons`` is a sequence of
    (label, return-value, is-primary). Returns the chosen value, or None if the
    dialog was dismissed (close button / Esc)."""
    dlg = SoftDialog(parent, title)
    label = QLabel(text)
    label.setObjectName("dialogText")
    label.setWordWrap(True)
    dlg.add_body_widget(label)
    for lbl, value, primary in buttons:
        dlg.add_button(lbl, value, primary=primary)
    dlg.setMinimumWidth(400)
    dlg.exec()
    return dlg.value()


def prompt_text(
    parent: QWidget | None,
    title: str,
    label: str,
    *,
    initial: str = "",
    password: bool = False,
    ok_label: str | None = None,
) -> str | None:
    """A themed single-line input dialog with a search-bar-style field. Returns
    the entered text, or None if cancelled."""
    dlg = SoftDialog(parent, title)
    prompt = QLabel(label)
    prompt.setObjectName("dialogText")
    field = QLineEdit(initial)
    field.setObjectName("dialogField")
    if password:
        field.setEchoMode(QLineEdit.EchoMode.Password)
    field.selectAll()
    field.returnPressed.connect(lambda: dlg._choose("__ok__"))
    dlg.add_body_widget(prompt)
    dlg.add_body_widget(field)
    dlg.add_button(tr("button.cancel"), None, primary=False)
    dlg.add_button(ok_label or tr("button.ok"), "__ok__", primary=True)
    dlg.setMinimumWidth(400)
    field.setFocus()
    dlg.exec()
    return field.text() if dlg.value() == "__ok__" else None


def confirm_with_toggle(
    parent: QWidget | None,
    title: str,
    text: str,
    toggle_label: str,
    buttons: Sequence[tuple[str, object, bool]],
) -> tuple[object | None, bool]:
    """Like ``confirm`` but with a checkbox (e.g. "apply to all"). Returns
    (chosen value, checkbox state)."""
    dlg = SoftDialog(parent, title)
    label = QLabel(text)
    label.setObjectName("dialogText")
    label.setWordWrap(True)
    dlg.add_body_widget(label)
    check = QCheckBox(toggle_label)
    check.setObjectName("dialogCheck")
    dlg.add_body_widget(check)
    for lbl, value, primary in buttons:
        dlg.add_button(lbl, value, primary=primary)
    dlg.setMinimumWidth(430)
    dlg.exec()
    return dlg.value(), check.isChecked()
