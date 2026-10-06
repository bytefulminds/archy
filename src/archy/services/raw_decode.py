from __future__ import annotations

import io
import struct

from PySide6.QtCore import QBuffer, QByteArray, QSize, Qt
from PySide6.QtGui import QImage, QImageIOHandler, QImageReader, QTransform

# Camera RAW files and Photoshop documents, shown without a RAW converter or
# Photoshop: what is already inside the file.
#
# RAW: every camera stores a ready-made JPEG next to the sensor data (Canon CR2
# and CR3, Nikon NEF, Sony ARW, Fujifilm RAF, Adobe DNG, Olympus ORF,
# Panasonic RW2, ...), often at full or half resolution. Rather than parsing
# each maker's layout, the file is scanned for JPEG streams, their sizes are
# read from the JPEG headers, and the largest one Qt can decode is used. The
# orientation comes from the RAW's own TIFF header (or the CMT1 box of a CR3)
# when the embedded JPEG carries none.
#
# PSD: the flattened composite image Photoshop stores ("Maximize
# compatibility", on by default) is read by Pillow; failing that (PSB, 16-bit
# modes Pillow does not read), the small JPEG thumbnail in the image resources.

RAW_EXT = {
    ".cr2",
    ".cr3",
    ".crw",
    ".nef",
    ".nrw",
    ".arw",
    ".srf",
    ".sr2",
    ".dng",
    ".raf",
    ".orf",
    ".rw2",
    ".rwl",
    ".pef",
    ".srw",
    ".3fr",
    ".iiq",
    ".erf",
    ".kdc",
    ".mos",
    ".mrw",
}
PSD_EXT = {".psd", ".psb"}

