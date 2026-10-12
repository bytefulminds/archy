from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, QSize, Qt
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QFontDatabase,
    QImage,
    QPainter,
    QPalette,
    QTextDocument,
)

from archy.services.text_decode import decode_text, looks_binary

# Thumbnails of documents, so a folder in a thumbnail view shows at a glance
# which files Archy can preview: the first page of a PDF, a rendered HTML or
# Markdown page, the first lines of a text or code file. Made on the
# thumbnail loader's worker threads (QImage and QPainter, no widgets), from the
# bytes in memory like every other thumbnail.
#
# The picture is landscape (WIDTH x HEIGHT), the shape of the tiles' picture
# area, and shows the top of the document: the tiles crop the middle of a
# thumbnail, which for a page would be its least telling part.

WIDTH, HEIGHT = 240, 160
TEXT_BYTES = 16 * 1024  # enough for the first lines (and an HTML page's start)
MAX_PDF_BYTES = 64 * 1024 * 1024  # a PDF is read whole: larger ones stay an icon

_RENDERED = {".html": "html", ".htm": "html", ".xhtml": "html", ".md": "markdown"}
_RENDERED[".markdown"] = "markdown"

_PAPER = QColor("#F6F7F9")
_INK = QColor("#2B3138")
_EDITOR = QColor("#1E1E1E")  # the code colours of the text preview (VS Code Dark+)
_EDITOR_TEXT = QColor("#C8CDD2")
_EDITOR_DIM = QColor("#6E7681")


def _extension(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot != -1 else ""


def is_pdf(name: str) -> bool:
    return _extension(name) == ".pdf"


def text_thumbnail(data: bytes, name: str) -> QImage:
    """A rendered page for HTML/Markdown, else the first lines of the text on
    a dark editor background. A null image for binary data."""
    head = data[:TEXT_BYTES]
    if not head or looks_binary(head):
        return QImage()
    text = decode_text(head, limit=TEXT_BYTES).text
    kind = _RENDERED.get(_extension(name))
    if kind is not None:
        return _rendered_page(text, kind)
    return _text_lines(text)


class _OfflineDocument(QTextDocument):
    """Never loads anything a page refers to (images, stylesheets): a
    thumbnail shows the page's own text and layout only."""

    def loadResource(self, _type, _name):  # noqa: ANN001, ANN201, N802 - Qt override
        return None


def _rendered_page(text: str, kind: str) -> QImage:
    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGB32)
    image.fill(_PAPER)
    document = _OfflineDocument()
    document.setDocumentMargin(14)
    font = QFont()
    font.setPixelSize(13)
    document.setDefaultFont(font)
    document.setDefaultStyleSheet(f"body {{ color: {_INK.name()}; }}")
    if kind == "html":
        document.setHtml(text)
    else:
        document.setMarkdown(text)
    # Laid out at twice the width and drawn at half size: a zoomed-out page.
    document.setTextWidth(WIDTH * 2)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.scale(0.5, 0.5)
    # Dark ink whatever the application palette (Archy's is dark: its light
    # text colour would vanish on the page).
    context = QAbstractTextDocumentLayout.PaintContext()
    context.palette.setColor(QPalette.ColorRole.Text, _INK)
    context.clip = QRectF(0, 0, WIDTH * 2, HEIGHT * 2)
    painter.setClipRect(context.clip)
    document.documentLayout().draw(painter, context)
    painter.end()
    return image


def _text_lines(text: str) -> QImage:
    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGB32)
    image.fill(_EDITOR)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setFamily("JetBrains Mono")  # bundled; the fixed system font otherwise
    font.setPixelSize(9)
    painter.setFont(font)
    line_height = 12
    top, left = 8, 10
    rows = (HEIGHT - top) // line_height
    lines = text.expandtabs(4).splitlines()[:rows]
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        dim = stripped.startswith(("#", "//", "--", ";", "<!--", "/*", "*"))
        painter.setPen(_EDITOR_DIM if dim else _EDITOR_TEXT)
        painter.drawText(left, top + (i + 1) * line_height - 3, line[:60])
    painter.end()
    return image


def office_thumbnail(data: bytes, name: str) -> QImage:
    """The preview picture Office saved in the file when there is one (a
    PowerPoint's first slide, usually), else a small page of its text: a
    Word document as a page, a sheet's first rows, a deck's slide text."""
    from archy.services import office

    picture = office.embedded_thumbnail(data)
    if picture is not None:
        from archy.services.image_decode import decode_image

        image = decode_image(picture, QSize(WIDTH * 2, WIDTH * 2))
        if not image.isNull() and not _blank(image):
            return _on_white(image.scaledToWidth(WIDTH, Qt.TransformationMode.SmoothTransformation))
    try:
        kind = office.kind_of(name)
        if kind == "word":
            return _rendered_page(office.word_page(data).body, "html")
        if kind == "powerpoint":
            return _slide_text(office.powerpoint_page(data).body)
        if kind == "excel":
            sheets = office.excel_sheets(data)
            return _sheet_grid(sheets[0].rows[:14] if sheets else [])
    except office.OfficeError:
        return QImage()
    return QImage()


def _blank(image: QImage) -> bool:
    """A preview picture of one colour (an empty first slide or page)."""
    small = image.scaled(16, 16)
    first = small.pixel(0, 0)
    return all(small.pixel(x, y) == first for x in range(16) for y in range(16))


def _slide_text(body: str) -> QImage:
    """The first slide's text on a white 16:9 card."""
    return _rendered_page(body or "", "html")


def _sheet_grid(rows: list[list[str]]) -> QImage:
    """The top left of a sheet: cells on a light grid, the first row bold."""
    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGB32)
    image.fill(QColor("#FFFFFF"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    font = QFont()
    font.setPixelSize(9)
    cell_w, cell_h = 52, 13
    painter.setPen(QColor("#D5D9DE"))
    for x in range(0, WIDTH, cell_w):
        painter.drawLine(x, 0, x, HEIGHT)
    for y in range(0, HEIGHT, cell_h):
        painter.drawLine(0, y, WIDTH, y)
    painter.setPen(_INK)
    for r, row in enumerate(rows[: HEIGHT // cell_h]):
        font.setBold(r == 0)
        painter.setFont(font)
        for c, cell in enumerate(row[: WIDTH // cell_w + 1]):
            rect = QRectF(c * cell_w + 3, r * cell_h, cell_w - 5, cell_h)
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, cell)
    painter.end()
    return image


def _on_white(picture: QImage) -> QImage:
    """The top of ``picture`` on a white thumbnail."""
    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    top = max(0, (HEIGHT - picture.height()) // 2)
    painter.drawImage(0, top, picture)
    painter.end()
    return image


def pdf_thumbnail(data: bytes) -> QImage:
    """The top of a PDF's first page; a null image when it cannot be read
    (damaged, or locked with a password)."""
    from PySide6.QtPdf import QPdfDocument

    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    document = QPdfDocument()
    document.load(buffer)
    try:
        if document.status() != QPdfDocument.Status.Ready or document.pageCount() < 1:
            return QImage()
        page = document.pagePointSize(0)
        if page.width() <= 0 or page.height() <= 0:
            return QImage()
        height = max(HEIGHT, round(WIDTH * page.height() / page.width()))
        rendered = document.render(0, QSize(WIDTH, height))
    finally:
        document.close()
        buffer.close()
    if rendered.isNull():
        return QImage()
    # PDF pages are usually transparent where nothing is drawn: put them on white.
    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.drawImage(0, 0, rendered)  # the top of the page
    painter.end()
    return image
