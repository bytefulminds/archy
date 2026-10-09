from __future__ import annotations

import html
import io
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time

# Previews of Office documents (Word, Excel, PowerPoint), made from the bytes in
# memory like every other preview: nothing is written to disk and nothing in
# the document runs (macros are never looked at).
#
# Word (.docx) becomes a page of HTML (mammoth: headings, lists, tables, bold
# and italic, pictures); PowerPoint (.pptx) becomes one card per slide with
# its text boxes and pictures where they are on the slide (python-pptx); Excel
# (.xlsx) becomes a table per sheet with the values as last calculated
# (openpyxl). Pictures are handed to the page view as resources next to the
# page ("media/..."), so the HTML stays small. These are previews: layout,
# fonts and colours are close, not exact.
#
# This module is Qt-free; services/providers.py builds the widgets.

WORD_EXT = {".docx", ".docm"}
EXCEL_EXT = {".xlsx", ".xlsm"}
POWERPOINT_EXT = {".pptx", ".pptm"}
OFFICE_EXT = WORD_EXT | EXCEL_EXT | POWERPOINT_EXT

MAX_ROWS = 5_000  # per sheet: a preview, not a spreadsheet program
MAX_COLUMNS = 200
MAX_SLIDES = 300

# Pictures a page view can show (Office also embeds EMF/WMF, which it cannot).
_WEB_IMAGES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/tiff": ".tif",
}


