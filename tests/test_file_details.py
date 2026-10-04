"""The details under a file's name in the preview (services/file_details.py)."""

from __future__ import annotations

import io

import pytest

from archy.services import file_details


def _jpeg_with_exif() -> bytes:
    pil = pytest.importorskip("PIL.Image")
    from PIL.TiffImagePlugin import IFDRational

    exif = pil.Exif()
    exif[271] = "SONY"
    exif[272] = "ILCE-7M4"
    ifd = exif.get_ifd(0x8769)
    ifd[42036] = "FE 24-70mm F2.8 GM II"
    ifd[37386] = IFDRational(35)
    ifd[33437] = IFDRational(56, 10)
    ifd[33434] = IFDRational(1, 250)
    ifd[34855] = 200
    ifd[36867] = "2026:06:04 05:42:10"
    out = io.BytesIO()
    pil.new("RGB", (60, 40), "orange").save(out, "JPEG", exif=exif)
    return out.getvalue()


def test_a_photo_shows_pixels_camera_settings_and_date() -> None:
    details = file_details.image_details(_jpeg_with_exif(), 60, 40)
    assert dict(details) == {
        "details.dimensions": "60 × 40 px",
        "details.camera": "Sony ILCE-7M4",
        "details.lens": "FE 24-70mm F2.8 GM II",
        "details.exposure": "35 mm, f/5.6, 1/250 s, ISO 200",
        "details.taken": "2026-06-04 05:42",
    }
    # The lens is for the file info; the line under the name stays short.
    assert file_details.subtitle(details) == (
        "60 × 40 px · Sony ILCE-7M4 · 35 mm, f/5.6, 1/250 s, ISO 200 · 2026-06-04 05:42"
    )


def test_an_image_without_exif_shows_its_size_only() -> None:
    assert file_details.image_details(b"not an image", 10, 20) == [
        ("details.dimensions", "10 × 20 px")
    ]


def test_camera_name_is_not_doubled() -> None:
    assert file_details._camera("Canon", "Canon EOS R5") == "Canon EOS R5"
    assert file_details._camera("NIKON CORPORATION", "Z 6_2") == "Nikon Z 6_2"
    assert file_details._camera("", "") == ""


def test_text_lines_and_encoding() -> None:
    assert file_details.text_details("a\nb\n", "utf-8") == [
        ("details.lines", "2 lines"),
        ("details.encoding", "UTF-8"),
    ]
    assert file_details.text_details("one", "cp1252")[0] == ("details.lines", "1 line")
    # A preview of only the start of a file does not claim a line count.
    assert file_details.text_details("a\nb", "utf-8", partial=True) == [
        ("details.encoding", "UTF-8")
    ]


def test_durations_and_shutter_speeds() -> None:
    assert file_details.duration(42_000) == "0:42"
    assert file_details.duration(3_729_000) == "1:02:09"
    assert file_details._shutter(0.004) == "1/250 s"
    assert file_details._shutter(2.5) == "2.5 s"
