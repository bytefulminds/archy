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
    "av",
    "mammoth",
    "openpyxl",
    "pptx",
    "lxml.etree",
    "PySide6.QtWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtQuickWidgets",  # macOS: keeps the window from being rebuilt (main_window)
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


def _check_office() -> None:
    """An Excel sheet and a PowerPoint deck, made and previewed in memory
    (python-pptx needs its template files in the package)."""
    import openpyxl
    from pptx import Presentation

    from archy.services import office

    book = openpyxl.Workbook()
    book.active.append(["archy", 1])
    buffer = io.BytesIO()
    book.save(buffer)
    if office.excel_sheets(buffer.getvalue())[0].rows != [["archy", "1"]]:
        raise RuntimeError("Excel sheet did not read back")
    deck = Presentation()
    deck.slides.add_slide(deck.slide_layouts[0]).shapes.title.text = "archy"
    buffer = io.BytesIO()
    deck.save(buffer)
    if "archy" not in office.powerpoint_page(buffer.getvalue()).html:
        raise RuntimeError("PowerPoint slide did not read back")


def _check_video_thumbnail() -> None:
    """Encode a short video in memory and make a thumbnail of it the way the
    thumbnail workers do: without PyAV (or its FFmpeg libraries) in the
    package, videos silently get no thumbnail."""
    import av

    from archy.ui.thumbnail_loader import _decode_video_frame

    out = io.BytesIO()
    with av.open(out, "w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for i in range(12):
            frame = av.VideoFrame(64, 48, "rgb24")
            frame.planes[0].update(bytes([i * 20 % 256, 120, 200]) * (64 * 48))
            for packet in stream.encode(frame.reformat(format="yuv420p")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    image = _decode_video_frame(out.getvalue())
    if image is None or image.isNull() or (image.width(), image.height()) != (64, 48):
        raise RuntimeError("no frame decoded from a test video")


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


_WEB_PAGE = (
    '<html><head><link rel="stylesheet" href="css/site.css"></head><body>'
    '<header><h1>Archy</h1></header><div class="row"><p>alpha</p><p>beta</p></div>'
    '<img src="img/dot.png" alt=""></body></html>'
)
_WEB_CSS = (
    b"body { margin: 0; font-family: sans-serif; background: linear-gradient(#fff, #cde); }"
    b" header { box-shadow: 0 4px 12px rgba(0,0,0,.4); border-radius: 12px; padding: 8px; }"
    b" .row { display: flex; gap: 10px; transform: rotate(-2deg); opacity: .9; }"
    b" .row p { filter: blur(0.5px); backdrop-filter: blur(2px); }"
    b" h1 { text-shadow: 1px 1px 2px #000; transition: color .3s; }"
)


def _png() -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QColor, QImage

    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor("#45c0cd"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def run_web() -> int:
    """``--self-test-web``: show a styled page (stylesheet and image from the
    "archive") the way the preview does, keep it on screen a few seconds, and
    exit 0 when it loaded and Chromium kept running (run by the self-test in a
    child process)."""
    from archy.services import web_view

    def say(line: str) -> None:
        if sys.stdout is not None:  # a windowed Windows build may have none
            print(line, flush=True)

    if not web_view.prepare():
        say("QtWebEngine cannot be set up")
        return 2
    from archy.app import _application_class

    app = _application_class()(sys.argv[:1])  # the application class Archy runs with
    from archy.services import text_view
    from archy.services.providers import make_text_preview

    files = {"css/site.css": _WEB_CSS, "img/dot.png": _png()}
    asked: list[str] = []

    def read(reference: str) -> bytes | None:
        asked.append(reference)
        return files.get(reference)

    read.base = "site"  # type: ignore[attr-defined]
    text_view._show_source["html"] = False
    widget, _edit = make_text_preview("index.html", _WEB_PAGE, editable=False, resources=read)
    widget.resize(480, 320)
    widget.show()
    view = getattr(widget, "rendered", None)
    if view is None or not hasattr(view, "page"):
        say(f"no web view in the preview ({type(view).__name__})")
        return 1
    loaded: list[bool] = []
    ended: list[str] = []
    view.loadFinished.connect(loaded.append)
    view.page().renderProcessTerminated.connect(
        lambda status, code: ended.append(f"{status.name} ({code})")
    )

    def pump(seconds: float, until: Callable[[], bool] = lambda: False) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end and not until() and not ended:
            app.processEvents()
            time.sleep(0.01)

    pump(60, lambda: bool(loaded))
    pump(3.0)  # stay on screen: compositing and the GPU process get going
    if ended:
        say(f"Chromium's page process ended: {ended[0]}")
        return 1
    if not (loaded and loaded[0]):
        say("page did not load")
        return 1
    if "css/site.css" not in asked:
        say(f"the stylesheet was not asked for ({asked})")
        return 1
    say("page loaded and shown")
    widget.close()
    app.aboutToQuit.emit()  # what quitting the app does: pages before the profile
    app.processEvents()
    return 0


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
        ("Office previews", _check_office),
        ("video thumbnail", _check_video_thumbnail),
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
