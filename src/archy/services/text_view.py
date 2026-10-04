from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    Qt,
    QUrl,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QPalette,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QStackedWidget,
    QTableView,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from archy.i18n import tr

# Text previews with a bit more than plain text:
#
# - syntax colours for JSON, YAML, TOML, HTML/XML and Markdown, in the colours
#   most editors use for them (VS Code's dark theme), and "rainbow" CSV: every
#   column its own colour, so a wide table stays readable as text;
# - HTML and Markdown can be shown rendered, with a switch to the text behind.
#
# Rendering is deliberately offline: nothing outside the file is ever loaded
# (no remote images, no web fonts, no scripts), and links do not navigate
# away. Everything stays in memory, like every other preview.
#
# Highlighting runs in Python, a line at a time, on the UI thread. It is
# skipped for very large texts so a preview never stalls the window.

HIGHLIGHT_LIMIT = 400_000  # characters

_EXT_KIND = {
    ".json": "json",
    ".jsonl": "json",
    ".geojson": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".csv": "csv",
    ".tsv": "csv",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".xml": "xml",
    ".md": "markdown",
    ".markdown": "markdown",
}
RENDERABLE = {"html", "markdown", "csv"}

# VS Code "Dark+" colours: what most people know JSON/YAML to look like.
_KEY = "#9CDCFE"
_STRING = "#CE9178"
_NUMBER = "#B5CEA8"
_KEYWORD = "#569CD6"
_COMMENT = "#6A9955"
_SECTION = "#4EC9B0"
_PUNCT = "#808B96"
_TAG = "#569CD6"
_HEADING = "#569CD6"
# Rainbow CSV: distinct hues that all read well on the dark background.
_RAINBOW = (
    "#E8E8E8",
    "#5FB3F9",
    "#F2C55C",
    "#7FD18B",
    "#F28FAD",
    "#B39DF2",
    "#4FD1C5",
    "#F6A15B",
    "#A6D96A",
    "#E57373",
)

# Show the rendered view or the source, per kind, for the rest of the session.
_show_source = {"html": False, "markdown": False, "csv": False}


def text_kind(name: str) -> str:
    """'json', 'yaml', 'toml', 'csv', 'html', 'xml', 'markdown' or ''."""
    dot = name.rfind(".")
    return _EXT_KIND.get(name[dot:].lower(), "") if dot >= 0 else ""


def _fmt(color: str, *, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    if italic:
        fmt.setFontItalic(True)
    return fmt


# ----------------------------------------------------------------- highlighters
class _RuleHighlighter(QSyntaxHighlighter):
    """Regex rules, applied in order (a later rule wins where they overlap).
    A rule is (pattern, format, group)."""

    rules: tuple[tuple[re.Pattern[str], QTextCharFormat, int], ...] = ()

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        for pattern, fmt, group in self.rules:
            for match in pattern.finditer(text):
                start, end = match.span(group)
                if end > start:
                    self.setFormat(start, end - start, fmt)


_STR_RE = r'"(?:[^"\\]|\\.)*"'


class JsonHighlighter(_RuleHighlighter):
    rules = (
        (re.compile(r"[{}\[\],:]"), _fmt(_PUNCT), 0),
        (re.compile(r"-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b"), _fmt(_NUMBER), 0),
        (re.compile(r"\b(?:true|false|null)\b"), _fmt(_KEYWORD), 0),
        (re.compile(_STR_RE), _fmt(_STRING), 0),
        (re.compile(_STR_RE + r"(?=\s*:)"), _fmt(_KEY), 0),
    )


class YamlHighlighter(_RuleHighlighter):
    rules = (
        (re.compile(r"(?:^|\s)-(?=\s|$)"), _fmt(_PUNCT), 0),
        (re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w.])"), _fmt(_NUMBER), 0),
        (
            re.compile(r"(?<![\w-])(?:true|false|yes|no|on|off|null|True|False|Null|~)(?![\w-])"),
            _fmt(_KEYWORD),
            0,
        ),
        (re.compile(r"[&*][\w-]+"), _fmt(_SECTION), 0),
        (re.compile(r"!{1,2}[\w/.-]*"), _fmt(_SECTION), 0),
        (re.compile(_STR_RE + r"|'(?:[^']|'')*'"), _fmt(_STRING), 0),
        (
            re.compile(r"^\s*(?:-\s+)?([^\s#'\"\-][^:#]*?|\"[^\"]*\"|'[^']*')\s*:(?=\s|$)"),
            _fmt(_KEY),
            1,
        ),
        (re.compile(r"^(?:---|\.\.\.)\s*$"), _fmt(_SECTION, bold=True), 0),
        (re.compile(r"(?:^|(?<=\s))#.*$"), _fmt(_COMMENT, italic=True), 0),
    )


