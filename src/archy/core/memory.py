from __future__ import annotations

from dataclasses import dataclass

# One memory policy for the whole app, measured live and tunable.
#
# Archy keeps things in RAM on purpose (a whole folder, thumbnails, a video
# being previewed). Every budget used to be "a share of free RAM", measured once
# and independently - the shares added up to well over 100%, and a budget fixed
# at startup did not shrink once the folder buffer had filled memory. On a
# 16 GB Windows machine that could exhaust the system's memory, and when that
# happens Windows' own compositor fails and the whole screen goes black.
#
# The rules here:
# - A RESERVE is never touched: by default the larger of 2 GB and 20% of total
#   RAM stays free for the OS, its compositor and other apps.
# - Archy as a whole stays under a CAP: by default 75% of total RAM (measured
#   as the process's own memory use), or a fixed amount.
# - What Archy may still take ("spare") is the smaller of the two headrooms,
#   measured NOW, so every budget shrinks as memory fills up.
# - Callers that hold several copies of the same bytes while working (a 7z read
#   keeps two briefly, the video player takes a third) say so, and the check
#   accounts for every copy.
# - Each buffer can be left on Auto (a share of spare) or set to a fixed size
#   in Settings > Advanced (see Limits). Fixed sizes are still subject to
#   the live reserve/cap check; anything that does not fit goes to the
#   encrypted temp store when temp files are enabled, or is not kept.

_MB = 1024 * 1024
_GB = 1024 * _MB
_RESERVE_MIN = 2 * _GB
_RESERVE_FRACTION = 0.20
_FALLBACK_TOTAL = 8 * _GB  # psutil missing: assume a modest machine
_FALLBACK_AVAILABLE = 2 * _GB


@dataclass
class Limits:
    """User-tunable limits. ``None`` means Auto."""

    cap_percent: int = 75  # Archy's own total memory, as % of RAM (Auto mode)
    cap_bytes: int | None = None  # a fixed total instead of the percentage
    reserve_bytes: int | None = None  # always left free for the OS
    folder_buffer_bytes: int | None = None  # the folder pass keeps up to this in RAM
    thumb_cache_bytes: int | None = None
    read_cache_bytes: int | None = None
    video_memory_bytes: int | None = None  # larger videos go via temp file / refused
    threads: int | None = None  # thumbnail worker threads


_limits = Limits()


def configure(limits: Limits) -> None:
    """Apply user limits (Settings > Advanced). Takes effect immediately:
    every budget below is computed on demand."""
    global _limits
    _limits = limits


def limits() -> Limits:
    return _limits


# ------------------------------------------------------------ measurements
def total() -> int:
    try:
        import psutil

        return int(psutil.virtual_memory().total)
    except Exception:  # noqa: BLE001 - psutil missing/unavailable
        return _FALLBACK_TOTAL


def available() -> int:
    try:
        import psutil

        return int(psutil.virtual_memory().available)
    except Exception:  # noqa: BLE001 - psutil missing/unavailable
        return _FALLBACK_AVAILABLE


_process = None


def process_rss() -> int:
    """Archy's own memory use right now."""
    global _process
    try:
        import psutil

        if _process is None:
            _process = psutil.Process()
        return int(_process.memory_info().rss)
    except Exception:  # noqa: BLE001 - psutil missing/unavailable
        return 0


# ----------------------------------------------------------------- policy
def reserve() -> int:
    """Memory that is always left for the OS and other apps."""
    if _limits.reserve_bytes is not None:
        return max(0, int(_limits.reserve_bytes))
    return max(_RESERVE_MIN, int(total() * _RESERVE_FRACTION))


def cap() -> int:
    """The most Archy as a whole may use."""
    if _limits.cap_bytes is not None:
        return max(0, int(_limits.cap_bytes))
    percent = min(95, max(10, int(_limits.cap_percent)))
    return int(total() * percent / 100)


def spare() -> int:
    """What Archy may still take right now: within its cap, and without
    touching the OS reserve."""
    by_system = available() - reserve()
    by_cap = cap() - process_rss()
    return max(0, min(by_system, by_cap))


def fits(size: int, copies: int = 1) -> bool:
    """True when holding ``copies`` copies of ``size`` bytes stays within the
    cap and leaves the reserve intact."""
    return int(size) * max(1, copies) <= spare()


def budget(fraction: float, *, floor: int, ceiling: int) -> int:
    """A share of what is spare right now, bounded to [floor, ceiling]. The
    floor keeps the app usable on a busy machine; it is small on purpose."""
    return int(min(ceiling, max(floor, spare() * fraction)))


def fixed_or(value: int | None, auto: int) -> int:
    """A user-fixed size, or the Auto value."""
    return int(value) if value is not None else int(auto)


def worker_threads(ideal: int) -> int:
    """Thumbnail worker threads: fixed, or Auto (cores minus two, 2..6)."""
    if _limits.threads is not None:
        return max(1, min(32, int(_limits.threads)))
    return max(2, min(6, int(ideal) - 2))
