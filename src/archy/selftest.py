"""``archy --self-test``: check that an installed or packaged Archy has
everything it needs, without opening a window.

The build scripts run it on the freshly built bundle, so a package that would
fail on a user's machine (a module PyInstaller left out, a missing resource
folder) stops the build instead. It imports the libraries Archy loads lazily,
round-trips a small 7z and AES zip in memory, and checks the resource folders.
Nothing is written to disk. Exit code 0 when all is well, 1 otherwise.
"""

from __future__ import annotations

import importlib
import io
import sys
from collections.abc import Callable
from pathlib import Path

# Imported lazily by Archy (inside functions), so a packager can miss them.
MODULES = (
    "py7zr",
    "py7zr.py7zr",
    "py7zr.archiveinfo",
    "py7zr.compressor",
    "py7zr.io",
    "pyzipper",
    "Cryptodome.Cipher.AES",
    "charset_normalizer",
    "psutil",
    "PIL.Image",
    "pi_heif",
    "PySide6.QtWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "archy.core.backends.sevenzip_backend",
    "archy.core.backends.zip_backend",
    "archy.core.backends.tar_backend",
    "archy.core.backends.nested_backend",
    "archy.services.raw_decode",
    "archy.ui.main_window",
)

RESOURCES = ("fonts", "icons", "i18n", "licenses")


def _check_7z() -> None:
    import py7zr

    buffer = io.BytesIO()
    with py7zr.SevenZipFile(buffer, "w") as archive:
        archive.writestr(b"archy", "check.txt")
    buffer.seek(0)
    with py7zr.SevenZipFile(buffer, "r") as archive:
        names = archive.getnames()
    if names != ["check.txt"]:
        raise RuntimeError(f"unexpected 7z contents: {names}")


def _check_aes_zip() -> None:
    import pyzipper

    buffer = io.BytesIO()
    with pyzipper.AESZipFile(buffer, "w", encryption=pyzipper.WZ_AES) as archive:
        archive.setpassword(b"check")
        archive.writestr("check.txt", b"archy")
    with pyzipper.AESZipFile(io.BytesIO(buffer.getvalue())) as archive:
        archive.setpassword(b"check")
        if archive.read("check.txt") != b"archy":
            raise RuntimeError("AES zip did not round-trip")


def _check_resources() -> None:
    base = Path(__file__).resolve().parent / "resources"
    missing = [name for name in RESOURCES if not any((base / name).glob("*"))]
    if missing:
        raise RuntimeError(f"missing resources: {', '.join(missing)} (in {base})")


def _libarchive() -> str:
    """Optional (rar and exotic formats): reported, not required."""
    from archy.core.native import ensure_libarchive_env, libarchive_available

    if not libarchive_available():
        return "not available (rar and some formats unsupported)"
    ensure_libarchive_env()
    import libarchive  # noqa: F401

    return "ok"


def _attach_windows_console() -> None:
    """A windowed Windows build has no stdout: write to the console it was
    started from (``start /wait dist\\Archy\\Archy.exe --self-test``)."""
    if sys.platform != "win32" or sys.stdout is not None:
        return
    try:
        import ctypes

        if ctypes.windll.kernel32.AttachConsole(-1):  # type: ignore[attr-defined]  # parent console
            sys.stdout = open("CONOUT$", "w", encoding="utf-8")  # noqa: SIM115
    except Exception:  # noqa: BLE001 - no console: the exit code still tells
        pass


def run(out: Callable[[str], None] | None = None) -> int:
    if out is None:
        _attach_windows_console()

    def say(line: str) -> None:
        if out is not None:
            out(line)
        elif sys.stdout is not None:  # a windowed Windows build has no stdout
            print(line, flush=True)

    failed = 0
    checks: list[tuple[str, Callable[[], object]]] = [
        *((f"import {name}", lambda name=name: importlib.import_module(name)) for name in MODULES),
        ("7z round trip", _check_7z),
        ("AES zip round trip", _check_aes_zip),
        ("resources", _check_resources),
    ]
    for label, check in checks:
        try:
            check()
            say(f"ok    {label}")
        except Exception as exc:  # noqa: BLE001 - report every failure
            failed += 1
            say(f"FAIL  {label}: {type(exc).__name__}: {exc}")
    try:
        say(f"info  libarchive: {_libarchive()}")
    except Exception as exc:  # noqa: BLE001
        say(f"info  libarchive: {type(exc).__name__}: {exc}")
    say("self-test passed" if not failed else f"self-test FAILED ({failed})")
    return 1 if failed else 0
