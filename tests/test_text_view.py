"""Text previews: syntax colours, rainbow CSV, rendered HTML/Markdown with a
source switch, and the video seek bar and thumbnail orientation."""

from __future__ import annotations

import shutil
import subprocess

import pytest

# Skip the module when the Qt GUI stack cannot load (a CI image without the
# system libraries Qt needs). importorskip only skips a missing module, not a
# missing system library underneath it, so import explicitly.
try:
    from PySide6.QtWidgets import QApplication as _QApplication  # noqa: F401
except Exception as _exc:  # noqa: BLE001 - missing Qt libs -> skip, don't fail
    pytest.skip(f"PySide6 GUI not available: {_exc}", allow_module_level=True)

from PySide6.QtCore import QPoint, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QImage, QTextDocument  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QPlainTextEdit, QStackedWidget, QTextBrowser  # noqa: E402

from archy.services import text_view  # noqa: E402
from archy.services.providers import make_text_preview  # noqa: E402


@pytest.fixture(autouse=True)
def _app():  # noqa: ANN202
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    text_view._show_source.update({"html": False, "markdown": False, "csv": False})
    return app


def _color(edit: QPlainTextEdit, line: int, column: int) -> str | None:
    """The foreground colour the highlighter gave a character, or None."""
    block = edit.document().findBlockByNumber(line)
    for fmt in block.layout().formats():
        if fmt.start <= column < fmt.start + fmt.length:
            return fmt.format.foreground().color().name().upper()
    return None


def _view(name: str, text: str, *, editable: bool = False):  # noqa: ANN202
    from PySide6.QtWidgets import QApplication

    widget, edit = make_text_preview(name, text, editable=editable)
    QApplication.processEvents()  # QSyntaxHighlighter colours on the next event loop turn
    return widget, edit


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("a.json", "json"),
        ("b.YML", "yaml"),
        ("c.toml", "toml"),
        ("d.tsv", "csv"),
        ("e.htm", "html"),
        ("f.md", "markdown"),
        ("g.txt", ""),
    ],
)
def test_text_kinds(name: str, kind: str) -> None:
    assert text_view.text_kind(name) == kind


def test_json_keys_and_values_have_their_own_colours() -> None:
    _w, edit = _view("x.json", '{"name": "Archy", "size": 12, "ok": true, "none": null}')
    assert _color(edit, 0, 2) == text_view._KEY.upper()  # "name"
    assert _color(edit, 0, 11) == text_view._STRING.upper()  # "Archy"
    assert _color(edit, 0, 26) == text_view._NUMBER.upper()  # 12
    assert _color(edit, 0, 38) == text_view._KEYWORD.upper()  # true


def test_yaml_and_toml() -> None:
    _w, yaml = _view("x.yaml", "name: Archy  # comment\nitems:\n  - 3\n")
    assert _color(yaml, 0, 0) == text_view._KEY.upper()
    assert _color(yaml, 0, 15) == text_view._COMMENT.upper()
    assert _color(yaml, 2, 4) == text_view._NUMBER.upper()
    _w, toml = _view("x.toml", '[tool.archy]\nname = "Archy"\nfast = true\n')
    assert _color(toml, 0, 2) == text_view._SECTION.upper()
    assert _color(toml, 1, 0) == text_view._KEY.upper()
    assert _color(toml, 1, 9) == text_view._STRING.upper()
    assert _color(toml, 2, 8) == text_view._KEYWORD.upper()


def test_rainbow_csv_follows_quotes_and_line_breaks() -> None:
    text = 'name;city;note\nMark;Wijchen;"has; a\nline break";x\nCinzia;Nijmegen;plain\n'
    assert text_view.sniff_delimiter(text) == ";"
    _w, edit = _view("x.csv", text)
    first, second, third = (c.upper() for c in text_view._RAINBOW[:3])
    assert _color(edit, 0, 0) == first and _color(edit, 0, 5) == second
    assert _color(edit, 1, 14) == third  # "has; a - the ; inside quotes is no column
    assert _color(edit, 2, 0) == third  # the quoted field continues on the next line
    assert _color(edit, 2, 12) == text_view._RAINBOW[3].upper()  # x: column 4
    assert _color(edit, 3, 7) == second  # a new record starts at column 1 again


def test_delimiter_sniffing() -> None:
    assert text_view.sniff_delimiter("a,b,c\n1,2,3\n") == ","
    assert text_view.sniff_delimiter("a\tb\n1\t2\n") == "\t"
    assert text_view.sniff_delimiter("a,b\n1,2\n", "data.tsv") == "\t"