_SOI = b"\xff\xd8\xff"
# SOF markers of JPEG that Qt (libjpeg) decodes: baseline, extended,
# progressive. SOF3 (lossless) is the raw sensor data in CR2/DNG: skipped.
_SOF_DECODABLE = {0xC0, 0xC1, 0xC2}
_SOF_OTHER = {0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
_MAX_CANDIDATES = 64

# EXIF orientation -> rotation (degrees clockwise) and mirror.
_ORIENTATION = {
    1: (0, False),
    2: (0, True),
    3: (180, False),
    4: (180, True),
    5: (90, True),
    6: (90, False),
    7: (270, True),
    8: (270, False),
}


def extension(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot != -1 else ""


def is_raw(name: str) -> bool:
    return extension(name) in RAW_EXT


def is_psd(name: str) -> bool:
    return extension(name) in PSD_EXT


# ------------------------------------------------------------------- RAW
def jpeg_size(data: bytes | memoryview, start: int) -> tuple[int, int, bool] | None:
    """(width, height, decodable) of the JPEG stream at ``start``, from its
    frame header; None when the bytes there are not a JPEG header."""
    view = memoryview(data)
    end = len(view)
    pos = start + 2
    while pos + 4 <= end:
        if view[pos] != 0xFF:
            return None
        marker = view[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        length = (view[pos + 2] << 8) | view[pos + 3]
        if length < 2:
            return None
        if marker in _SOF_DECODABLE or marker in _SOF_OTHER:
            if pos + 9 > end:
                return None
            height = (view[pos + 5] << 8) | view[pos + 6]
            width = (view[pos + 7] << 8) | view[pos + 8]
            if not width or not height:
                return None
            return width, height, marker in _SOF_DECODABLE
        if marker in (0xDA, 0xD9):  # scan data before any frame header
            return None
        pos += 2 + length
    return None


def embedded_jpegs(data: bytes) -> list[tuple[int, int, int]]:
    """(offset, width, height) of the decodable JPEG streams in ``data``,
    largest first."""
    found: list[tuple[int, int, int]] = []
    pos = data.find(_SOI)
    while pos != -1 and len(found) < _MAX_CANDIDATES:
        size = jpeg_size(data, pos)
        if size is not None and size[2]:
            found.append((pos, size[0], size[1]))
        pos = data.find(_SOI, pos + 3)
    found.sort(key=lambda item: item[1] * item[2], reverse=True)
    return found


def raw_orientation(data: bytes) -> int:
    """The EXIF orientation of a RAW file (1 when unknown)."""
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return _tiff_orientation(data, 0)
    if data[4:8] == b"ftyp":  # CR3: TIFF IFD0 in the CMT1 box
        box = data.find(b"CMT1", 0, 1 << 20)
        if box != -1:
            return _tiff_orientation(data, box + 4)
    if data[:4] == b"IIRO":  # Olympus ORF variant
        return _tiff_orientation(data, 0, magic_ok=True)
    return 1


def _tiff_orientation(data: bytes, base: int, *, magic_ok: bool = False) -> int:
    try:
        order = data[base : base + 2]
        if order not in (b"II", b"MM"):
            return 1
        endian = "<" if order == b"II" else ">"
        (ifd,) = struct.unpack_from(endian + "I", data, base + 4)
        (count,) = struct.unpack_from(endian + "H", data, base + ifd)
        for i in range(min(count, 512)):
            entry = base + ifd + 2 + 12 * i
            tag, kind = struct.unpack_from(endian + "HH", data, entry)
            if tag == 0x0112:
                (value,) = struct.unpack_from(endian + "H", data, entry + 8)
                return value if value in _ORIENTATION else 1
    except struct.error:
        pass
    return 1


def decode_raw(data: bytes, box: QSize | None = None) -> QImage:
    """The embedded preview of a camera RAW file, upright, fitted in ``box``
    when given; a null QImage when there is none Qt can decode."""
    orientation = raw_orientation(data)
    for offset, width, height in embedded_jpegs(data):
        if width < 64 or height < 64:
            continue  # tiny thumbnails only when nothing better exists
        image = _decode_jpeg_at(data, offset, width, height, box, orientation)
        if not image.isNull():
            return image
    for offset, width, height in embedded_jpegs(data):
        image = _decode_jpeg_at(data, offset, width, height, box, orientation)
        if not image.isNull():
            return image
    return QImage()


def _decode_jpeg_at(
    data: bytes, offset: int, width: int, height: int, box: QSize | None, orientation: int
) -> QImage:
    buffer = QBuffer()
    buffer.setData(QByteArray(data[offset:]))
    buffer.open(QBuffer.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b"jpeg")
    reader.setAutoTransform(True)
    if box is not None and (width > box.width() or height > box.height()):
        reader.setScaledSize(QSize(width, height).scaled(box, Qt.AspectRatioMode.KeepAspectRatio))
    has_own = reader.transformation() != QImageIOHandler.Transformation.TransformationNone
    image = reader.read()
    if image.isNull() or has_own or orientation == 1:
        return image
    return _orient(image, orientation)


def _orient(image: QImage, orientation: int) -> QImage:
    degrees, mirror = _ORIENTATION.get(orientation, (0, False))
    if mirror:
        image = image.mirrored(True, False)
    if degrees:
        image = image.transformed(QTransform().rotate(degrees))
    return image


# ------------------------------------------------------------------- PSD
def decode_psd(data: bytes, box: QSize | None = None) -> QImage:
    """The composite image of a Photoshop document (or, failing that, its
    embedded thumbnail), fitted in ``box`` when given."""
    image = _psd_composite(data)
    if image.isNull():
        image = psd_thumbnail(data)
    if image.isNull() or box is None:
        return image
    if image.width() <= box.width() and image.height() <= box.height():
        return image
    return image.scaled(
        box, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )


def _psd_composite(data: bytes) -> QImage:
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return QImage()
    try:
        with Image.open(io.BytesIO(data)) as source:
            picture = source.convert("RGBA")
    except Exception:  # noqa: BLE001 - PSB or a mode Pillow does not read
        return QImage()
    width, height = picture.size
    raw = picture.tobytes()
    return QImage(raw, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()


def psd_thumbnail(data: bytes) -> QImage:
    """The JPEG thumbnail in a PSD's image resources (resource 1036 or 1033)."""
    jpeg = psd_thumbnail_jpeg(data)
    if jpeg is None:
        return QImage()
    image = QImage()
    image.loadFromData(jpeg, "JPEG")
    if not image.isNull() and _psd_thumbnail_is_bgr(data):
        image = image.rgbSwapped()
    return image


def psd_thumbnail_jpeg(data: bytes) -> bytes | None:
    resources = _psd_resources(data)
    for wanted in (1036, 1033):
        block = resources.get(wanted)
        if block is not None and len(block) > 28:
            return block[28:]  # after the 28-byte thumbnail header
    return None


def _psd_thumbnail_is_bgr(data: bytes) -> bool:
    # Resource 1033 (Photoshop 4) stored the thumbnail as BGR.
    resources = _psd_resources(data)
    return 1036 not in resources and 1033 in resources


def _psd_resources(data: bytes) -> dict[int, bytes]:
    if data[:4] != b"8BPS" or len(data) < 34:
        return {}
    try:
        pos = 26
        (color_len,) = struct.unpack_from(">I", data, pos)
        pos += 4 + color_len
        (res_len,) = struct.unpack_from(">I", data, pos)
        pos += 4
        end = min(len(data), pos + res_len)
        found: dict[int, bytes] = {}
        while pos + 12 <= end and data[pos : pos + 4] == b"8BIM":
            (rid,) = struct.unpack_from(">H", data, pos + 4)
            name_len = data[pos + 6]
            name_total = 1 + name_len
            name_total += name_total % 2  # padded to even
            pos += 6 + name_total
            (size,) = struct.unpack_from(">I", data, pos)
            pos += 4
            found[rid] = data[pos : pos + size]
            pos += size + (size % 2)
        return found
    except struct.error:
        return {}
