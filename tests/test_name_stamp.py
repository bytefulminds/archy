"""A Unix time at the start of a file name (services/name_stamp.py)."""

from __future__ import annotations

import pytest

from archy.services.name_stamp import name_timestamp


@pytest.mark.parametrize(
    ("name", "seconds"),
    [
        ("1696512000--beach.jpg", 1696512000),
        ("1696512000_IMG_0042.jpg", 1696512000),
        ("1696512000.jpg", 1696512000),
        ("1696512000", 1696512000),
        ("999999999--old.jpg", 999999999),  # 9 digits: 2001
        ("1696512000123--ms.jpg", 1696512000.123),
        ("1696512000123456_us.mov", 1696512000.123456),
        ("1696512000123456789_ns.txt", 1696512000.123456789),
    ],
)
def test_reads_the_time(name: str, seconds: float) -> None:
    assert name_timestamp(name) == pytest.approx(seconds)


@pytest.mark.parametrize(
    "name",
    [
        "beach.jpg",
        "IMG_0042.jpg",  # not at the start
        "20231005_party.jpg",  # a date, not a Unix time (8 digits)
        "0042_IMG.jpg",
        "1696512000-beach.jpg",  # one dash: not a separator Archy takes
        "16965120001_x.jpg",  # 11 digits
        "5000000000_future.jpg",  # past 2100
        "12345678901234_x.jpg",
    ],
)
def test_other_names_have_none(name: str) -> None:
    assert name_timestamp(name) is None
