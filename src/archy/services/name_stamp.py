from __future__ import annotations

import re

# A Unix time at the start of a file name, as some cameras, apps and exports
# write it: "1696512000--beach.jpg", "1696512000123_IMG_0042.jpg". Sorting by
# name puts those in a muddle (a shorter or longer number sorts wrongly), so
# Archy can sort on the time itself (see archive_model's "Timestamp in name").
#
# The number is followed by "--", "_" or the extension's dot (or ends the
# name). Seconds (9 or 10 digits), milliseconds (13), microseconds (16) or
# nanoseconds (19); anything else, or a time outside 1973-2100, is not taken
# for one (a 20231005 date or a camera counter stays a name).

_LEADING = re.compile(r"(\d+)(?:--|_|\.|$)")
_DIVISOR = {9: 1, 10: 1, 13: 1_000, 16: 1_000_000, 19: 1_000_000_000}
_EARLIEST = 100_000_000  # March 1973
_LATEST = 4_102_444_800  # 2100


def name_timestamp(name: str) -> float | None:
    """The Unix time (seconds) a file name starts with, or None."""
    match = _LEADING.match(name)
    if match is None:
        return None
    digits = match.group(1)
    divisor = _DIVISOR.get(len(digits))
    if divisor is None:
        return None
    seconds = int(digits) / divisor
    if not _EARLIEST <= seconds < _LATEST:
        return None
    return seconds
