"""Regenerate the screenshots in docs/screenshots/ for the README.

Builds the synthetic demo archive, opens it in Archy offscreen at 2x scale and
grabs the main window, the extraction window and the settings. Run it after UI
changes:

    pip install numpy pillow
    python scripts/readme_screenshots/take_screenshots.py
    ARCHY_SCREENSHOT_LANG=nl python scripts/readme_screenshots/take_screenshots.py

The extraction window is filled with illustrative numbers (a 48 GB backup), so
it does not need a real long-running extraction.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "docs" / "screenshots"

_home = tempfile.mkdtemp(prefix="archy-shots-")
os.environ["HOME"] = _home  # fresh, default settings
os.environ["XDG_CONFIG_HOME"] = _home
# The offscreen screen is 800x800 by default (400x400 at scale 2), which makes
# dialogs size themselves for a tiny screen; describe a normal desktop instead.
_screen = Path(_home) / "offscreen-screen.json"
_screen.write_text(
    '{"screens": [{"name": "shots", "x": 0, "y": 0, "width": 3200, "height": 2000,'
    ' "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1}]}'
)
os.environ.setdefault("QT_QPA_PLATFORM", f"offscreen:configfile={_screen}")
os.environ.setdefault("QT_SCALE_FACTOR", "2")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolButton  # noqa: E402

app = QApplication([])
QCoreApplication.setOrganizationName("ArchyScreenshots")
QCoreApplication.setApplicationName("ArchyScreenshots")

from make_demo_archive import build  # noqa: E402

import archy.ui.soft_dialog as soft_dialog  # noqa: E402
from archy import i18n  # noqa: E402
from archy.ui import theme  # noqa: E402
from archy.ui.extract_dialog import ExtractProgressDialog  # noqa: E402
from archy.ui.main_window import MainWindow  # noqa: E402

# The README is English; ARCHY_SCREENSHOT_LANG=nl renders the Dutch UI instead.
i18n.set_language(os.environ.get("ARCHY_SCREENSHOT_LANG", "en"))
theme.apply(app)


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        time.sleep(0.01)


def save(widget, name: str, width: int | None = None) -> None:  # noqa: ANN001
    pump(0.3)
    raw = Path(tempfile.mkdtemp()) / "shot.png"
    widget.grab().save(str(raw))
    image = Image.open(raw).convert("RGBA")
    if width and image.width > width:
        image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
    image.save(OUT / name, optimize=True)
    print("saved", OUT / name)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    archive = build(Path(_home) / "demo.7z")
    window = MainWindow()
    window.resize(1440, 900)
    window.show()
    window.open_archive(archive)
    while window.is_opening:  # the index is read on a worker thread
        pump(0.1)
    pump(1.0)

    def go(*parts: str) -> None:
        del window._nav[1:]
        node = window._nav[-1]
        for part in parts:
            node = node.children[part]
            window._nav.append(node)
        window._refresh_folder()
        pump(0.5)

    def view(index: int) -> None:
        window._view_group.button(index).setChecked(True)
        window._switch_view(index)
        pump(0.3)

    view(2)  # Miniaturen
    go("Foto's", "2026-06 Dolomieten")
    pump(4)
    window._select_path("Foto's/2026-06 Dolomieten/zonsondergang_tre_cime.jpg")
    pump(3)
    save(window, "browse-thumbnails.png", 1920)

    view(0)  # Lijst
    go("Scripts")
    window._select_path("Scripts/rename_photos.py")
    pump(2)
    save(window, "preview-code.png", 1920)

    total = 48 * 1024**3
    dialog = ExtractProgressDialog(window, "backup_2026.7z", "/Users/demo/Restore", 101_648, total)
    dialog.show()
    start = int(total * 0.37)
    for step in range(8):
        name = "Foto's/2024/Portfolio/RAW/IMG_4821.CR3"
        dialog.update_progress(start + step * 60 * 1024**2, total, name, 37_512 + step * 9)
        pump(0.25)

    class _Elapsed:  # pretend the extraction has been running for a while
        def elapsed(self) -> int:
            return 79_000

    dialog._elapsed = _Elapsed()
    dialog._refresh_numbers()
    save(dialog, "extract-dialog.png")
    dialog._finished = True
    dialog.close()

    def grab_settings(dlg) -> int:  # noqa: ANN001 - replaces SoftDialog.exec
        dlg.show()
        pump(0.5)
        tabs = [b for b in dlg.findChildren(QToolButton) if b.objectName() == "viewButton"]
        tabs[1].click()  # Temporary files
        save(dlg, "settings-temp-files.png")
        dlg.done(0)
        return 0

    original = soft_dialog.SoftDialog.exec
    soft_dialog.SoftDialog.exec = grab_settings
    try:
        window.open_settings_dialog()
    finally:
        soft_dialog.SoftDialog.exec = original
    window.close()


if __name__ == "__main__":
    main()
