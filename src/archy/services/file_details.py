from __future__ import annotations

import contextlib
import io
from datetime import datetime
from fractions import Fraction

from archy.i18n import tr

# The details shown under a file's name in the preview pane, and in the file
# info: what is useful to know about that kind of file (a photo's size in
# pixels and camera settings, a PDF's page count and author, a video's length
# and resolution, a text file's lines and encoding). Each detail is a pair
# (i18n key of its label, value); the header shows the values, the info window
# label and value. No Qt here: the values come from bytes already in memory or
# from what the preview widget found out.

Details = list[tuple[str, str]]

# EXIF tags (TIFF/EXIF numbering).
_MAKE = 271
_MODEL = 272
_EXIF_IFD = 0x8769
_EXPOSURE_TIME = 33434
_F_NUMBER = 33437
_ISO = 34855
_DATE_TAKEN = 36867
_FOCAL_LENGTH = 37386
_LENS = 42036


# Shown in the file info only; too long for the line under the name.
_INFO_ONLY = {"details.lens"}


def subtitle(details: Details) -> str:
    """The details as one line, values only."""
    return " · ".join(value for key, value in details if value and key not in _INFO_ONLY)


def dimensions(width: int, height: int) -> str:
    return f"{width} × {height} px"


def duration(ms: int) -> str:
    """0:42, 12:05 or 1:02:09."""
    total = max(0, int(ms) // 1000)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def count(key: str, amount: int) -> str:
    """ "1 page" / "12 pages": ``key`` has plural forms in the catalog."""
    return tr(key, count=amount)


def image_details(data: bytes, width: int, height: int) -> Details:
    """A photo's size in pixels, then camera, lens, exposure and the date it
    was taken when its EXIF has them."""
    details: Details = [("details.dimensions", dimensions(width, height))]
    exif = _read_exif(data)
    if not exif:
        return details
    camera = _camera(exif.get(_MAKE), exif.get(_MODEL))
    if camera:
        details.append(("details.camera", camera))
    lens = _text(exif.get(_LENS))
    if lens:
        details.append(("details.lens", lens))
    exposure = [
        part
        for part in (
            _focal(exif.get(_FOCAL_LENGTH)),
            _aperture(exif.get(_F_NUMBER)),
            _shutter(exif.get(_EXPOSURE_TIME)),
            _iso(exif.get(_ISO)),
        )
        if part
    ]
    if exposure:
        details.append(("details.exposure", ", ".join(exposure)))
    taken = _date(exif.get(_DATE_TAKEN))
    if taken:
        details.append(("details.taken", taken))
    return details


def text_details(text: str, encoding: str | None, *, partial: bool = False) -> Details:
    """Lines and encoding of a text file (lines only when all of it is shown)."""
    details: Details = []
    if not partial:
        lines = text.count("\n") + (0 if not text or text.endswith("\n") else 1)
        details.append(("details.lines", count("details.line_count", lines)))
    if encoding:
        details.append(("details.encoding", _encoding_label(encoding)))
    return details


def _encoding_label(encoding: str) -> str:
    name = encoding.lower().replace("_", "-")
    known = {"utf-8": "UTF-8", "utf-8-sig": "UTF-8 (BOM)", "ascii": "ASCII"}
    return known.get(name, encoding.upper())


def _read_exif(data: bytes) -> dict[int, object]:
    """The EXIF tags of a JPEG, TIFF, PNG, WebP, HEIC or AVIF, flattened (main
    IFD plus the EXIF sub-IFD); empty when there are none or Pillow is missing."""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - optional
        return {}
    exif = None
    try:
        with Image.open(io.BytesIO(data)) as picture:  # reads the header only
            exif = picture.getexif()
    except Exception:  # noqa: BLE001 - not an image Pillow reads: try HEIF below
        exif = _heif_exif(data)
    if not exif:
        return {}
    tags: dict[int, object] = dict(exif)
    with contextlib.suppress(Exception):  # no EXIF sub-IFD
        tags.update(exif.get_ifd(_EXIF_IFD))
    return tags


def _heif_exif(data: bytes):  # noqa: ANN202 - PIL.Image.Exif | None
    for name in ("pi_heif", "pillow_heif"):
        try:
            library = __import__(name)
            heif = library.open_heif(io.BytesIO(data))
            raw = heif.info.get("exif")
            if not raw:
                return None
            from PIL import Image

            exif = Image.Exif()
            exif.load(raw[raw.find(b"Exif") :] if b"Exif" in raw[:16] else raw)
            return exif
        except Exception:  # noqa: BLE001, S112 - library missing or no EXIF
            continue
    return None


def _text(value: object) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value).strip("\x00 ").strip() if value is not None else ""


def _camera(make: object, model: object) -> str:
    make_text, model_text = _text(make), _text(model)
    if not model_text:
        return make_text
    brand = make_text.split(" ")[0]
    if brand.isupper() and len(brand) > 3:
        brand = brand.capitalize()  # SONY, NIKON, FUJIFILM -> Sony, Nikon, Fujifilm
    if brand and not model_text.lower().startswith(brand.lower()):
        return f"{brand} {model_text}"
    return model_text


def _number(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type] - IFDRational, int, float
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return result if result > 0 else None


def _focal(value: object) -> str:
    focal = _number(value)
    return f"{focal:g} mm" if focal else ""


def _aperture(value: object) -> str:
    aperture = _number(value)
    return f"f/{round(aperture, 1):g}" if aperture else ""


def _shutter(value: object) -> str:
    seconds = _number(value)
    if not seconds:
        return ""
    if seconds < 1:
        fraction = Fraction(seconds).limit_denominator(8000)
        if fraction.numerator == 1:
            return f"1/{fraction.denominator} s"
        return f"1/{round(1 / seconds)} s"
    return f"{seconds:g} s"


def _iso(value: object) -> str:
    if isinstance(value, tuple):
        value = value[0] if value else None
    iso = _number(value)
    return f"ISO {iso:g}" if iso else ""


def _date(value: object) -> str:
    text = _text(value)
    try:
        return datetime.strptime(text[:19], "%Y:%m:%d %H:%M:%S").strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ""