def test_markup_and_markdown_source_colours() -> None:
    _w, html = _view("x.html", '<a href="x.html">link</a> <!-- one\ntwo -->')
    text_view._show_source["html"] = True
    assert _color(html, 0, 1) == text_view._TAG.upper()
    assert _color(html, 0, 3) == text_view._KEY.upper()  # href
    assert _color(html, 0, 8) == text_view._STRING.upper()  # "x.html"
    assert _color(html, 1, 1) == text_view._COMMENT.upper()  # comment continues
    _w, md = _view("x.md", "# Title\n```\n# not a heading\n```\ntext\n")
    assert _color(md, 0, 2) == text_view._HEADING.upper()
    assert _color(md, 2, 2) == text_view._STRING.upper()  # inside the code fence


def test_markdown_renders_and_switches_to_source() -> None:
    widget, edit = _view("readme.md", "# Hello\n\nSome *text*.\n", editable=True)
    pages = widget.findChild(QStackedWidget)
    browser = widget.findChild(QTextBrowser)
    assert pages.currentWidget() is browser  # rendered first
    assert "Hello" in browser.toPlainText() and "#" not in browser.toPlainText()
    widget.show_source(True)
    assert pages.currentWidget() is edit
    edit.setPlainText("# Changed\n")
    widget.show_source(False)
    assert "Changed" in browser.toPlainText()  # follows edits
    # The choice sticks for the next Markdown file this session.
    widget.show_source(True)
    other, other_edit = _view("other.md", "# Other\n")
    assert other.findChild(QStackedWidget).currentWidget() is other_edit


def test_html_renders_offline() -> None:
    page = '<h1>Title</h1><img src="https://example.com/x.png"><a href="https://example.com">x</a>'
    widget, _edit = _view("index.html", page)
    browser = widget.findChild(QTextBrowser)
    assert "Title" in browser.toPlainText()
    kind = QTextDocument.ResourceType.ImageResource.value
    assert browser.loadResource(kind, QUrl("https://example.com/x.png")) is None
    assert browser.loadResource(kind, QUrl.fromLocalFile("/etc/passwd")) is None
    assert not browser.openLinks() and not browser.openExternalLinks()


def test_large_texts_are_not_highlighted() -> None:
    _w, edit = _view("big.json", '{"a": 1}\n' * (text_view.HIGHLIGHT_LIMIT // 9 + 10))
    assert edit._archy_highlighter is None


# ------------------------------------------------------------------- video
def test_seek_bar_handle_can_be_dragged() -> None:
    from archy.services.providers import _ClickSlider

    slider = _ClickSlider(Qt.Orientation.Horizontal)
    slider.resize(200, 20)
    slider.setRange(0, 1000)
    moved: list[int] = []
    released: list[bool] = []
    slider.sliderMoved.connect(moved.append)
    slider.sliderReleased.connect(lambda: released.append(True))
    slider.show()
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(50, 10))
    assert slider.isSliderDown() and slider.value() == 250
    QTest.mouseMove(slider, QPoint(150, 10))
    assert slider.value() == 750
    QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=QPoint(180, 10))
    assert not slider.isSliderDown() and slider.value() == 900
    assert moved[0] == 250 and 750 in moved and released == [True]


class _Frame:
    def __init__(self, rotation) -> None:  # noqa: ANN001
        self.rotation = rotation


class _Stream:
    def __init__(self, rotate: str | None = None) -> None:
        self.metadata = {"rotate": rotate} if rotate is not None else {}


def test_video_rotation_from_display_matrix_or_tag() -> None:
    from archy.ui.thumbnail_loader import _upright, _video_rotation

    assert _video_rotation(_Frame(-90), _Stream()) == 270
    assert _video_rotation(_Frame(90.0), _Stream()) == 90
    assert _video_rotation(_Frame(-180), _Stream()) == 180
    legacy = type("F", (), {})()  # an old PyAV frame without .rotation
    assert _video_rotation(legacy, _Stream("90")) == 270  # the tag is clockwise
    landscape = QImage(40, 20, QImage.Format.Format_RGB888)
    turned = _upright(landscape, 270)
    assert (turned.width(), turned.height()) == (20, 40)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_portrait_phone_video_thumbnail_stands_upright(tmp_path) -> None:  # noqa: ANN001
    pytest.importorskip("av")
    from archy.ui.thumbnail_loader import _decode_video_frame

    plain = tmp_path / "plain.mp4"
    rotated = tmp_path / "portrait.mp4"
    common = ["ffmpeg", "-loglevel", "error", "-y"]
    subprocess.run(
        [*common, "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25", "-t", "1", str(plain)],
        check=True,
    )
    subprocess.run(
        [*common, "-display_rotation", "-90", "-i", str(plain), "-c", "copy", str(rotated)],
        check=True,
    )
    image = _decode_video_frame(rotated.read_bytes())
    assert image is not None and (image.width(), image.height()) == (90, 160)