_TOML_NUMBER = (
    r"(?<![\w.])[+-]?(?:0x[0-9a-fA-F_]+|\d[\d_]*(?:\.[\d_]+)?(?:[eE][+-]?\d+)?|inf|nan)(?![\w.])"
)
_TOML_DATE = r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?"
_TOML_KEY_PART = r"(?:[\w-]+|\"[^\"]*\"|'[^']*')"
_TOML_KEY = rf"^\s*({_TOML_KEY_PART}(?:\s*\.\s*{_TOML_KEY_PART})*)\s*="


class TomlHighlighter(_RuleHighlighter):
    rules = (
        (re.compile(r"[=,\[\]{}]"), _fmt(_PUNCT), 0),
        (re.compile(_TOML_NUMBER), _fmt(_NUMBER), 0),
        (re.compile(_TOML_DATE), _fmt(_NUMBER), 0),
        (re.compile(r"\b(?:true|false)\b"), _fmt(_KEYWORD), 0),
        (re.compile(_STR_RE + r"|'[^']*'"), _fmt(_STRING), 0),
        (re.compile(_TOML_KEY), _fmt(_KEY), 1),
        (re.compile(r"^\s*\[\[?[^\]]*\]\]?"), _fmt(_SECTION, bold=True), 0),
        (re.compile(r"#.*$"), _fmt(_COMMENT, italic=True), 0),
    )


class _StatefulHighlighter(QSyntaxHighlighter):
    """Base for highlighters with a construct that spans lines."""

    def _span(self, text: str, start: int, end_marker: str, fmt: QTextCharFormat) -> int:
        """Format from ``start`` up to and including ``end_marker`` (or the end
        of the line, staying in the block state). Returns where it ended."""
        end = text.find(end_marker, start)
        if end < 0:
            self.setFormat(start, len(text) - start, fmt)
            return -1
        end += len(end_marker)
        self.setFormat(start, end - start, fmt)
        return end


class MarkupHighlighter(_StatefulHighlighter):
    """HTML and XML: tags, attributes, values, entities and comments."""

    _COMMENT_STATE = 1
    _tag = re.compile(r"<[!?/]?[\w:.-]+|/?>|\?>")
    _attr = re.compile(r"([\w:.-]+)\s*(=)\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)?")
    _entity = re.compile(r"&#?\w+;")

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self._f_tag = _fmt(_TAG)
        self._f_attr = _fmt(_KEY)
        self._f_value = _fmt(_STRING)
        self._f_punct = _fmt(_PUNCT)
        self._f_entity = _fmt(_SECTION)
        self._f_comment = _fmt(_COMMENT, italic=True)

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        self.setCurrentBlockState(0)
        pos = 0
        if self.previousBlockState() == self._COMMENT_STATE:
            pos = self._span(text, 0, "-->", self._f_comment)
            if pos < 0:
                self.setCurrentBlockState(self._COMMENT_STATE)
                return
        for match in self._entity.finditer(text, pos):
            self.setFormat(match.start(), match.end() - match.start(), self._f_entity)
        while True:
            lt = text.find("<", pos)
            if lt < 0:
                return
            if text.startswith("<!--", lt):
                pos = self._span(text, lt, "-->", self._f_comment)
                if pos < 0:
                    self.setCurrentBlockState(self._COMMENT_STATE)
                    return
                continue
            gt = text.find(">", lt)
            end = len(text) if gt < 0 else gt + 1
            inside = text[lt:end]
            name = self._tag.match(inside)
            if name is None:
                pos = lt + 1
                continue
            self.setFormat(lt, name.end(), self._f_tag)
            for attr in self._attr.finditer(inside, name.end()):
                self.setFormat(lt + attr.start(1), len(attr.group(1)), self._f_attr)
                self.setFormat(lt + attr.start(2), 1, self._f_punct)
                if attr.group(3):
                    self.setFormat(lt + attr.start(3), len(attr.group(3)), self._f_value)
            if gt >= 0:
                close = 2 if inside.endswith("/>") or inside.endswith("?>") else 1
                self.setFormat(end - close, close, self._f_tag)
            pos = end


