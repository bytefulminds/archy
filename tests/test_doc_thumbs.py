"""Thumbnails of documents (services/doc_thumbs.py): the top of a PDF's first
page, a rendered HTML/Markdown page, the first lines of a text file. Made on
worker threads, so they are made on one here too."""

from __future__ import annotations

import threading

import pytest

try:
    from PySide6.QtWidgets import QApplication as _QApplication  # noqa: F401
except Exception as _exc:  # noqa: BLE001 - missing Qt libs -> skip, don't fail
    pytest.skip(f"PySide6 GUI not available: {_exc}", allow_module_level=True)

from PySide6.QtGui import QColor, QImage  # noqa: E402

from archy.services import doc_thumbs  # noqa: E402


@pytest.fixture(autouse=True)
def _app():  # noqa: ANN202
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _pdf() -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QFont, QPageSize, QPainter, QPdfWriter

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QPdfWriter(buffer)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    painter = QPainter(writer)
    font = QFont()
    font.setPointSize(40)
    painter.setFont(font)
    painter.drawText(300, 900, "Invoice")
    painter.end()
    buffer.close()
    return bytes(data)


def _on_worker(make):  # noqa: ANN001, ANN202
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("image", make()))
    thread.start()
    thread.join(60)
    return result["image"]


def _dark_pixels(image: QImage) -> int:
    return sum(
        1
        for x in range(0, image.width(), 2)
        for y in range(0, image.height(), 2)
        if QColor(image.pixel(x, y)).lightness() < 128
    )


def test_pdf_thumbnail_shows_the_top_of_the_first_page() -> None:
    image = _on_worker(lambda: doc_thumbs.pdf_thumbnail(_pdf()))
    assert (image.width(), image.height()) == (doc_thumbs.WIDTH, doc_thumbs.HEIGHT)
    assert QColor(image.pixel(2, 2)).lightness() > 240  # white paper
    assert _dark_pixels(image) > 10  # the title is on it
    assert doc_thumbs.pdf_thumbnail(b"not a pdf").isNull()


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("index.html", "<html><body><h1>Welcome</h1><p>Some text</p></body></html>"),
        ("README.md", "# Archy\n\nLook inside archives.\n"),
    ],
)
def test_html_and_markdown_render_as_a_page_with_dark_ink(name: str, text: str) -> None:
    image = _on_worker(lambda: doc_thumbs.text_thumbnail(text.encode(), name))
    assert QColor(image.pixel(image.width() - 2, image.height() - 2)).lightness() > 240
    # Dark text on the light page, whatever the (dark) application palette.
    assert _dark_pixels(image) > 10


def test_text_shows_its_first_lines_and_binary_none() -> None:
    image = _on_worker(lambda: doc_thumbs.text_thumbnail(b"line one\nline two\n", "a.txt"))
    assert not image.isNull()
    assert QColor(image.pixel(image.width() - 2, image.height() - 2)).lightness() < 60
    assert doc_thumbs.text_thumbnail(b"\x00\x01\x02\x00binary", "a.txt").isNull()


def test_a_page_never_loads_what_it_refers_to() -> None:
    document = doc_thumbs._OfflineDocument()
    document.setHtml('<img src="file:///etc/hostname"><link rel="stylesheet" href="x.css">')
    assert document.loadResource(0, None) is None