# ----------------------------------------------------------- CSV table, find
def test_csv_opens_as_a_rainbow_table_with_a_source_switch() -> None:
    from PySide6.QtWidgets import QTableView

    text = 'naam;plaats;notitie\nMark;Wijchen;"a; b"\nCinzia;Nijmegen\n'
    widget, edit = _view("klanten.csv", text)
    table = widget.findChild(QTableView)
    assert widget.pages.currentWidget() is table
    model = table.model()
    assert (model.rowCount(), model.columnCount()) == (2, 3)
    assert model.headerData(1, Qt.Orientation.Horizontal) == "plaats"
    assert model.data(model.index(0, 2)) == "a; b"
    assert model.data(model.index(1, 2)) == ""  # a short row is padded
    colors = {
        model.data(model.index(0, c), Qt.ItemDataRole.ForegroundRole).name() for c in range(3)
    }
    assert len(colors) == 3  # every column its own colour
    widget.show_source(True)
    assert widget.pages.currentWidget() is edit


def test_find_in_the_file() -> None:
    widget, edit = _view("notes.txt", "foo bar\nFOO baz\nqux foo\n")
    widget.show()
    widget.open_find()
    bar = widget.find_bar
    assert bar.isVisible()
    QTest.keyClicks(bar.field, "foo")
    assert bar.counter.text() == "1/3"  # case-insensitive
    assert len(edit.extraSelections()) == 3  # every match highlighted
    QTest.keyClick(bar.field, Qt.Key.Key_Return)
    assert bar.counter.text() == "2/3" and edit.textCursor().blockNumber() == 1
    QTest.keyClick(bar.field, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert bar.counter.text() == "1/3"
    bar.field.setText("nothing like this")
    assert bar.counter.text() == text_view.tr("find.none")
    QTest.keyClick(bar.field, Qt.Key.Key_Escape)
    assert not bar.isVisible() and edit.extraSelections() == []


def test_find_follows_the_rendered_page_and_the_table() -> None:
    from PySide6.QtWidgets import QTableView

    widget, _edit = _view("readme.md", "# Archy\n\nArchy reads archives.\n")
    widget.show()
    widget.open_find()
    widget.find_bar.field.setText("archy")
    assert widget.find_bar.counter.text() == "1/2"  # in the rendered text: no '#'
    widget.show_source(True)
    assert widget.find_bar.counter.text() == "1/2"  # searched again in the source
    table_view, _e = _view("x.csv", "a,b\nfoo,1\n2,foo\n")
    table_view.show()
    table_view.open_find()
    table_view.find_bar.field.setText("foo")
    table = table_view.findChild(QTableView)
    assert table_view.find_bar.counter.text() == "1/2"
    assert len(table.selectionModel().selectedIndexes()) == 2


def test_html_loads_stylesheets_and_images_from_the_archive_only() -> None:
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtWidgets import QApplication

    png = QByteArray()
    buffer = QBuffer(png)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    picture = QImage(4, 3, QImage.Format.Format_RGB32)
    picture.fill(0xFF0000)
    picture.save(buffer, "PNG")
    files = {"css/site.css": b"p { color: #00aa00; }", "img/a.png": bytes(png)}
    asked: list[str] = []

    def resources(path: str) -> bytes | None:
        asked.append(path)
        return files.get(path)

    page = (
        '<html><head><link rel="stylesheet" href="css/site.css"></head>'
        '<body><p>green</p><img src="img/a.png"><img src="https://x.test/b.png"></body></html>'
    )
    widget, edit = make_text_preview("index.html", page, editable=False, resources=resources)
    widget.resize(400, 300)
    widget.show()
    QApplication.processEvents()  # images load when the page is laid out
    browser = widget.findChild(QTextBrowser)
    block = browser.document().findBlockByNumber(0)
    assert block.text().startswith("green")
    color = block.begin().fragment().charFormat().foreground().color().name()
    assert color == "#00aa00"  # the archive's stylesheet applied
    assert "css/site.css" in asked and "img/a.png" in asked
    assert not any("x.test" in path for path in asked)  # never asked for remote