class MarkdownHighlighter(_StatefulHighlighter):
    _FENCE_STATE = 1
    _rules = (
        (re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s"), _fmt(_PUNCT), 0),
        (re.compile(r"^\s{0,3}>.*$"), _fmt(_COMMENT, italic=True), 0),
        (re.compile(r"\*\*[^*]+\*\*|__[^_]+__"), _fmt(_KEY, bold=True), 0),
        (
            re.compile(r"(?<![*\w])\*[^*\s][^*]*\*(?!\*)|(?<![_\w])_[^_\s][^_]*_(?!_)"),
            _fmt(_KEY, italic=True),
            0,
        ),
        (re.compile(r"!?\[[^\]]*\]\([^)]*\)"), _fmt(_SECTION), 0),
        (re.compile(r"`[^`]+`"), _fmt(_STRING), 0),
        (re.compile(r"^\s{0,3}#{1,6}\s.*$"), _fmt(_HEADING, bold=True), 0),
        (re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$"), _fmt(_PUNCT), 0),
    )

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self._f_code = _fmt(_STRING)

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        fence = text.lstrip().startswith(("```", "~~~"))
        if self.previousBlockState() == self._FENCE_STATE:
            self.setFormat(0, len(text), self._f_code)
            self.setCurrentBlockState(0 if fence else self._FENCE_STATE)
            return
        if fence:
            self.setFormat(0, len(text), self._f_code)
            self.setCurrentBlockState(self._FENCE_STATE)
            return
        self.setCurrentBlockState(0)
        for pattern, fmt, group in self._rules:
            for match in pattern.finditer(text):
                start, end = match.span(group)
                self.setFormat(start, end - start, fmt)


class RainbowCsvHighlighter(QSyntaxHighlighter):
    """Every column its own colour. Quoted fields (with the delimiter or line
    breaks inside) are followed across lines through the block state."""

    def __init__(self, document: QTextDocument, delimiter: str = ",") -> None:
        super().__init__(document)
        self._delimiter = delimiter
        self._formats = [_fmt(color) for color in _RAINBOW]
        self._f_delim = _fmt(_PUNCT)

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        # State: -1 = a fresh record; otherwise 2 * column + 1 while a quoted
        # field continues on the next line.
        state = self.previousBlockState()
        quoted = state > 0 and state % 2 == 1
        column = (state - 1) // 2 if quoted else 0
        delimiter = self._delimiter
        formats = self._formats
        start = 0
        i = 0
        length = len(text)
        while i < length:
            char = text[i]
            if quoted:
                if char == '"':
                    if i + 1 < length and text[i + 1] == '"':
                        i += 2
                        continue
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == delimiter:
                self.setFormat(start, i - start, formats[column % len(formats)])
                self.setFormat(i, 1, self._f_delim)
                column += 1
                start = i + 1
            i += 1
        self.setFormat(start, length - start, formats[column % len(formats)])
        self.setCurrentBlockState(2 * column + 1 if quoted else -1)


def sniff_delimiter(text: str, name: str = "") -> str:
    """The CSV delimiter: tab for .tsv, else the candidate that splits the
    first lines most consistently (',' ';' tab '|')."""
    if name.lower().endswith(".tsv"):
        return "\t"
    lines = [line for line in text.splitlines()[:20] if line.strip()]
    best, best_score = ",", 0.0
    for candidate in (",", ";", "\t", "|"):
        counts = [line.count(candidate) for line in lines]
        if not counts or max(counts) == 0:
            continue
        common = max(set(counts), key=counts.count)
        score = counts.count(common) / len(counts) * (1 + min(common, 50) / 50)
        if common and score > best_score:
            best, best_score = candidate, score
    return best


def attach_highlighter(edit: QPlainTextEdit, name: str) -> QSyntaxHighlighter | None:
    """Colour ``edit`` for the file type of ``name`` (None for plain text or
    when the text is too large to highlight on the UI thread)."""
    kind = text_kind(name)
    document = edit.document()
    if not kind or document.characterCount() > HIGHLIGHT_LIMIT:
        return None
    if kind == "csv":
        return RainbowCsvHighlighter(document, sniff_delimiter(edit.toPlainText(), name))
    cls = {
        "json": JsonHighlighter,
        "yaml": YamlHighlighter,
        "toml": TomlHighlighter,
        "html": MarkupHighlighter,
        "xml": MarkupHighlighter,
        "markdown": MarkdownHighlighter,
    }[kind]
    return cls(document)


# -------------------------------------------------------------- rendered view
# Reads a file from the same archive, by a path relative to the previewed file
# ("css/site.css", "../img/logo.png"); None when it is not there or too costly.
ResourceReader = Callable[[str], "bytes | None"]


class OfflineBrowser(QTextBrowser):
    """A QTextBrowser that never loads anything from outside the archive (no
    files on disk, no network) and does not follow links; in-page anchors
    still scroll. Stylesheets and images next to the page in the same archive
    are read from it, in memory, when a ``resources`` reader is given."""

    def __init__(self, resources: ResourceReader | None = None, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._resources = resources
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._on_anchor)

    def loadResource(self, kind: int, url: QUrl) -> object:  # noqa: N802 - Qt override
        if url.scheme() == "data":
            return super().loadResource(kind, url)
        if self._resources is None or url.scheme() or url.isEmpty():
            return None  # http(s), file:, ...: never
        data = self._resources(url.path())
        if data is None:
            return None
        if kind == QTextDocument.ResourceType.ImageResource.value:
            from archy.services.image_decode import decode_image

            image = decode_image(data)
            return None if image.isNull() else image
        if kind == QTextDocument.ResourceType.StyleSheetResource.value:
            return data.decode("utf-8", errors="replace")
        return None

    def _on_anchor(self, url: QUrl) -> None:
        if url.hasFragment() and not url.scheme() and not url.path():
            self.scrollToAnchor(url.fragment())


_LINK_TAG = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r"""([\w-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""")


def inline_stylesheets(html: str, resources: ResourceReader | None) -> str:
    """Put the page's linked stylesheets (from the archive) inline as <style>.
    Qt only loads a <link> that says type="text/css" and sits in <head>; most
    pages leave the type out. Links that cannot be read are dropped."""

    def replace(match: re.Match[str]) -> str:
        attrs = {k.lower(): v.strip("\"'") for k, v in _ATTR.findall(match.group(0))}
        if "stylesheet" not in attrs.get("rel", "").lower().split():
            return match.group(0)
        data = resources(attrs.get("href", "")) if resources is not None else None
        if not data:
            return ""
        css = data.decode("utf-8", errors="replace").replace("</", "<\\/")
        return f"<style>{css}</style>"

    return _LINK_TAG.sub(replace, html)


def _make_page(kind: str, name: str, resources: ResourceReader | None) -> QWidget:
    """The rendered view: HTML through Chromium when QtWebEngine is there
    (all of CSS), else, and for Markdown, Qt's own rich text."""
    if kind == "html":
        from archy.services import web_view

        if web_view.available():
            return web_view.make_web_view(name, resources)
    return _make_browser(kind, resources)


def _make_browser(kind: str, resources: ResourceReader | None) -> OfflineBrowser:
    browser = OfflineBrowser(resources)
    browser.setObjectName("htmlPage" if kind == "html" else "markdownPage")
    browser.document().setDocumentMargin(22)
    browser.setFrameShape(QFrame.Shape.NoFrame)
    if kind == "markdown":
        from archy.ui import theme

        palette = browser.palette()
        palette.setColor(QPalette.ColorRole.Link, QColor(theme.ACCENT))
        browser.setPalette(palette)
    return browser


# --------------------------------------------------------------- CSV as table
class CsvModel(QAbstractTableModel):
    """The rows of a CSV text: the first row is the header; every column in its
    rainbow colour, like the source view."""

    MAX_ROWS = 200_000

    def __init__(self, text: str, delimiter: str) -> None:
        super().__init__()
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows: list[list[str]] = []
        try:
            for row in reader:
                rows.append(row)
                if len(rows) > self.MAX_ROWS:
                    break
        except csv.Error:
            pass  # a damaged tail (or a cut-off preview): show what parsed
        self._header = rows[0] if rows else []
        self._rows = rows[1:]
        self._columns = max((len(r) for r in rows), default=0)
        self._colors = [QColor(c) for c in _RAINBOW]

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, B008, N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, B008, N802
        return 0 if parent.isValid() else self._columns

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001, ANN201
        if not index.isValid():
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            row = self._rows[index.row()]
            return row[index.column()] if index.column() < len(row) else ""
        if role == Qt.ItemDataRole.ForegroundRole:
            return self._colors[index.column() % len(self._colors)]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001, ANN201, N802
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self._header[section] if section < len(self._header) else ""
            if role == Qt.ItemDataRole.ForegroundRole:
                return self._colors[section % len(self._colors)]
        elif role == Qt.ItemDataRole.DisplayRole:
            return str(section + 1)
        return None


def _make_table() -> QTableView:
    table = QTableView()
    table.setObjectName("csvTable")
    table.setFont(_monospace())
    table.setWordWrap(False)
    table.setAlternatingRowColors(True)
    table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
    table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setHighlightSections(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.verticalHeader().setDefaultSectionSize(22)
    return table


def _monospace() -> QFont:
    from archy.services.providers import text_font

    return text_font()


# ------------------------------------------------------------------ find bar
class _TextTarget:
    """Find in a QPlainTextEdit or QTextEdit (the source or a rendered page)."""

    MAX_MATCHES = 10_000

    def __init__(self, view: QPlainTextEdit | QTextEdit) -> None:
        self._view = view
        self._matches: list[QTextCursor] = []
        self._all = QTextCharFormat()
        self._all.setBackground(QColor(242, 197, 92, 90))
        self._current = QTextCharFormat()
        self._current.setBackground(QColor(242, 197, 92, 220))
        self._current.setForeground(QColor("#111111"))

    def search(self, text: str) -> int:
        self._matches = []
        if text:
            document = self._view.document()
            cursor = QTextCursor(document)
            while len(self._matches) < self.MAX_MATCHES:
                cursor = document.find(text, cursor)
                if cursor.isNull():
                    break
                self._matches.append(QTextCursor(cursor))
        self._paint(-1)
        return len(self._matches)

    def first_after_cursor(self) -> int:
        position = self._view.textCursor().selectionStart()
        for i, match in enumerate(self._matches):
            if match.selectionStart() >= position:
                return i
        return 0

    def go(self, index: int) -> None:
        if not self._matches:
            return
        cursor = self._matches[index]
        self._view.setTextCursor(cursor)
        self._view.ensureCursorVisible()
        self._paint(index)

    def _paint(self, current: int) -> None:
        selections = []
        for i, match in enumerate(self._matches):
            selection = QTextEdit.ExtraSelection()
            selection.cursor = match
            selection.format = self._current if i == current else self._all
            selections.append(selection)
        self._view.setExtraSelections(selections)

    def clear(self) -> None:
        self._matches = []
        self._view.setExtraSelections([])


class _TableTarget:
    """Find in the CSV table: every matching cell selected, one current."""

    MAX_MATCHES = 10_000

    def __init__(self, table: QTableView) -> None:
        self._table = table
        self._matches: list[QModelIndex] = []

    def search(self, text: str) -> int:
        self._matches = []
        model = self._table.model()
        needle = text.casefold()
        if needle and model is not None:
            for row in range(model.rowCount()):
                for column in range(model.columnCount()):
                    index = model.index(row, column)
                    if needle in str(model.data(index) or "").casefold():
                        self._matches.append(index)
                        if len(self._matches) >= self.MAX_MATCHES:
                            break
                if len(self._matches) >= self.MAX_MATCHES:
                    break
        selection = QItemSelection()
        for index in self._matches:
            selection.select(index, index)
        selection_model = self._table.selectionModel()
        if selection_model is not None:
            selection_model.select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        return len(self._matches)

    def first_after_cursor(self) -> int:
        return 0

    def go(self, index: int) -> None:
        if self._matches:
            target = self._matches[index]
            self._table.selectionModel().setCurrentIndex(
                target, QItemSelectionModel.SelectionFlag.NoUpdate
            )
            self._table.scrollTo(target)

    def clear(self) -> None:
        self._matches = []
        self._table.clearSelection()


class FindBar(QFrame):
    """Find in the shown file (Ctrl+F / Cmd+F while the preview has focus):
    every match highlighted, Enter / Shift+Enter for the next / previous one,
    Esc to close."""

    def __init__(self, target_of: Callable[[], object], parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        from archy.ui import theme

        self.setObjectName("findBar")
        self._target_of = target_of
        self._target = None
        self._count = 0
        self._index = -1
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 6, 4)
        row.setSpacing(4)
        icon = QLabel()
        icon.setPixmap(theme.symbol_icon("search", 14, theme.TEXT_SECONDARY).pixmap(14, 14))
        self.field = QLineEdit()
        self.field.setObjectName("findField")
        self.field.setPlaceholderText(tr("find.placeholder"))
        self.field.setClearButtonEnabled(True)
        self.counter = QLabel()
        self.counter.setObjectName("findCount")
        row.addWidget(icon)
        row.addWidget(self.field, stretch=1)
        row.addWidget(self.counter)
        for name, tip, step in (
            ("chev_left", "find.previous", -1),
            ("chev_right", "find.next", 1),
        ):
            button = QToolButton()
            button.setObjectName("findButton")
            button.setIcon(theme.symbol_icon(name, 14, theme.TEXT_SECONDARY))
            button.setToolTip(tr(tip))
            button.clicked.connect(lambda _c=False, s=step: self.step(s))
            row.addWidget(button)
        close = QToolButton()
        close.setObjectName("findButton")
        close.setIcon(theme.symbol_icon("x", 14, theme.TEXT_SECONDARY))
        close.setToolTip(tr("find.close"))
        close.clicked.connect(self.close_bar)
        row.addWidget(close)
        self.field.textChanged.connect(lambda _t: self.refresh())
        self.field.installEventFilter(self)
        self.hide()

    def open_bar(self, initial: str = "") -> None:
        if initial and "\n" not in initial and " " not in initial:
            self.field.setText(initial)
        self.show()
        self.field.setFocus()
        self.field.selectAll()
        self.refresh()

    def close_bar(self) -> None:
        if self._target is not None:
            self._target.clear()
        self.hide()
        view = getattr(self._target_of(), "_view", None) or getattr(
            self._target_of(), "_table", None
        )
        if view is not None:
            view.setFocus()

    def refresh(self) -> None:
        """(Re)run the search on the current page (the text may have changed,
        or the user switched between rendered and source)."""
        if not self.isVisible():
            return
        if self._target is not None and self._target is not self._target_of():
            self._target.clear()
        self._target = self._target_of()
        if getattr(self._target, "asynchronous", False):
            # The page searches by itself and reports back (WebFindTarget).
            self._target.on_result = self._on_result
        self._count = self._target.search(self.field.text())
        self._index = self._target.first_after_cursor() if self._count else -1
        if self._count:
            self._target.go(self._index)
        self._update_counter()

    def _on_result(self, count: int, index: int) -> None:
        self._count = count
        self._index = index if count else -1
        self._update_counter()

    def step(self, direction: int) -> None:
        if self._target is not None and getattr(self._target, "asynchronous", False):
            self._target.step(direction)
            return
        if not self._count or self._target is None:
            return
        self._index = (self._index + direction) % self._count
        self._target.go(self._index)
        self._update_counter()

    def _update_counter(self) -> None:
        if not self.field.text():
            self.counter.setText("")
        elif self._count:
            self.counter.setText(f"{self._index + 1}/{self._count}")
        else:
            self.counter.setText(tr("find.none"))

    def eventFilter(self, watched, event) -> bool:  # noqa: ANN001, N802 - Qt override
        if watched is self.field and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                back = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self.step(-1 if back else 1)
                return True
            if key == Qt.Key.Key_Escape:
                self.close_bar()
                return True
        return super().eventFilter(watched, event)


# ------------------------------------------------------------- the preview
class TextPreview(QWidget):
    """A text file in the preview panel: the (highlighted) text, for HTML,
    Markdown and CSV also a rendered page or table with a switch to the text,
    and a find bar for the file itself."""

    def __init__(
        self,
        name: str,
        edit: QPlainTextEdit,
        resources: ResourceReader | None = None,
    ) -> None:
        super().__init__()
        self.edit = edit
        self.kind = text_kind(name)
        self._name = name
        self._resources = resources
        self._rendered_text: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.pages = QStackedWidget()
        self.rendered: QWidget | None = None
        self._buttons: list[QToolButton] = []
        if self.kind in RENDERABLE:
            self._build_switch(layout)
            if self.kind == "csv":
                self.rendered = _make_table()
            else:
                # Prose and markup are written in long lines: wrap the source.
                edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
                self.rendered = _make_page(self.kind, name, resources)
            self.pages.addWidget(self.rendered)
        self.pages.addWidget(edit)
        layout.addWidget(self.pages, stretch=1)

        self.find_bar = FindBar(self._find_target)
        layout.addWidget(self.find_bar)
        self._text_target = _TextTarget(edit)
        self._rendered_target: object | None = None
        if self.kind in RENDERABLE:
            self.show_source(_show_source[self.kind])

    def _build_switch(self, layout: QVBoxLayout) -> None:
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        switch = QFrame()
        switch.setObjectName("viewSwitch")
        switch.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(switch)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        group = QButtonGroup(self)
        labels = ("preview.table" if self.kind == "csv" else "preview.rendered", "preview.source")
        for i, label in enumerate(labels):
            button = QToolButton()
            button.setObjectName("viewButton")
            button.setCheckable(True)
            button.setText(tr(label))
            button.clicked.connect(lambda _c=False, source=bool(i): self.show_source(source))
            group.addButton(button)
            row.addWidget(button)
            self._buttons.append(button)
        bar.addStretch(1)
        bar.addWidget(switch)
        layout.addLayout(bar)

    def show_source(self, source: bool) -> None:
        if self.rendered is None:
            return
        _show_source[self.kind] = source
        if not source:
            current = self.edit.toPlainText()
            if current != self._rendered_text:  # first time, or edited since
                self._render(current)
                self._rendered_text = current
        self.pages.setCurrentIndex(1 if source else 0)
        self._buttons[1 if source else 0].setChecked(True)
        self.find_bar.refresh()

    def _render(self, text: str) -> None:
        if isinstance(self.rendered, QTableView):
            self.rendered.setModel(CsvModel(text, sniff_delimiter(text, self._name)))
            self.rendered.resizeColumnsToContents()
            header = self.rendered.horizontalHeader()
            for column in range(header.count()):
                header.resizeSection(column, min(header.sectionSize(column), 360))
            self._rendered_target = _TableTarget(self.rendered)
        elif self.kind == "markdown":
            self.rendered.setMarkdown(text)
            self._rendered_target = _TextTarget(self.rendered)
        elif hasattr(self.rendered, "set_html"):  # Chromium (services/web_view.py)
            from archy.services.web_view import WebFindTarget

            self.rendered.set_html(text)
            self._rendered_target = WebFindTarget(self.rendered)
        else:
            self.rendered.setHtml(inline_stylesheets(text, self._resources))
            self._rendered_target = _TextTarget(self.rendered)

    def _find_target(self) -> object:
        if self.rendered is not None and self.pages.currentWidget() is self.rendered:
            return self._rendered_target
        return self._text_target

    def open_find(self) -> None:
        """Ctrl+F / Cmd+F: find in this file, starting from the selected text."""
        view = self.pages.currentWidget()
        initial = view.textCursor().selectedText() if hasattr(view, "textCursor") else ""
        self.find_bar.open_bar(initial)


def make_text_view(
    name: str,
    text: str,
    *,
    editable: bool,
    make_edit: Callable[..., QPlainTextEdit],
    resources: ResourceReader | None = None,
) -> tuple[QWidget, QPlainTextEdit]:
    """The text preview for ``name`` (see TextPreview). Returns (widget to
    show, the text field); the text field is what the window edits and saves,
    the rendered page or table follows it when switched to."""
    edit = make_edit(text, editable=editable)
    edit._archy_highlighter = attach_highlighter(edit, name)  # type: ignore[attr-defined]
    preview = TextPreview(name, edit, resources)
    return preview, edit
