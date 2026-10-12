"""Image decoding for the preview and thumbnails: WebP, HEIC, AVIF and the
camera's orientation."""

from __future__ import annotations

import base64
import io

import pytest

# Skip the module when the Qt GUI stack cannot load (a CI image without the
# system libraries Qt needs). importorskip only skips a missing module, not a
# missing system library underneath it, so import explicitly.
try:
    from PySide6.QtWidgets import QApplication as _QApplication  # noqa: F401
except Exception as _exc:  # noqa: BLE001 - missing Qt libs -> skip, don't fail
    pytest.skip(f"PySide6 GUI not available: {_exc}", allow_module_level=True)
Image = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QBuffer, QByteArray, QSize  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402

from archy.services.image_decode import decode_image  # noqa: E402
from archy.services.preview import PreviewKind, kind_for_name  # noqa: E402


@pytest.fixture(autouse=True)
def _app():  # noqa: ANN202 - Qt finds its image plugins through the app
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _red(width: int = 320, height: int = 200):  # noqa: ANN202
    return Image.new("RGB", (width, height), (220, 30, 30))


def _is_red(image: QImage) -> bool:
    color = QColor(image.pixel(image.width() // 2, image.height() // 2))
    return color.red() > 180 and color.green() < 80 and color.blue() < 80


@pytest.mark.parametrize("name", ["a.heic", "B.HEIF", "c.webp", "d.avif", "e.tif", "f.jpg"])
def test_photo_formats_are_previewed_as_images(name: str) -> None:
    assert kind_for_name(name) is PreviewKind.IMAGE


def test_webp() -> None:
    source = QImage(320, 200, QImage.Format.Format_RGB32)
    source.fill(QColor(220, 30, 30))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    assert source.save(buffer, "WEBP")
    image = decode_image(bytes(data))
    assert (image.width(), image.height()) == (320, 200) and _is_red(image)
    thumb = decode_image(bytes(data), QSize(100, 100))
    assert thumb.width() <= 100 and thumb.height() <= 100


# A 64x40 red HEIC (made with pillow-heif); Archy itself only decodes.
_RED_HEIC = base64.b64decode(
    "AAAAHGZ0eXBoZWljAAAAAG1pZjFoZWljbWlhZgAAAX1tZXRhAAAAAAAAACFoZGxyAAAAAAAAAABwaWN0AAAAAAAAAAAAAAAAAAAAACJpbG9jAAAAAERAAAEAAQAAAAABoQABAAAAAAAAAB8AAAAjaWluZgAAAAAAAQAAABVpbmZlAgAAAAABAABodmMxAAAAAA5waXRtAAAAAAABAAAA/WlwcnAAAADdaXBjbwAAAHZodmNDAQNwAAAAAAAAAAAAHvAA/P34+AAADwNgAAEAGEABDAH//wNwAAADAJAAAAMAAAMAHroCQGEAAQAqQgEBA3AAAAMAkAAAAwAAAwAeoCCBBZbq5Ka5uAhoMCAAAAMDIAAAAwAhYgABAAZEAcFzwIkAAAATY29scm5jbHgAAQANAAaAAAAAFGlzcGUAAAAAAAAAQAAAAEAAAAAoY2xhcAAAAEAAAAABAAAAKAAAAAEAAAAAAAAAAv///+gAAAACAAAAEHBpeGkAAAAAAwgICAAAABhpcG1hAAAAAAAAAAEAAQWBAgMFhAAAACdtZGF0AAAAGygBrwY4iS58L/UgT///j1f2X90Q2zNTCc4z8A=="
)


def test_heic() -> None:
    if not (_has("pi_heif") or _has("pillow_heif") or _qt_reads("heic")):
        pytest.skip("no HEIC decoder")
    image = decode_image(_RED_HEIC)
    assert (image.width(), image.height()) == (64, 40) and _is_red(image)
    thumb = decode_image(_RED_HEIC, QSize(32, 32))
    assert thumb.width() == 32 and thumb.height() <= 32 and _is_red(thumb)


def _has(module: str) -> bool:
    try:
        __import__(module)
    except Exception:  # noqa: BLE001
        return False
    return True


def _qt_reads(fmt: str) -> bool:
    from PySide6.QtGui import QImageReader

    return fmt.encode() in [bytes(f) for f in QImageReader.supportedImageFormats()]


def test_avif() -> None:
    from PIL import features

    if not features.check("avif"):
        pytest.skip("Pillow without AVIF")
    out = io.BytesIO()
    _red().save(out, "AVIF", quality=90)
    image = decode_image(out.getvalue())
    assert (image.width(), image.height()) == (320, 200) and _is_red(image)


def test_a_portrait_photo_stands_upright() -> None:
    # Cameras store a portrait photo sideways plus an EXIF orientation tag.
    out = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to display
    _red(64, 32).save(out, "JPEG", exif=exif)
    image = decode_image(out.getvalue())
    assert (image.width(), image.height()) == (32, 64)
    thumb = decode_image(out.getvalue(), QSize(16, 16))
    assert (thumb.width(), thumb.height()) == (8, 16)


def test_garbage_is_a_null_image() -> None:
    assert decode_image(b"not an image").isNull()
    assert decode_image(b"\0\0\0\x18ftypheic" + b"\0" * 40).isNull()


def test_a_damaged_jpeg_warning_names_the_file(capfd) -> None:  # noqa: ANN001
    # A photo cut short still decodes, with a warning from Qt's JPEG reader;
    # Archy adds which file it was (its path in the archive).
    from PySide6.QtCore import qInstallMessageHandler

    from archy.app import _qt_message
    from archy.services.image_decode import describing

    buffer = io.BytesIO()
    _red().save(buffer, "JPEG")
    damaged = buffer.getvalue()[:-300] + b"\xff\xd9"
    qInstallMessageHandler(_qt_message)
    try:
        with describing("2009/holiday/IMG_0042.jpg"):
            image = decode_image(damaged, QSize(64, 64), "IMG_0042.jpg")
        decode_image(damaged, name="IMG_0043.jpg")  # no path given: the name
    finally:
        qInstallMessageHandler(None)
    assert not image.isNull()
    err = capfd.readouterr().err
    assert "qt.gui.imageio.jpeg: Corrupt JPEG data" in err
    assert "[2009/holiday/IMG_0042.jpg]" in err
    assert "[IMG_0043.jpg]" in err
