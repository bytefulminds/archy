from __future__ import annotations

from datetime import datetime

from archy.i18n import number

# Pure formatting helpers for the UI - no Qt, so they can be unit-tested
# headless and shared by the table model and preview widgets.


def format_size(num: int) -> str:
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{number(size, 1)} {unit}"
        size /= 1024
    return f"{num} B"


def format_modified(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M")


def format_ratio(size: int, compressed: int | None) -> str:
    """Compression ratio as a percentage of the original size (lower = smaller).

    Empty string when there is nothing meaningful to show (unknown compressed
    size, or an empty file).
    """
    if compressed is None or size <= 0:
        return ""
    return f"{compressed / size * 100:.0f}%"
