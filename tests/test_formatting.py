from __future__ import annotations

from datetime import datetime

from archy.services.formatting import format_modified, format_ratio, format_size


def test_format_size() -> None:
    assert format_size(0) == "0 B"
    assert format_size(512) == "512 B"
    assert format_size(1536) == "1.5 KB"
    assert format_size(5 * 1024 * 1024) == "5.0 MB"


def test_format_modified() -> None:
    assert format_modified(None) == ""
    assert format_modified(datetime(2026, 9, 16, 14, 5)) == "2026-09-16 14:05"


def test_format_ratio() -> None:
    assert format_ratio(1000, 400) == "40%"
    assert format_ratio(0, 0) == ""
    assert format_ratio(100, None) == ""
    assert format_ratio(200, 200) == "100%"
