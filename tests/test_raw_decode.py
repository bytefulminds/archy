"""Camera RAW files and Photoshop documents (services/raw_decode.py): the
embedded preview, upright, with the camera's details; a PSD's composite
image or its thumbnail."""

from __future__ import annotations

import io
import struct

import pytest

try:
    from PySide6.QtWidgets import QApplication as _QApplication  # noqa: F401
except Exception as _exc:  # noqa: BLE001 - missing Qt libs -> skip, don't fail
    pytest.skip(f"PySide6 GUI not available: {_exc}", allow_module_level=True)
Image = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

from archy.services import file_details, raw_decode  # noqa: E402
from archy.services.image_decode import decode_image  # noqa: E402
from archy.services.preview import PreviewKind, kind_for_name  # noqa: E402


@pytest.fixture(autouse=True)
def _app():  # noqa: ANN202
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _jpeg(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "JPEG", quality=85)
    return out.getvalue()


def _tiff_header(orientation: int, make: str = "NIKON CORPORATION", model: str = "NIKON Z 6_2"):
    """A small TIFF (like a RAW's IFD0 thumbnail) with camera EXIF."""
    from PIL import TiffImagePlugin

    info = TiffImagePlugin.ImageFileDirectory_v2()
    info[0x0112] = orientation
    info[271] = make
    info[272] = model
    out = io.BytesIO()
    Image.new("RGB", (32, 24), (0, 0, 200)).save(out, "TIFF", tiffinfo=info)
    return out.getvalue()


def _fake_lossless_jpeg(width: int, height: int) -> bytes:
    # The header of a lossless (SOF3) stream, like a CR2's sensor data: Qt
    # cannot decode it, so it must be skipped even though it is the largest.
    sof = struct.pack(">BHHB", 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8\xff\xc3" + struct.pack(">H", len(sof) + 2) + sof + b"\x00" * 64


def _is(image, rgb: tuple[int, int, int]) -> bool:  # noqa: ANN001
    color = QColor(image.pixel(image.width() // 2, image.height() // 2))
    found = (color.red(), color.green(), color.blue())
    return all(abs(a - b) < 40 for a, b in zip(found, rgb, strict=True))


def test_raw_and_psd_are_images() -> None:
    for name in ("a.CR3", "b.nef", "c.arw", "d.dng", "e.raf", "f.psd"):
        assert kind_for_name(name) is PreviewKind.IMAGE


def test_tiff_based_raw_shows_its_largest_embedded_jpeg_upright() -> None:
    # NEF-like: TIFF with a tiny thumbnail and orientation 6 (90 degrees
    # clockwise), a lossless sensor stream, a small and a large preview.
    data = (
        _tiff_header(6)
        + _fake_lossless_jpeg(6000, 4000)
        + _jpeg(160, 120, (0, 200, 0))
        + _jpeg(600, 400, (220, 30, 30))
    )
    image = decode_image(data, name="DSC_0001.NEF")
    assert (image.width(), image.height()) == (400, 600)  # rotated upright
    assert _is(image, (220, 30, 30))
    thumb = decode_image(data, QSize(240, 240), "DSC_0001.NEF")
    assert max(thumb.width(), thumb.height()) <= 240 and _is(thumb, (220, 30, 30))
    details = dict(file_details.image_details(data, image.width(), image.height(), raw=True))
    assert details["details.camera"] == "NIKON Z 6_2"
    assert "details.dimensions" not in details  # the preview size is not the sensor's


def test_cr3_reads_orientation_and_camera_from_its_cmt_boxes() -> None:
    tiff = _tiff_header(8, "Canon", "Canon EOS R5")
    cmt1 = struct.pack(">I", 8 + len(tiff)) + b"CMT1" + tiff
    uuid = struct.pack(">I", 24 + len(cmt1)) + b"uuid" + b"\x85\xc0\xb6\x87" * 4 + cmt1
    ftyp = struct.pack(">I", 24) + b"ftypcrx \x00\x00\x00\x01crx isom"
    preview = _jpeg(640, 480, (30, 30, 220))
    data = ftyp + uuid + struct.pack(">I", 8 + len(preview)) + b"mdat" + preview
    assert raw_decode.raw_orientation(data) == 8
    image = decode_image(data, name="IMG_0001.CR3")
    assert (image.width(), image.height()) == (480, 640)
    details = dict(file_details.image_details(data, 480, 640, raw=True))
    assert details["details.camera"] == "Canon EOS R5"


def test_a_raw_without_a_preview_gives_no_image() -> None:
    assert decode_image(_fake_lossless_jpeg(4000, 3000), name="x.cr2").isNull()


def _psd(width: int, height: int, rgb: tuple[int, int, int], thumb: bytes | None) -> bytes:
    header = b"8BPS" + struct.pack(">H6xHIIHH", 1, 3, height, width, 8, 3)
    resources = b""
    if thumb is not None:
        block = b"\x00" * 28 + thumb
        resources = b"8BIM" + struct.pack(">H", 1036) + b"\x00\x00"
        resources += struct.pack(">I", len(block)) + block + (b"\x00" if len(block) % 2 else b"")
    planes = b"".join(bytes([value]) * (width * height) for value in rgb)
    return (
        header
        + struct.pack(">I", 0)  # color mode data
        + struct.pack(">I", len(resources))
        + resources
        + struct.pack(">I", 0)  # layers and masks
        + struct.pack(">H", 0)  # raw image data
        + planes
    )


def test_psd_shows_its_composite_or_its_thumbnail() -> None:
    thumb = _jpeg(64, 48, (0, 200, 0))
    data = _psd(120, 80, (220, 30, 30), thumb)
    image = decode_image(data, name="poster.psd")
    assert (image.width(), image.height()) == (120, 80) and _is(image, (220, 30, 30))
    # When the composite cannot be read (here: cut off), the thumbnail.
    broken = data[: data.index(thumb) + len(thumb) + 8]
    fallback = decode_image(broken, name="poster.psd")
    assert (fallback.width(), fallback.height()) == (64, 48) and _is(fallback, (0, 200, 0))
