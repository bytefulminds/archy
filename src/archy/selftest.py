"""``archy --self-test``: check that an installed or packaged Archy has
everything it needs, without opening a window.

The build scripts run it on the freshly built bundle, so a package that would
fail on a user's machine (a module PyInstaller left out, a missing resource
folder) stops the build instead. It imports the libraries Archy loads lazily,
round-trips a small 7z and AES zip in memory, checks the resource folders, and
renders a small HTML page with QtWebEngine in a separate process (Chromium's
helper process is the part of a package most likely to be broken, and a broken
one takes the whole app down; in a child process that shows up as a failed
check with its output instead). Nothing is written to disk. Exit code 0 when
all is well, 1 otherwise.
"""

from __future__ import annotations

import importlib
import io
import os
import subprocess
import sys
import time
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


def _check_web_in_child() -> None:
    """Run ``--self-test-web`` in a child process and report how it ended."""
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--self-test-web"]
    else:
        command = [sys.executable, "-m", "archy", "--self-test-web"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("no answer within 120 s") from exc
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip().splitlines()
        # The cause (a Qt or Chromium message) comes first, a crash trace last.
        shown = output if len(output) <= 30 else [*output[:12], "...", *output[-15:]]
        tail = "\n        ".join(shown) or "(no output)"
        raise RuntimeError(f"exit code {result.returncode}:\n        {tail}")


def run_web() -> int:
    """``--self-test-web``: render a small page with QtWebEngine and exit 0
    when it loaded (run by the self-test in a child process)."""
    from archy.services import web_view

    def say(line: str) -> None:
        if sys.stdout is not None:  # a windowed Windows build may have none
            print(line, flush=True)

    if not web_view.prepare():
        say("QtWebEngine cannot be set up")
        return 2
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    view = web_view.make_web_view("check.html", None)
    loaded: list[bool] = []
    view.loadFinished.connect(loaded.append)  # type: ignore[attr-defined]
    view.resize(320, 200)
    view.show()
    view.set_html("<html><body><p>Archy</p></body></html>")  # type: ignore[attr-defined]
    deadline = time.monotonic() + 60
    while not loaded and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    ok = bool(loaded and loaded[0])
    say("page loaded" if ok else "page did not load")
    view.close()
    view.deleteLater()
    app.processEvents()
    return 0 if ok else 1


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
    if not os.environ.get("ARCHY_NO_WEBENGINE"):
        checks.append(("HTML page with QtWebEngine", _check_web_in_child))
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
