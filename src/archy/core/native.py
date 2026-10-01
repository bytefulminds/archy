from __future__ import annotations

import ctypes.util
import os
import sys
from pathlib import Path

# libarchive-c does NOT bundle the native libarchive C library; it loads it at
# import time via ctypes. The library must therefore be present on the system
# and discoverable. On most Linux distros and via the LIBARCHIVE env var this
# just works. macOS is the awkward case: Homebrew's libarchive is keg-only, so
# `ctypes.util.find_library('archive')` usually fails to find it. We locate the
# Homebrew dylib ourselves and point the LIBARCHIVE env var at it.
#
# Always call ensure_libarchive_env() BEFORE `import libarchive`.

_LIBARCHIVE_ENV = "LIBARCHIVE"

# Common Homebrew locations for the libarchive dylib (Apple Silicon and Intel).
_MACOS_CANDIDATES: tuple[str, ...] = (
    "/opt/homebrew/opt/libarchive/lib/libarchive.dylib",
    "/opt/homebrew/lib/libarchive.dylib",
    "/usr/local/opt/libarchive/lib/libarchive.dylib",
    "/usr/local/lib/libarchive.dylib",
)

_ensured = False


def _find_bundled_lib() -> str | None:
    """When the app is frozen (PyInstaller), the native libarchive is shipped
    inside the bundle next to the other libraries (see packaging/archy.spec,
    which adds it at the bundle root). Return its path so we can point LIBARCHIVE
    at it instead of relying on a system install. Covers all three OSes.
    """
    if not getattr(sys, "frozen", False):
        return None
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    root = Path(base)
    names = (
        "libarchive.dll",  # Windows
        "libarchive.dylib",  # macOS
        "libarchive.so",  # Linux (unversioned)
        "libarchive.so.13",  # Linux (versioned)
    )
    for name in names:
        candidate = root / name
        if candidate.exists():
            return str(candidate)
    # Last resort: any versioned libarchive.* the spec may have bundled.
    for candidate in sorted(root.glob("libarchive*")):
        if candidate.is_file():
            return str(candidate)
    return None


def _find_macos_dylib() -> str | None:
    # Prefer an explicit `brew --prefix libarchive` if brew is on PATH; fall
    # back to the well-known paths above.
    import shutil
    import subprocess

    brew = shutil.which("brew")
    if brew is not None:
        try:
            prefix = subprocess.run(
                [brew, "--prefix", "libarchive"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            prefix = ""
        if prefix:
            candidate = Path(prefix) / "lib" / "libarchive.dylib"
            if candidate.exists():
                return str(candidate)

    for path in _MACOS_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def ensure_libarchive_env() -> None:
    """Make the native libarchive discoverable before importing libarchive.

    Idempotent. Safe to call repeatedly. On macOS it sets the LIBARCHIVE env
    var to the Homebrew dylib when it is not already set and cannot be found by
    the normal ctypes lookup. On other platforms it is essentially a no-op when
    the library is already reachable.
    """
    global _ensured
    if _ensured:
        return

    # An explicit override always wins - never second-guess the user.
    if os.environ.get(_LIBARCHIVE_ENV):
        _ensured = True
        return

    # A frozen (packaged) app ships its own libarchive; prefer that so it is
    # self-contained and needs no system install on the user's machine.
    bundled = _find_bundled_lib()
    if bundled is not None:
        os.environ[_LIBARCHIVE_ENV] = bundled
        _ensured = True
        return

    if sys.platform == "darwin":
        # Does ctypes already find it? If so, leave things alone.
        if ctypes.util.find_library("archive") is None:
            dylib = _find_macos_dylib()
            if dylib is not None:
                os.environ[_LIBARCHIVE_ENV] = dylib

    _ensured = True


_available: bool | None = None


def libarchive_available() -> bool:
    """True if the native libarchive can actually be loaded on this system.

    libarchive is optional: zip/7z have their own backends and tar.* is read via
    the stdlib TarBackend, so the app runs fine without it (only rar and a few
    exotic formats need it). This lets the factory decide up front whether to use
    the libarchive backend or report the format as unsupported here. Cached, and
    never raises.
    """
    global _available
    if _available is None:
        ensure_libarchive_env()
        try:
            import libarchive  # noqa: F401

            _available = True
        except Exception:  # noqa: BLE001 - any import/load failure means unavailable
            _available = False
    return _available