def extension(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot != -1 else ""


def kind_of(name: str) -> str | None:
    """'word', 'excel', 'powerpoint' or None."""
    ext = extension(name)
    if ext in WORD_EXT:
        return "word"
    if ext in EXCEL_EXT:
        return "excel"
    if ext in POWERPOINT_EXT:
        return "powerpoint"
    return None


class OfficeError(Exception):
    """The document could not be read (damaged, encrypted, not Office)."""


@dataclass
class Page:
    """An HTML page and the pictures it refers to (by relative path)."""

    html: str
    media: dict[str, bytes] = field(default_factory=dict)
    body: str = ""  # the content without the page styling (for a thumbnail)

    def reader(self) -> Callable[[str], bytes | None]:
        media = self.media

        def read(reference: str) -> bytes | None:
            return media.get(reference.lstrip("./"))

        read.base = ""  # type: ignore[attr-defined]
        return read


@dataclass
class Sheet:
    title: str
    rows: list[list[str]]
    columns: int
    more_rows: bool = False
    more_columns: bool = False


# ------------------------------------------------------------------ thumbnails
def embedded_thumbnail(data: bytes) -> bytes | None:
    """The preview picture Office stores in the file when asked to
    (docProps/thumbnail.*; PowerPoint usually does), else None."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                lower = name.lower()
                if lower.startswith("docprops/thumbnail.") and lower.endswith(
                    (".jpeg", ".jpg", ".png")
                ):
                    info = archive.getinfo(name)
                    if info.file_size > 8 * 1024 * 1024:
                        return None
                    return archive.read(name)
    except (zipfile.BadZipFile, OSError, KeyError, ValueError):
        return None
    return None


def _encrypted(data: bytes) -> bool:
    """A password-protected Office file is not a zip but an OLE container."""
    return data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


# ---------------------------------------------------------------------- Word
_PAGE_CSS = """
html { background: #e9ebee; }
body { margin: 0; padding: 28px 18px; font-family: Calibri, 'Segoe UI', Helvetica, Arial,
  sans-serif; font-size: 15px; line-height: 1.45; color: #1f2329; }
.page { background: #fff; max-width: 820px; margin: 0 auto; padding: 56px 64px;
  box-shadow: 0 2px 10px rgba(0,0,0,.18); border-radius: 3px; }
h1 { font-size: 1.9em; margin: .6em 0 .4em; } h2 { font-size: 1.5em; margin: .9em 0 .4em; }
h3 { font-size: 1.25em; } h4, h5, h6 { font-size: 1.05em; }
p { margin: 0 0 .7em; } img { max-width: 100%; height: auto; }
table { border-collapse: collapse; margin: .6em 0 1em; }
td, th { border: 1px solid #c8ccd2; padding: 4px 8px; vertical-align: top; }
a { color: #1a6aa8; }
"""


def word_page(data: bytes) -> Page:
    import mammoth

    if _encrypted(data):
        raise OfficeError("encrypted")
    media: dict[str, bytes] = {}

    def picture(image) -> dict[str, str]:  # noqa: ANN001 - mammoth image
        suffix = _WEB_IMAGES.get(str(image.content_type or "").lower())
        if suffix is None:
            return {"alt": ""}
        with image.open() as stream:
            content = stream.read()
        path = f"media/image{len(media) + 1}{suffix}"
        media[path] = content
        return {"src": path}

    try:
        result = mammoth.convert_to_html(
            io.BytesIO(data),
            convert_image=mammoth.images.img_element(picture),
            style_map=_WORD_STYLES,
        )
    except Exception as exc:  # noqa: BLE001 - any damage: not readable
        raise OfficeError(str(exc)) from exc
    body = result.value or ""
    page = (
        f'<!doctype html><html><head><meta charset="utf-8"><style>{_PAGE_CSS}</style>'
        f'</head><body><div class="page">{body}</div></body></html>'
    )
    return Page(page, media, body)


# Word's title styles have no HTML counterpart in mammoth's defaults.
_WORD_STYLES = """
p[style-name='Title'] => h1.title:fresh
p[style-name='Subtitle'] => h2.subtitle:fresh
"""


# --------------------------------------------------------------------- Excel
def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return f"{value:.10g}"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M" if value.time() != time(0) else "%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    return str(value)


def excel_sheets(data: bytes) -> list[Sheet]:
    """Every sheet's values (as last calculated by Excel), at most MAX_ROWS
    by MAX_COLUMNS each."""
    import openpyxl

    if _encrypted(data):
        raise OfficeError("encrypted")
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise OfficeError(str(exc)) from exc
    sheets: list[Sheet] = []
    try:
        for worksheet in book.worksheets:
            rows: list[list[str]] = []
            more_rows = more_columns = False
            width = 0
            for row in worksheet.iter_rows(values_only=True):
                if len(rows) >= MAX_ROWS:
                    more_rows = True
                    break
                cells = [_cell_text(v) for v in row[:MAX_COLUMNS]]
                more_columns = more_columns or len(row) > MAX_COLUMNS
                while cells and cells[-1] == "":
                    cells.pop()
                rows.append(cells)
                width = max(width, len(cells))
            while rows and not rows[-1]:
                rows.pop()  # trailing empty rows (formatting reaches far down)
            sheets.append(Sheet(str(worksheet.title), rows, width, more_rows, more_columns))
    except Exception as exc:  # noqa: BLE001
        raise OfficeError(str(exc)) from exc
    finally:
        book.close()
    return sheets


def column_name(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


# ---------------------------------------------------------------- PowerPoint
_SLIDES_CSS = """
html { background: #e9ebee; }
body { margin: 0; padding: 22px 18px 30px; font-family: Calibri, 'Segoe UI', Helvetica,
  Arial, sans-serif; color: #1f2329; }
.slide { position: relative; max-width: 960px; margin: 0 auto 8px; background: #fff;
  box-shadow: 0 2px 10px rgba(0,0,0,.18); border-radius: 3px; overflow: hidden;
  container-type: inline-size; }
.number { max-width: 960px; margin: 0 auto 22px; font-size: 12px; color: #6b7280; }
.shape { position: absolute; overflow: hidden; }
.shape p { margin: 0 0 .25em; line-height: 1.15; }
.shape img { width: 100%; height: 100%; object-fit: fill; display: block; }
.shape table { border-collapse: collapse; width: 100%; height: 100%; }
.shape td { border: 1px solid #c8ccd2; padding: .3cqw .6cqw; font-size: 1.6cqw;
  vertical-align: top; }
"""

_EMU_PER_PT = 12_700


def _percent(value: int | None, whole: int) -> float:
    return 100.0 * (value or 0) / whole if whole else 0.0


def powerpoint_page(data: bytes) -> Page:
    from pptx import Presentation

    if _encrypted(data):
        raise OfficeError("encrypted")
    try:
        deck = Presentation(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise OfficeError(str(exc)) from exc
    width = int(deck.slide_width or 12_192_000)
    height = int(deck.slide_height or 6_858_000)
    media: dict[str, bytes] = {}
    parts = [
        f'<!doctype html><html><head><meta charset="utf-8"><style>{_SLIDES_CSS}</style>'
        "</head><body>"
    ]
    for number, slide in enumerate(deck.slides, start=1):
        if number > MAX_SLIDES:
            break
        shapes: list[str] = []
        try:
            for shape in slide.shapes:
                shapes.extend(_shape_html(shape, width, height, media, number))
        except Exception:  # noqa: BLE001, S110 - one odd slide must not sink the rest
            pass
        parts.append(
            f'<div class="slide" style="aspect-ratio: {width} / {height}">{"".join(shapes)}</div>'
            f'<div class="number">{number}</div>'
        )
    parts.append("</body></html>")
    first = next(iter(deck.slides), None)
    return Page("".join(parts), media, _slide_outline(first) if first is not None else "")


def _slide_outline(slide) -> str:  # noqa: ANN001
    """A slide's text as plain HTML, the title as a heading (for a
    thumbnail, which has no room for the layout)."""
    parts = []
    try:
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False) or not shape.text_frame.text.strip():
                continue
            text = html.escape(shape.text_frame.text.strip()).replace("\n", "<br>")
            title = _default_size(shape) >= 40.0
            parts.append(f"<h1>{text}</h1>" if title else f"<p>{text}</p>")
    except Exception:  # noqa: BLE001, S110
        pass
    return "".join(parts)


def _box(shape, width: int, height: int) -> str:  # noqa: ANN001
    return (
        f"left:{_percent(shape.left, width):.3f}%;top:{_percent(shape.top, height):.3f}%;"
        f"width:{_percent(shape.width, width):.3f}%;height:{_percent(shape.height, height):.3f}%"
    )


def _shape_html(shape, width: int, height: int, media: dict[str, bytes], number: int) -> list[str]:  # noqa: ANN001, E501
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    kind = getattr(shape, "shape_type", None)
    if kind == MSO_SHAPE_TYPE.GROUP:
        out: list[str] = []
        for child in shape.shapes:
            out.extend(_shape_html(child, width, height, media, number))
        return out
    if shape.left is None or shape.top is None or not shape.width or not shape.height:
        return []
    box = _box(shape, width, height)
    image = getattr(shape, "image", None) if kind == MSO_SHAPE_TYPE.PICTURE else None
    if image is not None:
        suffix = _WEB_IMAGES.get(str(image.content_type or "").lower())
        if suffix is None:
            return []
        path = f"media/slide{number}-{len(media) + 1}{suffix}"
        media[path] = image.blob
        return [f'<div class="shape" style="{box}"><img src="{path}" alt=""></div>']
    if getattr(shape, "has_table", False):
        rows = []
        for row in shape.table.rows:
            cells = "".join(f"<td>{html.escape(cell.text)}</td>" for cell in row.cells)
            rows.append(f"<tr>{cells}</tr>")
        return [f'<div class="shape" style="{box}"><table>{"".join(rows)}</table></div>']
    if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
        default = _default_size(shape)
        paragraphs = []
        for paragraph in shape.text_frame.paragraphs:
            runs = []
            for run in paragraph.runs:
                size = run.font.size.pt if run.font.size else default
                style = f"font-size:{100 * size * _EMU_PER_PT / width:.3f}cqw"
                if run.font.bold:
                    style += ";font-weight:bold"
                if run.font.italic:
                    style += ";font-style:italic"
                color = _rgb(run.font)
                if color:
                    style += f";color:#{color}"
                runs.append(f'<span style="{style}">{html.escape(run.text)}</span>')
            styles = []
            if paragraph.level:
                styles.append(f"margin-left:{paragraph.level * 2.2:.1f}cqw")
            align = _alignment(paragraph)
            if align:
                styles.append(f"text-align:{align}")
            attributes = f' style="{";".join(styles)}"' if styles else ""
            empty_size = 100 * default * _EMU_PER_PT / width
            content = "".join(runs) or f'<span style="font-size:{empty_size:.3f}cqw">&nbsp;</span>'
            paragraphs.append(f"<p{attributes}>{content}</p>")
        return [f'<div class="shape" style="{box}">{"".join(paragraphs)}</div>']
    return []


def _default_size(shape) -> float:  # noqa: ANN001
    """Points for text without its own size (it inherits from the layout)."""
    try:
        if shape.is_placeholder:
            kind = str(shape.placeholder_format.type).upper()
            if "TITLE" in kind and "SUB" not in kind:
                return 40.0
            if "SUBTITLE" in kind:
                return 24.0
            return 22.0
    except Exception:  # noqa: BLE001, S110
        pass
    return 18.0


def _rgb(font) -> str | None:  # noqa: ANN001
    try:
        color = font.color
        if color is not None and color.type is not None and color.rgb is not None:
            return str(color.rgb)
    except Exception:  # noqa: BLE001, S110 - theme colours etc.: the default
        pass
    return None


def _alignment(paragraph) -> str | None:  # noqa: ANN001
    value = str(getattr(paragraph, "alignment", None) or "").upper()
    for key, css in (("CENTER", "center"), ("RIGHT", "right"), ("JUSTIFY", "justify")):
        if key in value:
            return css
    return None
