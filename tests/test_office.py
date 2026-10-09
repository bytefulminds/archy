"""Previews of Word, Excel and PowerPoint files (services/office.py)."""

from __future__ import annotations

import datetime
import io
import zipfile

import pytest

docx = pytest.importorskip("docx")
openpyxl = pytest.importorskip("openpyxl")
pptx = pytest.importorskip("pptx")

from archy.services import office  # noqa: E402
from archy.services.preview import PreviewKind, kind_for_name  # noqa: E402


def _png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), (40, 140, 200)).save(buffer, "PNG")
    return buffer.getvalue()


def _word() -> bytes:
    document = docx.Document()
    document.add_heading("Verslag 2024", 0)
    paragraph = document.add_paragraph("Dit is ")
    paragraph.add_run("vet").bold = True
    document.add_paragraph("punt een", style="List Bullet")
    table = document.add_table(rows=2, cols=2)
    table.cell(1, 1).text = "<script>"
    document.add_picture(io.BytesIO(_png()))
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _excel() -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Budget"
    sheet.append(["Post", "Bedrag", "Datum", "Ok"])
    sheet.append(["Huur", 950.5, datetime.date(2024, 1, 1), True])
    sheet.append([None, 1 / 3])
    book.create_sheet("Leeg")
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _powerpoint() -> bytes:
    from pptx.util import Inches

    deck = pptx.Presentation()
    first = deck.slides.add_slide(deck.slide_layouts[0])
    first.shapes.title.text = "Hallo Archy"
    second = deck.slides.add_slide(deck.slide_layouts[1])
    second.shapes.title.text = "Punten"
    second.placeholders[1].text_frame.text = "Eerste & meer"
    second.shapes.add_picture(io.BytesIO(_png()), Inches(6), Inches(4), Inches(2))
    out = io.BytesIO()
    deck.save(out)
    return out.getvalue()


def test_office_files_are_previewable() -> None:
    for name in ("a.docx", "b.XLSX", "c.pptx", "d.docm", "e.xlsm", "f.pptm"):
        assert kind_for_name(name) is PreviewKind.OFFICE
    assert kind_for_name("old.doc") is PreviewKind.UNSUPPORTED
    assert office.kind_of("x.docx") == "word"
    assert office.kind_of("x.xlsx") == "excel"
    assert office.kind_of("x.pptx") == "powerpoint"


def test_word_becomes_a_page_with_its_pictures() -> None:
    page = office.word_page(_word())
    assert "<h1" in page.html and "Verslag 2024" in page.html
    assert "<strong>vet</strong>" in page.html
    assert "<li>punt een</li>" in page.html
    assert "&lt;script&gt;" in page.html and "<script>" not in page.body
    assert list(page.media) == ["media/image1.png"]
    assert 'src="media/image1.png"' in page.html
    assert page.reader()("media/image1.png") == _png()
    assert page.reader()("../../etc/passwd") is None


def test_excel_sheets_hold_the_values() -> None:
    sheets = office.excel_sheets(_excel())
    assert [s.title for s in sheets] == ["Budget", "Leeg"]
    budget = sheets[0]
    assert budget.rows[0] == ["Post", "Bedrag", "Datum", "Ok"]
    assert budget.rows[1] == ["Huur", "950.5", "2024-01-01", "TRUE"]
    assert budget.rows[2] == ["", "0.3333333333"]
    assert budget.columns == 4 and not budget.more_rows
    assert sheets[1].rows == []
    assert [office.column_name(i) for i in (0, 25, 26, 701)] == ["A", "Z", "AA", "ZZ"]


def test_a_big_sheet_is_cut_off(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(office, "MAX_ROWS", 2)
    sheet = office.excel_sheets(_excel())[0]
    assert len(sheet.rows) == 2 and sheet.more_rows


def test_powerpoint_becomes_slide_cards() -> None:
    page = office.powerpoint_page(_powerpoint())
    assert page.html.count('class="slide"') == 2
    assert "Hallo Archy" in page.html and "Eerste &amp; meer" in page.html
    assert list(page.media) == ["media/slide2-1.png"]
    assert "Hallo Archy" in page.body and "Punten" not in page.body  # the first slide


def test_embedded_thumbnail_and_bad_files() -> None:
    assert office.embedded_thumbnail(_powerpoint()) is not None  # python-pptx's template
    assert office.embedded_thumbnail(b"not a zip") is None
    with pytest.raises(office.OfficeError):
        office.word_page(b"not a zip at all")
    protected = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(100)
    for read in (office.word_page, office.excel_sheets, office.powerpoint_page):
        with pytest.raises(office.OfficeError, match="encrypted"):
            read(protected)
    damaged = io.BytesIO()
    with zipfile.ZipFile(damaged, "w") as archive:
        archive.writestr("word/document.xml", "<broken")
    with pytest.raises(office.OfficeError):
        office.word_page(damaged.getvalue())


# ------------------------------------------------------------------- widgets
@pytest.fixture
def app():  # noqa: ANN201
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:  # noqa: BLE001 - no Qt GUI libs: skip
        pytest.skip(f"PySide6 GUI not available: {exc}")
    return QApplication.instance() or QApplication([])


def test_preview_widgets(app) -> None:  # noqa: ANN001
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QTableView, QTabWidget

    from archy.services.providers import build_preview_widget

    sheets = build_preview_widget("budget.xlsx", _excel())
    assert isinstance(sheets, QTabWidget) and sheets.count() == 2
    table = sheets.widget(0).findChild(QTableView)
    assert table.model().index(1, 1).data() == "950.5"
    assert table.model().headerData(0, Qt.Orientation.Horizontal) == "A"
    for name, data in (("a.docx", _word()), ("a.pptx", _powerpoint())):
        widget = build_preview_widget(name, data)
        assert not isinstance(widget, QLabel), name  # a page, not a message
    message = build_preview_widget("broken.docx", b"nonsense")
    assert isinstance(message, QLabel) and "broken.docx" in message.text()


def test_thumbnails(app) -> None:  # noqa: ANN001
    from archy.services import doc_thumbs

    for name, data in (("a.docx", _word()), ("a.xlsx", _excel()), ("a.pptx", _powerpoint())):
        image = doc_thumbs.office_thumbnail(data, name)
        assert not image.isNull(), name
        assert (image.width(), image.height()) == (doc_thumbs.WIDTH, doc_thumbs.HEIGHT)
    assert doc_thumbs.office_thumbnail(b"nonsense", "x.docx").isNull()
