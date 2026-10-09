"""Regenerate the screenshots in docs/screenshots/ for the README.

Builds the synthetic demo archive, opens it in Archy offscreen at 2x scale and
grabs the showcase in the README: browsing, previews (video, code, Markdown,
CSV, HTML, Word, PowerPoint, Excel), document thumbnails, sorting on a
timestamp in the name, find and search, the extraction window, the unlock
dialog and the settings. Run it after UI changes:

    pip install -e ".[dev]" numpy
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

from archy.services import web_view  # noqa: E402

web_view.prepare()  # web pages through QtWebEngine, as in the app
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


def save(widget, name: str, width: int | None = None, paint=None, region=None, aspect=None) -> None:  # noqa: ANN001, E501
    """Grab ``widget``; ``region`` (widgets inside it) crops the shot to the
    area they cover, so a README tile shows the part that matters, readable;
    ``aspect`` caps its height (as a share of its width), cutting the empty
    bottom of a short file."""
    from PySide6.QtCore import QPoint, QRect

    pump(0.3)
    raw = Path(tempfile.mkdtemp()) / "shot.png"
    shot = widget.grab()
    if paint is not None:
        paint(shot)
    if region:
        area = QRect()
        for part in region:
            area = area.united(QRect(part.mapTo(widget, QPoint(0, 0)), part.size()))
        if aspect:
            area.setHeight(min(area.height(), round(area.width() * aspect)))
        ratio = shot.devicePixelRatio()
        shot = shot.copy(
            QRect(
                round(area.x() * ratio),
                round(area.y() * ratio),
                round(area.width() * ratio),
                round(area.height() * ratio),
            )
        )
    shot.save(str(raw))
    image = Image.open(raw).convert("RGBA")
    if width and image.width > width:
        image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
    image.save(OUT / name, optimize=True)
    print("saved", OUT / name)


def paint_frame(window, shot, path: str) -> None:  # noqa: ANN001
    """Paint the video's frame into the (black, offscreen) video widget."""
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtGui import QPainter
    from PySide6.QtMultimediaWidgets import QVideoWidget

    from archy.ui.thumbnail_loader import _decode_video_frame

    entry = next(e for e in window._backend.list_entries() if e.path == path)
    frame = _decode_video_frame(window._backend.read_bytes(entry))
    targets = [w for w in window.findChildren(QVideoWidget) if w.isVisible()]
    if frame is None or not targets:
        return
    target = targets[-1]
    # The painter works in logical pixels; the image carries the 2x ratio.
    ratio = shot.devicePixelRatio()
    area = QRect(target.mapTo(window, QPoint(0, 0)), target.size())
    picture = frame.scaled(area.size() * ratio, Qt.AspectRatioMode.KeepAspectRatio)
    picture.setDevicePixelRatio(ratio)
    width, height = picture.width() / ratio, picture.height() / ratio
    left = area.x() + round((area.width() - width) / 2)
    top = area.y() + round((area.height() - height) / 2)
    painter = QPainter(shot)
    painter.drawImage(QPoint(left, top), picture)
    painter.end()


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

    # The video, playing straight from the (stored) archive. The offscreen
    # platform cannot draw video, so the frame the player shows is painted
    # into the video area from the same file.
    video = "Foto's/2026-06 Dolomieten/timelapse_wolken.mp4"
    window._select_path(video)
    pump(3)
    panels = [window._file_panel, window._preview_panel]
    save(
        window,
        "preview-video.png",
        1600,
        paint=lambda shot: paint_frame(window, shot, video),
        region=panels,
    )

    view(0)  # List
    go("Scripts")
    window._select_path("Scripts/rename_photos.py")
    pump(2)
    save(window, "preview-code.png", 1920)

    # The document and data shots show the preview pane, made wider.
    sizes = window._splitter.sizes()
    window._splitter.setSizes([sizes[0], 470, sum(sizes) - sizes[0] - 470])
    pump(0.3)
    pane = [window._preview_panel]

    # Find in the file (Ctrl+F with the focus in the preview). The find bar
    # sits at the bottom of the pane, so the window is made lower instead of
    # cropping the shot.
    preview = window._preview_layout.itemAt(0).widget()
    preview.open_find()
    preview.find_bar.field.setText("photo")
    window.resize(1440, 520)
    pump(0.5)
    save(window, "find-in-file.png", 1100, region=pane)
    preview.find_bar.close_bar()
    window.resize(1440, 900)
    pump(0.3)

    go("Documenten")
    for path, name in (
        ("Documenten/reisplanning.md", "preview-markdown.png"),
        ("Documenten/uitgaven.csv", "preview-csv.png"),
        ("Documenten/reisverslag.docx", "preview-word.png"),
        ("Documenten/presentatie.pptx", "preview-powerpoint.png"),
    ):
        window._select_path(path)
        pump(2)
        save(window, name, 1100, region=pane, aspect=0.8)
    # The sheet tabs are at the bottom: a lower window instead of a crop.
    window.resize(1440, 640)
    window._select_path("Documenten/budget.xlsx")
    pump(2)
    save(window, "preview-excel.png", 1100, region=pane)
    window.resize(1440, 900)
    pump(0.3)

    go("Website")
    window._select_path("Website/index.html")
    pump(2)
    save(window, "preview-html.png", 1100, region=pane, aspect=0.8)
    window._splitter.setSizes(sizes)
    pump(0.3)

    # Documents get thumbnails too: a page of a PDF, Word or web page, the
    # picture Office saved, the first lines of a text file.
    view(2)
    go("Documenten")
    pump(4)
    window._select_path("Documenten/presentatie.pptx")
    pump(3)
    save(window, "browse-documents.png", 1600, region=panels)

    # Phone exports named after a Unix time: sorted on it, shown as a date.
    # The list gets the room for all its columns, so the name shows too.
    view(0)
    go("Foto's", "Telefoon")
    window._sort_by(6, False)
    window.resize(1700, 1110)  # about the shape of the documents shot next to it
    pump(0.3)
    total = sum(window._splitter.sizes())
    window._splitter.setSizes([sizes[0], 1110, total - sizes[0] - 1110])
    window._table.setColumnWidth(0, 300)  # the whole name
    pump(1)
    window._select_path("Foto's/Telefoon/1780720200-ontbijt_terras.jpg")
    pump(2)
    window._table.horizontalScrollBar().setValue(0)
    save(window, "sort-timestamp.png", 1600, region=panels)
    window._sort_by(0, False)
    window.resize(1440, 900)
    pump(0.3)
    window._splitter.setSizes(sizes)
    pump(0.3)

    # Search through the whole archive.
    go()
    window._focus_search("archive")
    window._search.setText("dolomieten")
    pump(1.5)
    window._select_path("Foto's/2026-06 Dolomieten/meer_bij_zonsopgang.jpg")
    pump(2)
    save(window, "search-archive.png", 1600, region=panels)
    window._search.clear()
    pump(0.5)

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
        # Only as tall as this tab needs (the pages scroll), no empty bottom.
        dlg.setMinimumSize(0, 0)
        dlg.resize(dlg.width(), 610)
        pump(0.3)
        save(dlg, "settings-temp-files.png")
        dlg.done(0)
        return 0

    original = soft_dialog.SoftDialog.exec
    soft_dialog.SoftDialog.exec = grab_settings
    try:
        window.open_settings_dialog()
    finally:
        soft_dialog.SoftDialog.exec = original

    # The dialog that explains unlocking an encrypted, stored backup.
    def grab_unlock(dlg) -> int:  # noqa: ANN001 - replaces SoftDialog.exec
        dlg.show()
        save(dlg, "unlock-dialog.png")
        dlg.done(0)
        return 0

    class _Profile:  # an encrypted 48 GB backup made with Store
        packed_size = 48 * 1024**3
        header_encrypted = True
        store_only = True

    soft_dialog.SoftDialog.exec = grab_unlock
    try:
        window._archive_label = "backup_2026.7z"
        window._confirm_unlock(_Profile(), 212 * 1024**3)
    finally:
        soft_dialog.SoftDialog.exec = original
    window.close()

    # The README shows these side by side: give each pair the same shape, so
    # the two stand level (the shorter one gets a transparent bottom).
    for pair in (
        ("preview-word.png", "preview-powerpoint.png"),
        ("preview-excel.png", "preview-csv.png"),
        ("browse-documents.png", "sort-timestamp.png"),
        ("find-in-file.png", "extract-dialog.png"),
        ("unlock-dialog.png", "settings-temp-files.png"),
    ):
        match_shapes(*pair)


def match_shapes(first: str, second: str) -> None:
    images = {name: Image.open(OUT / name).convert("RGBA") for name in (first, second)}
    aspect = max(image.height / image.width for image in images.values())
    for name, image in images.items():
        height = round(image.width * aspect)
        if height > image.height:
            padded = Image.new("RGBA", (image.width, height), (0, 0, 0, 0))
            padded.paste(image, (0, 0))
            padded.save(OUT / name, optimize=True)


if __name__ == "__main__":
    main()
