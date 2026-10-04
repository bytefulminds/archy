from __future__ import annotations

import io

from PySide6.QtCore import QBuffer, QByteArray, QSize, Qt
from PySide6.QtGui import QImage, QImageReader

# Decoding image bytes for the preview and the thumbnails, in memory.
#
# Qt reads most formats itself (JPEG, PNG, GIF, WebP, TIFF, ...). HEIC/HEIF,
# the iPhone's photo format, only on macOS (Qt's ImageIO plugin); elsewhere,
# and for AVIF, the bytes go to pi-heif / Pillow instead. pi-heif is the
# decode-only build of pillow-heif: no HEVC encoder (x265, GPL) in the wheels. Either way the
# camera's orientation is applied, so a portrait photo stands upright.
#
# QImageReader rather than QImage.fromData: PySide releases the GIL during
# QImageReader.read() but not during fromData(), and a JPEG can be decoded
# straight to a smaller size (most of the work skipped) when one is asked for.

HEIF_EXT = {".heic", ".heif", ".hif", ".avif"}
# ISO base media "brands" of HEIF still images (HEVC) and AVIF (AV1).
_HEIF_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1"}
_AVIF_BRANDS = {b"avif", b"avis"}


def decode_image(data: bytes, box: QSize | None = None) -> QImage:
    """The image in ``data``, upright, fitted inside ``box`` when given (never
    enlarged). A null QImage when it cannot be decoded."""
    image = _decode_qt(data, box)
    if image.isNull() and _is_heif_family(data):
        image = _decode_heif(data, box)
    if image.isNull() or box is None:
        return image
    if image.width() <= box.width() and image.height() <= box.height():
        return image
    return image.scaled(
        box, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )


def _decode_qt(data: bytes, box: QSize | None) -> QImage:
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QBuffer.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)  # EXIF orientation
    if box is not None:
        size = reader.size()  # header only
        if size.isValid() and (size.width() > box.width() or size.height() > box.height()):
            # The box is square, so fitting the stored (unrotated) size also
            # fits the rotated result.
            reader.setScaledSize(size.scaled(box, Qt.AspectRatioMode.KeepAspectRatio))
    return reader.read()


def _is_heif_family(data: bytes) -> bool:
    """An ISO base media file whose brand says HEIF or AVIF."""
    if len(data) < 16 or data[4:8] != b"ftyp":
        return False
    size = int.from_bytes(data[0:4], "big")
    brands = {data[8:12]}
    end = min(len(data), max(16, size))
    brands.update(data[i : i + 4] for i in range(16, end - 3, 4))
    return bool(brands & (_HEIF_BRANDS | _AVIF_BRANDS))


def _decode_heif(data: bytes, box: QSize | None) -> QImage:
    image = _decode_with_heif_library(data, box)
    if image.isNull():
        image = _decode_with_pillow(data)  # AVIF: Pillow reads it natively
    return image


def _heif_library():  # noqa: ANN202 - the pi_heif (or pillow_heif) module, or None
    for name in ("pi_heif", "pillow_heif"):
        try:
            return __import__(name)
        except Exception:  # noqa: BLE001, S112 - optional where Qt reads HEIC
            continue
    return None


def _decode_with_heif_library(data: bytes, box: QSize | None) -> QImage:
    library = _heif_library()
    if library is None:
        return QImage()
    try:
        heif = library.open_heif(io.BytesIO(data), convert_hdr_to_8bit=True)
        primary = heif[heif.primary_index]
        if box is not None:
            # Photos from a phone carry a small preview; a thumbnail needs
            # nothing more and is much quicker than decoding 12+ megapixels.
            for index in range(len(primary.info.get("thumbnails") or ())):
                thumb = primary.get_thumbnail(index)
                width, height = thumb.size
                if max(width, height) >= max(box.width(), box.height()):
                    image = _qimage(thumb.data, width, height, thumb.stride, thumb.mode)
                    if not image.isNull():
                        return image
        width, height = primary.size
        return _qimage(primary.data, width, height, primary.stride, primary.mode)
    except Exception:  # noqa: BLE001 - not decodable: no image
        return QImage()


def _decode_with_pillow(data: bytes) -> QImage:
    try:
        from PIL import Image, ImageOps
    except Exception:  # noqa: BLE001
        return QImage()
    try:
        with Image.open(io.BytesIO(data)) as source:
            picture = ImageOps.exif_transpose(source).convert("RGBA")
            width, height = picture.size
            return _qimage(picture.tobytes(), width, height, width * 4, "RGBA")
    except Exception:  # noqa: BLE001
        return QImage()


def _qimage(data: object, width: int, height: int, stride: int, mode: str) -> QImage:
    formats = {
        "RGB": QImage.Format.Format_RGB888,
        "RGBA": QImage.Format.Format_RGBA8888,
        "BGR": QImage.Format.Format_BGR888,
    }
    fmt = formats.get(mode)
    if fmt is None:
        return QImage()
    # copy(): the QImage must own its pixels, not borrow the decoder's buffer.
    return QImage(bytes(data), width, height, stride, fmt).copy()
