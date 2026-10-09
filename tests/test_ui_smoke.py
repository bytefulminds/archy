from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# A lightweight safety net for the UI layer, which has no other test coverage.
# It boots a QApplication, applies the theme, checks that a runtime accent
# change is actually reflected (guards the theme package's live-token wiring),
# and constructs the whole MainWindow once (exercises every _build_* path and
# every signal connection). It does not assert on pixels - it only proves the
# app assembles and re-themes without raising.

# Skip the whole module when the Qt GUI stack cannot be imported - e.g. a
# headless CI image without libGL. importorskip does not skip on this kind of
# nested ImportError (a missing system .so, not a missing module), so import it
# explicitly and skip at collection time on any failure.
try:
    from PySide6.QtWidgets import QApplication as _QApplication  # noqa: F401
except Exception as _exc:  # noqa: BLE001 - missing Qt libs/display -> skip, don't fail
    pytest.skip(f"PySide6 GUI not available: {_exc}", allow_module_level=True)


def _app():  # noqa: ANN202
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _open(window, archive, timeout: float = 20.0) -> None:  # noqa: ANN001
    """Open an archive and wait for the worker thread to finish reading it."""
    import time

    from PySide6.QtWidgets import QApplication

    window.open_archive(archive)
    deadline = time.monotonic() + timeout
    while window.is_opening and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    QApplication.processEvents()
    assert not window.is_opening


def test_theme_applies_and_accent_is_live() -> None:
    from archy.ui import theme

    app = _app()
    theme.apply(app)
    before = theme.ACCENT
    theme.set_accent("#FF0000", "#00FF00")
    assert theme.ACCENT == "#FF0000"
    assert before != theme.ACCENT
    assert theme.ACCENT_RGB == (255, 0, 0)
    # restore the default so other tests/renders see the normal palette
    theme.set_accent("#45C0CD", "#CD7E45")


def test_main_window_constructs() -> None:
    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.show()
    window.close()


def test_thumbnail_view_loads_offscreen_thumbnails_from_solid_7z(tmp_path) -> None:
    # Opening a solid 7z in a thumbnail view should start the folder pass right
    # away and make every thumbnail during it - not only the visible tiles -
    # with one indicator that finishes when they are all ready.
    import io
    import time

    py7zr = pytest.importorskip("py7zr")
    from PySide6.QtGui import QColor, QImage

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    archive = tmp_path / "photos.7z"
    with py7zr.SevenZipFile(str(archive), "w") as out:
        for i in range(60):
            image = QImage(64, 48, QImage.Format.Format_RGB32)
            image.fill(QColor(i * 4, 90, 160))
            buf = io.BytesIO()
            from PySide6.QtCore import QBuffer, QByteArray

            data = QByteArray()
            qbuf = QBuffer(data)
            qbuf.open(QBuffer.OpenModeFlag.WriteOnly)
            image.save(qbuf, "PNG")
            buf.write(bytes(data))
            out.writef(io.BytesIO(buf.getvalue()), f"img{i:03d}.png")

    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.resize(900, 600)
    window.show()
    progress: list[tuple[int, int]] = []
    finished: list[bool] = []
    window._prefetcher.progress.connect(lambda d, t: progress.append((d, t)))
    window._prefetcher.done.connect(lambda: finished.append(True))
    _open(window, archive)
    window._view_group.button(2).setChecked(True)
    window._switch_view(2)

    paths = [e.path for e in window._display_folder_files()]
    assert len(paths) == 60
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        app.processEvents()
        if all(p in window._thumbs._cache for p in paths):
            break
        time.sleep(0.01)
    missing = [p for p in paths if p not in window._thumbs._cache]
    assert not missing, f"{len(missing)} thumbnails never loaded"
    deadline = time.monotonic() + 5
    while not finished and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    # One folder pass made every thumbnail, and its indicator counted them all.
    assert finished
    assert progress and progress[-1] == (60, 60)
    dones = [done for done, _total in progress]
    assert dones == sorted(dones)
    window.close()


def test_image_thumbnail_gate_uses_uncompressed_size() -> None:
    # On a solid 7z the first entry of a block reports the whole block as its
    # compressed size; that must not rule out its thumbnail.
    from archy.core.backend import ArchiveEntry, EntryType
    from archy.ui.thumbnail_loader import ThumbnailLoader

    _app()
    loader = ThumbnailLoader()
    first = ArchiveEntry(
        path="photos/IMG_0000.jpg",
        type=EntryType.FILE,
        size=5 * 1024 * 1024,
        compressed_size=400 * 1024 * 1024,
    )
    assert loader._is_thumbnailable(first)


def test_image_thumbnail_is_decoded_to_thumbnail_size() -> None:
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QColor, QImage

    from archy.ui.thumbnail_loader import _THUMB_SIZE, _decode_image_thumb

    _app()
    for width, height in ((2400, 1600), (100, 60)):
        source = QImage(width, height, QImage.Format.Format_RGB32)
        source.fill(QColor(20, 120, 200))
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QBuffer.OpenModeFlag.WriteOnly)
        source.save(buffer, "JPG")
        image = _decode_image_thumb(bytes(data))
        assert not image.isNull()
        assert max(image.width(), image.height()) == min(max(width, height), _THUMB_SIZE)


def test_thumbnail_view_loads_whole_zip_folder(tmp_path) -> None:
    # A zip needs no folder pass (every file is cheap to read on its own); the
    # loader fills the whole folder itself while a thumbnail view is showing.
    import time
    import zipfile

    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QColor, QImage

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    archive = tmp_path / "photos.zip"
    with zipfile.ZipFile(archive, "w") as out:
        for i in range(50):
            image = QImage(64, 48, QImage.Format.Format_RGB32)
            image.fill(QColor(i * 5, 60, 120))
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QBuffer.OpenModeFlag.WriteOnly)
            image.save(buffer, "PNG")
            out.writestr(f"img{i:03d}.png", bytes(data))

    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.resize(900, 600)
    window.show()
    _open(window, archive)
    window._view_group.button(2).setChecked(True)
    window._switch_view(2)
    paths = [e.path for e in window._display_folder_files()]
    assert len(paths) == 50
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        app.processEvents()
        if all(p in window._thumbs._cache for p in paths):
            break
        time.sleep(0.01)
    assert all(p in window._thumbs._cache for p in paths)
    window.close()


def test_app_icon_resolves_for_every_platform(monkeypatch) -> None:
    # The Dock/taskbar icon ships inside the package; both variants must exist.
    from archy import app as app_module

    for platform in ("darwin", "win32", "linux"):
        monkeypatch.setattr(app_module.sys, "platform", platform)
        path = app_module._app_icon_path()
        assert path.exists(), f"missing app icon for {platform}: {path}"
    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    assert app_module._app_icon_path().name == "archy_macos.png"


def test_disk_mode_keeps_folder_and_video_encrypted(tmp_path, monkeypatch) -> None:
    # "Always use temporary files": the folder pass keeps its files in the
    # encrypted store (not RAM), thumbnails still all appear, a video plays
    # through the decrypting device, and nothing readable is left on disk.
    import io
    import time
    from dataclasses import dataclass

    py7zr = pytest.importorskip("py7zr")
    av = pytest.importorskip("av")
    import numpy as np
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QColor, QImage

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    # A tiny real video (PyAV) plus a few images, solid 7z.
    video = io.BytesIO()
    with av.open(video, "w", format="mp4") as out:
        stream = out.add_stream("mpeg4", rate=25)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for i in range(25):
            frame = av.VideoFrame.from_ndarray(
                np.full((48, 64, 3), i * 8, np.uint8), format="rgb24"
            )
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    marker = b"ARCHY-PLAINTEXT-MARKER"
    archive = tmp_path / "shoot.7z"
    with py7zr.SevenZipFile(str(archive), "w") as z:
        z.writef(io.BytesIO(video.getvalue()), "shoot/clip.mp4")
        for i in range(6):
            image = QImage(64, 48, QImage.Format.Format_RGB32)
            image.fill(QColor(i * 30, 80, 140))
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QBuffer.OpenModeFlag.WriteOnly)
            image.save(buffer, "PNG")
            z.writef(io.BytesIO(bytes(data) + marker), f"shoot/img{i}.png")

    scratch = tmp_path / "scratch"
    scratch.mkdir()

    @dataclass
    class _Temp:
        temp_mode: str = "disk"
        temp_location: str = "custom"
        temp_dir: str = str(scratch)
        prune_on_exit: bool = True

    app = _app()
    theme.apply(app)
    window = MainWindow()
    window._temp._settings = _Temp()
    from archy.ui import thumbnail_loader

    via_disk = []
    original = thumbnail_loader._ThumbTask._video_thumbnail_from_disk

    def spy(task):  # noqa: ANN001, ANN202
        via_disk.append(task._entry.path)
        return original(task)

    monkeypatch.setattr(thumbnail_loader._ThumbTask, "_video_thumbnail_from_disk", spy)
    window.resize(900, 600)
    window.show()
    _open(window, archive)
    window._view_group.button(2).setChecked(True)
    window._switch_view(2)
    window._enter_folder(window._nav[0].children["shoot"])

    images = [e.path for e in window._display_folder_files() if e.path.endswith(".png")]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        app.processEvents()
        if all(p in window._thumbs._cache for p in images):
            break
        time.sleep(0.01)
    assert all(p in window._thumbs._cache for p in images)
    backend = window._backend
    assert backend._folder_buffer == {}  # nothing kept in RAM...
    assert all(backend.spilled_blob(p) is not None for p in images)  # ...but on disk

    # The video's thumbnail comes through the encrypted store too (streamed to
    # disk, read by PyAV from the decrypting reader), not from RAM.
    clip = next(e.path for e in window._display_folder_files() if e.path.endswith(".mp4"))
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and clip not in window._thumbs._cache:
        window._grid.viewport().update()
        app.processEvents()
        time.sleep(0.01)
    assert clip in window._thumbs._cache
    assert via_disk == [clip]  # made via the encrypted store, not from RAM
    assert backend._read_cache_bytes == 0  # no RAM read cache in this mode

    # Nothing readable on disk: no marker, no original names.
    on_disk = [p for p in scratch.rglob("*") if p.is_file()]
    assert on_disk
    assert not any(marker in p.read_bytes() for p in on_disk)
    assert not any(p.suffix for p in on_disk)

    # Select the video: it plays from the encrypted store via the device.
    video_node = window._nav[-1].children["clip.mp4"]
    window._req_id += 1
    window._preview_node(video_node, window._req_id)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        app.processEvents()
        layout = window._preview_layout
        widget = layout.itemAt(0).widget() if layout.count() else None
        if widget is not None and hasattr(widget, "_video_device"):
            break
        time.sleep(0.01)
    assert hasattr(widget, "_video_device")
    assert not any(b"ftyp" in p.read_bytes()[:64] for p in scratch.rglob("*") if p.is_file())
    window.close()
    assert not [p for p in scratch.rglob("*") if p.is_file()]  # pruned at exit


def test_settings_map_to_memory_limits_and_migrate_old_temp_mode() -> None:
    from PySide6.QtCore import QCoreApplication

    from archy.ui.settings import Settings

    _app()
    org, name = QCoreApplication.organizationName(), QCoreApplication.applicationName()
    QCoreApplication.setOrganizationName("ArchyTest")
    QCoreApplication.setApplicationName("ArchyTestSettings")
    try:
        s = Settings()
        s._q.clear()
        limits = s.limits()  # all Automatic by default
        assert limits.cap_bytes is None and limits.cap_percent == 75
        assert limits.folder_buffer_bytes is None and limits.threads is None
        s.set("temp/mode", "temp")  # stored by older versions
        assert s.temp_mode == "overflow"
        s.set("res/cap_mode", "fixed")
        s.set("res/cap_gb", 6)
        s.set("res/folder_mb", 2048)
        s.set("res/threads", 3)
        limits = s.limits()
        assert limits.cap_bytes == 6 * 1024**3
        assert limits.folder_buffer_bytes == 2048 * 1024**2
        assert limits.threads == 3
        s._q.clear()
    finally:
        QCoreApplication.setOrganizationName(org)
        QCoreApplication.setApplicationName(name)


def test_byte_progress_signals_carry_more_than_2gb() -> None:
    # Qt's plain int is 32-bit: byte counts of a 46 GB extraction overflowed
    # (a bare "OverflowError" per update, and 0 delivered).
    from archy.ui.extract_worker import ExtractWorker
    from archy.ui.preview_loader import PreviewLoader

    _app()
    big, total = 5 * 1024**3, 46 * 1024**3
    worker = ExtractWorker()
    got: list = []
    worker.progress.connect(lambda d, t, n, f: got.append((d, t, n)))
    worker.progress.emit(big, total, "x.jpg", 3)
    loader = PreviewLoader()
    loader.spill_progress.connect(lambda r, d, t: got.append((r, d, t)))
    loader.spill_progress.emit(7, big, total)
    assert got == [(big, total, "x.jpg"), (7, big, total)]


def test_extract_dialog_follows_progress_and_shows_the_result(tmp_path, monkeypatch) -> None:
    import io
    import os
    import time

    py7zr = pytest.importorskip("py7zr")
    from PySide6.QtWidgets import QFileDialog

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    archive = tmp_path / "a.7z"
    with py7zr.SevenZipFile(str(archive), "w") as z:
        for i in range(20):
            z.writef(io.BytesIO(os.urandom(200_000)), f"map/sub/f{i:02d}.bin")
    dest = tmp_path / "out"
    dest.mkdir()
    pick = staticmethod(lambda *a, **k: str(dest))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", pick)

    app = _app()
    theme.apply(app)
    window = MainWindow()
    window._view_group.button(0).setChecked(True)
    window._switch_view(0)
    window.show()
    _open(window, archive)
    names: list[str] = []
    finished: list = []
    window._extract_worker.progress.connect(lambda d, t, n, f: names.append(n))
    window._extract_worker.finished.connect(finished.append)
    window.extract_all()
    dialog = window._extract_dialog
    assert dialog is not None and dialog.isVisible()
    deadline = time.monotonic() + 30
    while not finished and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert finished and finished[0].extracted == 20
    assert all(n.startswith("map/sub/") for n in names)  # full path in the archive
    assert dialog._state.text() == "Done"
    assert dialog._bar.value() == 1000
    assert "20 of 20" in dialog._numbers.text()
    assert len(list(dest.rglob("*.bin"))) == 20
    dialog.accept()
    window.close()


def _encrypted_backup(path, *, store: bool = True):  # noqa: ANN001, ANN202
    import io

    from archy.core.backends.sevenzip_writer import _Writer

    writer = _Writer(path, password="geheim", header_encrypted=True, store=store, solid=False)
    writer.add_dir("fotos")
    writer.add_file("fotos/a.jpg", 5000, io.BytesIO(b"a" * 5000))
    writer.add_file("fotos/b.jpg", 3000, io.BytesIO(b"b" * 3000))
    writer.add_file("notes.txt", 5, io.BytesIO(b"hallo"))
    writer.close()
    return path


def test_lock_shows_why_and_store_7z_can_be_unlocked_and_saved(tmp_path, monkeypatch) -> None:
    import time

    import archy.ui.main_window.locking as locking
    import archy.ui.main_window.opening as opening
    from archy.ui.main_window import MainWindow

    app = _app()
    monkeypatch.setattr(opening.soft_dialog, "prompt_text", lambda *a, **k: "geheim")
    asked: list[str] = []

    def fake_exec(dialog) -> int:  # noqa: ANN001 - the unlock explanation
        from PySide6.QtWidgets import QLabel

        asked.extend(label.text() for label in dialog.findChildren(QLabel))
        dialog._value = True
        return 1

    monkeypatch.setattr(locking.soft_dialog.SoftDialog, "exec", fake_exec)
    window = MainWindow()
    window.resize(1200, 800)
    window.show()

    # Compressed + encrypted: locked for good, and the tooltip says why.
    _open(window, _encrypted_backup(tmp_path / "packed.7z", store=False))
    assert window._lock_button.isVisible()
    assert window._lock_button.property("lockState") == locking.FIXED
    assert "compressed" in window._lock_button.toolTip()
    assert not window._can_edit()

    # Store + encrypted: locked, explained, and unlockable on purpose.
    archive = _encrypted_backup(tmp_path / "store.7z")
    _open(window, archive)
    assert window._lock_button.property("lockState") == locking.LOCKED
    assert "Click to unlock" in window._lock_button.toolTip()
    assert not window._can_edit()
    window._lock_button.click()
    assert any("next to the original" in text for text in asked)
    assert window._can_edit()
    assert window._lock_button.property("lockState") == locking.UNLOCKED

    window._backend.rename("notes.txt", "notities.txt")
    window._backend.write_bytes("nieuw.txt", b"nieuw")
    window.save_archive()
    deadline = time.monotonic() + 30
    while window._saving and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert not window._saving
    assert not window._backend.has_unsaved_changes
    names = {e.path for e in window._backend.list_entries()}
    assert {"notities.txt", "nieuw.txt", "fotos/a.jpg"} <= names and "notes.txt" not in names
    assert window._can_edit()  # stays unlocked for this session

    # Reopening locks it again.
    _open(window, archive)
    assert window._lock_button.property("lockState") == locking.LOCKED
    window.close()


def test_spinner_shows_while_an_archive_opens(tmp_path, monkeypatch) -> None:
    import time
    import zipfile

    from archy.core.backends.zip_backend import ZipBackend
    from archy.ui.main_window import MainWindow

    app = _app()
    archive = tmp_path / "slow.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.txt", "a")
    original = ZipBackend.list_entries

    def slow(self):  # noqa: ANN001, ANN202
        time.sleep(0.6)
        return original(self)

    monkeypatch.setattr(ZipBackend, "list_entries", slow)
    window = MainWindow()
    window.show()
    window.open_archive(archive)
    seen = False
    deadline = time.monotonic() + 10
    while window.is_opening and time.monotonic() < deadline:
        app.processEvents()
        seen = seen or window._busy.isVisible()
        time.sleep(0.01)
    app.processEvents()
    assert seen  # the window showed it was busy
    assert not window._busy.isVisible()
    assert window._archive_items == 1
    window.close()


def test_the_window_is_built_in_the_chosen_language() -> None:
    from archy import i18n
    from archy.ui.main_window import MainWindow

    _app()
    i18n.set_language("nl")
    dutch = MainWindow()
    assert dutch._open_button.text() == "Openen"
    assert dutch._status_selection.text() == "Geen selectie"
    dutch.close()
    i18n.set_language("en")
    english = MainWindow()
    assert english._open_button.text() == "Open"
    assert english._status_selection.text() == "No selection"
    english.close()


def test_video_in_a_stored_7z_plays_straight_from_the_archive(tmp_path) -> None:  # noqa: ANN001
    # A 7z made with Store: the video plays through a device that reads the
    # archive on demand, at once, without loading it into memory first.
    import io
    import time

    py7zr = pytest.importorskip("py7zr")
    av = pytest.importorskip("av")
    import numpy as np

    from archy.services.encrypted_device import ReaderDevice
    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    video = io.BytesIO()
    with av.open(video, "w", format="mp4") as out:
        stream = out.add_stream("mpeg4", rate=25)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for i in range(25):
            frame = av.VideoFrame.from_ndarray(
                np.full((48, 64, 3), i * 8, np.uint8), format="rgb24"
            )
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    archive = tmp_path / "stored.7z"
    filters = [{"id": py7zr.FILTER_COPY}, {"id": py7zr.FILTER_CRYPTO_AES256_SHA256}]
    with py7zr.SevenZipFile(
        archive, "w", filters=filters, password="geheim", header_encryption=True
    ) as z:
        z.writestr(video.getvalue(), "shoot/clip.mp4")
        z.writestr(b"notes", "shoot/notes.txt")

    app = _app()
    theme.apply(app)
    window = MainWindow()
    window._prompt_password = lambda _message: "geheim"
    window.resize(900, 600)
    window.show()
    _open(window, archive)
    window._enter_folder(window._nav[0].children["shoot"])
    reads = []
    original = window._backend.read_bytes
    window._backend.read_bytes = lambda *a, **k: reads.append(a) or original(*a, **k)

    window._req_id += 1
    window._preview_node(window._nav[-1].children["clip.mp4"], window._req_id)
    layout = window._preview_layout
    widget = layout.itemAt(0).widget() if layout.count() else None
    assert isinstance(getattr(widget, "_video_device", None), ReaderDevice)  # at once
    assert reads == []  # never read whole
    player = widget._video_player
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and player.duration() <= 0:
        app.processEvents()
        time.sleep(0.01)
    assert player.duration() > 0  # the player reads it fine through the device
    window.close()


def test_ctrl_f_finds_in_the_previewed_file_and_pages_read_their_css(tmp_path) -> None:  # noqa: ANN001
    import zipfile

    from PySide6.QtWidgets import QApplication

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    archive = tmp_path / "site.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("site/data.json", '{"name": "Archy", "other": "archy"}')
        z.writestr("site/index.html", '<link rel="stylesheet" href="css/s.css"><p>x</p>')
        z.writestr("site/css/s.css", "p { color: #00aa00; }")
    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.resize(1300, 820)
    window.show()
    _open(window, archive)
    window._enter_folder(window._nav[0].children["site"])

    window._req_id += 1
    window._preview_node(window._nav[-1].children["data.json"], window._req_id)
    preview = window._preview_layout.itemAt(0).widget()
    for _ in range(200):  # zip text opens synchronously; others via the loader
        if hasattr(preview, "open_find"):
            break
        app.processEvents()
        preview = window._preview_layout.itemAt(0).widget()
    window.activateWindow()
    preview.edit.setFocus()
    app.processEvents()
    window._find_action.trigger()  # Ctrl+F / Cmd+F with the focus in the file
    assert preview.find_bar.isVisible()
    preview.find_bar.field.setText("archy")
    assert preview.find_bar.counter.text() == "1/2"

    window._current_view().setFocus()  # focus in the file list: filter the folder
    app.processEvents()
    preview.find_bar.close_bar()
    window._current_view().setFocus()
    app.processEvents()
    window._find_action.trigger()
    assert QApplication.focusWidget() is window._search
    assert not preview.find_bar.isVisible()

    read = window._archive_resources(window._nav[-1].children["index.html"])
    assert read("css/s.css") == b"p { color: #00aa00; }"
    assert read("../../outside.css") is None  # never outside the archive
    assert read("https://example.com/x.css") is None
    window.close()


def test_mixed_7z_shows_a_notice_and_previews_show_file_details(tmp_path) -> None:  # noqa: ANN001
    # A Store 7z with files added compressed: a notice (no dialog) says so.
    # Selecting a file shows what is known about it under its name, not the
    # type and size that the footer already shows.
    import io
    import time

    py7zr = pytest.importorskip("py7zr")
    from PySide6.QtGui import QColor, QImage

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    image = QImage(320, 200, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    png = io.BytesIO()
    from PySide6.QtCore import QBuffer, QByteArray

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    png.write(bytes(data))
    archive = tmp_path / "mixed.7z"
    with py7zr.SevenZipFile(archive, "w", filters=[{"id": py7zr.FILTER_COPY}]) as z:
        z.writestr(png.getvalue(), "photo.png")
        z.writestr(b"one\ntwo\nthree\n", "notes.txt")
    with py7zr.SevenZipFile(archive, "a") as z:
        z.writestr(b"later " * 100, "added.txt")

    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.resize(900, 600)
    window.show()
    _open(window, archive)
    assert window._archive_notice.isVisible()
    assert "1" in window._notice_text.text() and "3" in window._notice_text.text()

    def shown(path: str) -> str:
        window._select_path(path)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            app.processEvents()
            text = window._preview_sub.full_text()
            if "px" in text or "lines" in text:
                break
            time.sleep(0.01)
        return window._preview_sub.full_text()

    assert shown("photo.png") == "320 × 200 px"
    # The zoom readout always fits its pill (it used to clip "100%").
    label = window._stage_zoom_val
    label.setText("100%")
    window._on_preview_scale_changed(100)
    assert label.width() >= label.fontMetrics().horizontalAdvance("100%")
    assert shown("notes.txt") == "3 lines · UTF-8"
    window.close_archive()
    assert not window._archive_notice.isVisible()
    window.close()


def test_pdf_preview_knows_its_pages_title_and_author() -> None:
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QPainter, QPdfWriter

    from archy.services.providers import build_preview_widget

    app = _app()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    writer = QPdfWriter(buffer)
    writer.setTitle("Reisverslag")
    if hasattr(writer, "setAuthor"):
        writer.setAuthor("Mark")
    painter = QPainter(writer)
    painter.drawText(100, 100, "one")
    writer.newPage()
    painter.drawText(100, 100, "two")
    painter.end()
    buffer.close()
    widget = build_preview_widget("trip.pdf", bytes(data))
    app.processEvents()
    details = dict(widget._zx_details.items)
    assert details["details.pages"] == "2 pages"
    assert details["details.title"] == "Reisverslag"
    widget._zx_cleanup()


def test_sorting_by_the_timestamp_in_the_name(tmp_path) -> None:  # noqa: ANN001
    import time
    import zipfile

    from PySide6.QtCore import QCoreApplication

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    archive = tmp_path / "stamps.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("sub/inside.txt", b"x")
        z.writestr("1696512000--b.jpg", b"x")  # Oct 2023
        z.writestr("999999999_a.jpg", b"x")  # Sep 2001: shorter, sorts last by name
        z.writestr("1700000000123_c.jpg", b"x")  # Nov 2023, in milliseconds
        z.writestr("zeta.txt", b"x")
        z.writestr("Alpha.txt", b"x")

    app = _app()
    theme.apply(app)
    org, name = QCoreApplication.organizationName(), QCoreApplication.applicationName()
    QCoreApplication.setOrganizationName("ArchyTest")
    QCoreApplication.setApplicationName("ArchyTestStamps")
    try:
        from archy.ui.settings import Settings

        Settings()._q.clear()
        window = MainWindow()
        window.show()
        _open(window, archive)

        def names() -> list[str]:
            proxy = window._proxy
            return [window._node_at(proxy.index(r, 0)).name for r in range(proxy.rowCount())]

        window._sort_by(6, False)
        # Folders first; then names with a time, oldest first; then the rest by name.
        assert names() == [
            "sub",
            "999999999_a.jpg",
            "1696512000--b.jpg",
            "1700000000123_c.jpg",
            "Alpha.txt",
            "zeta.txt",
        ]
        assert window._sort_column_actions[6].isChecked()
        assert window._table.isColumnHidden(6)  # only a sort choice
        window._sort_by(None, True)  # newest first, the others still below
        assert names() == [
            "sub",
            "1700000000123_c.jpg",
            "1696512000--b.jpg",
            "999999999_a.jpg",
            "zeta.txt",
            "Alpha.txt",
        ]
        again = MainWindow()  # remembered
        assert again._settings.sort_column == 6
        again.close()
        window.close()
        time.sleep(0.01)
        Settings()._q.clear()
    finally:
        QCoreApplication.setOrganizationName(org)
        QCoreApplication.setApplicationName(name)


def test_sorting_applies_to_every_view_keeps_folders_first_and_is_remembered(tmp_path) -> None:  # noqa: ANN001
    import time
    import zipfile

    from PySide6.QtCore import QCoreApplication

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    archive = tmp_path / "sort.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("b-map/inside.txt", b"x")
        z.writestr("a-map/big-photo.jpg", b"x" * 500)
        z.writestr("small.txt", b"x" * 10)
        z.writestr("large.txt", b"x" * 900)
        z.writestr("medium.jpg", b"x" * 300)

    app = _app()
    theme.apply(app)
    org, name = QCoreApplication.organizationName(), QCoreApplication.applicationName()
    QCoreApplication.setOrganizationName("ArchyTest")
    QCoreApplication.setApplicationName("ArchyTestSort")
    try:
        from archy.ui.settings import Settings

        Settings()._q.clear()
        window = MainWindow()
        window.resize(1100, 700)
        window.show()
        _open(window, archive)

        def names() -> list[str]:
            proxy = window._proxy
            return [window._node_at(proxy.index(r, 0)).name for r in range(proxy.rowCount())]

        window._switch_view(2)  # thumbnails: no header, the sort menu drives it
        window._sort_by(1, True)  # size, largest first
        assert names() == ["a-map", "b-map", "large.txt", "medium.jpg", "small.txt"]
        assert window._sort_button.text() == "↓"
        assert window._sort_column_actions[1].isChecked()
        assert window._table.horizontalHeader().sortIndicatorSection() == 1  # in sync

        # Archive-wide search: folders above files, whatever the sort.
        window._focus_search("archive")
        window._search.setText("map")
        window._apply_archive_search()
        found = names()
        assert found[:2] == ["a-map", "b-map"] and set(found[2:]) == {"inside.txt", "big-photo.jpg"}
        window._search.clear()
        app.processEvents()

        # Remembered for the next window (and the next start).
        again = MainWindow()
        assert again._settings.sort_column == 1 and again._settings.sort_descending
        assert again._table.horizontalHeader().sortIndicatorSection() == 1
        again.close()
        window.close()
        time.sleep(0.01)
        Settings()._q.clear()
    finally:
        QCoreApplication.setOrganizationName(org)
        QCoreApplication.setApplicationName(name)


def test_closing_an_archive_clears_the_status_strip(tmp_path) -> None:  # noqa: ANN001
    # A selection and a folder pass still running for the closed archive must
    # not stay behind in the status strip under the file list.
    import zipfile

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for i in range(5):
            z.writestr(f"pics/{i}.txt", b"x" * 100)

    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.resize(1000, 650)
    window.show()
    _open(window, archive)
    window._enter_folder(window._nav[0].children["pics"])
    window._select_path("pics/1.txt")
    app.processEvents()
    assert "1.txt" in window._status_selection.text()

    # A pass that is still decoding this folder when the archive is closed.
    window._prefetcher._running = True
    window._on_prefetch_started(5)
    window._on_prefetch_progress(2, 5)
    window._on_thumb_progress(1, 5)
    assert window._prefetch_bar.isVisible()

    window.close_archive()
    app.processEvents()
    assert window._status_selection.text() == "No selection"
    assert not window._prefetch_bar.isVisible() and not window._prefetch_label.isVisible()
    assert not window._thumb_bar.isVisible() and not window._thumb_label.isVisible()
    assert window._status_msg.text() == "Ready"
    window.close()


def _png(color: str) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QColor, QImage

    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def test_slideshow_shows_only_showable_photos_with_a_bar_and_wraps(tmp_path) -> None:  # noqa: ANN001
    import time
    import zipfile

    from PySide6.QtCore import QCoreApplication

    from archy.ui import theme
    from archy.ui.main_window import MainWindow
    from archy.ui.settings import Settings

    archive = tmp_path / "photos.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("a.png", _png("#aa0000"))
        z.writestr("b.png", _png("#00aa00"))  # "too large" below
        z.writestr("broken.png", b"not really a png")  # cannot be shown
        z.writestr("notes.txt", b"skipped by the slideshow")
        z.writestr("c.png", _png("#0000aa"))

    app = _app()
    theme.apply(app)
    org, name = QCoreApplication.organizationName(), QCoreApplication.applicationName()
    QCoreApplication.setOrganizationName("ArchyTest")
    QCoreApplication.setApplicationName("ArchyTestSlideshow")
    try:
        Settings()._q.clear()
        window = MainWindow()
        window.resize(900, 600)
        window.show()
        _open(window, archive)
        limit = window._media_size_limit
        window._media_size_limit = lambda kind, node=None: (
            1 if node is not None and node.name == "b.png" else limit(kind, node)
        )
        window._select_path("a.png")
        deadline = time.monotonic() + 5
        while not window._fullscreenable and time.monotonic() < deadline:
            app.processEvents()  # the photo is shown
        seen = []
        window.start_slideshow()
        bar = window._fs_window.bar
        assert bar is not None and window._slideshow_timer.isActive()
        window._slideshow_timer.setInterval(60)  # a fast slideshow for the test

        deadline = time.monotonic() + 5
        while len([n for n in seen if n != "broken.png"]) < 3 and time.monotonic() < deadline:
            app.processEvents()
            node = window._node_at(window._selection.currentIndex())
            if node is not None and (not seen or seen[-1] != node.name):
                seen.append(node.name)
            time.sleep(0.005)
        assert "b.png" not in seen and "notes.txt" not in seen  # too large / not a photo
        assert [n for n in seen if n != "broken.png"][:3] == ["a.png", "c.png", "a.png"]
        # Left out: known as undecodable from its thumbnail, or tried once.
        assert "broken.png" in window._slideshow_skip | window._thumbs._failed

        # The bar: pause / play, the time per photo, stop.
        bar.play.click()
        assert not window._slideshow_timer.isActive()
        bar.play.click()
        assert window._slideshow_timer.isActive()
        bar.faster.click()  # +1 s on the default 4
        assert window._settings.slideshow_seconds == 5
        assert window._slideshow_timer.interval() == 5000
        bar.stop.click()
        app.processEvents()
        assert window._fs_window is None and not window._slideshow_timer.isActive()
        window.close()
        Settings()._q.clear()
    finally:
        QCoreApplication.setOrganizationName(org)
        QCoreApplication.setApplicationName(name)


def test_music_plays_with_its_details(tmp_path) -> None:  # noqa: ANN001
    import io
    import time
    import wave

    from archy.services.preview import PreviewKind, kind_for_name
    from archy.services.providers import build_preview_widget

    assert kind_for_name("song.FLAC") is PreviewKind.AUDIO
    app = _app()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x10" * 8000 * 2)  # two seconds
    widget = build_preview_widget("song.wav", out.getvalue())
    widget.resize(400, 300)
    widget.show()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and widget._video_player.duration() <= 0:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert dict(widget._zx_details.items)["details.duration"] == "0:02"
    assert widget._zx_step is None  # no frame stepping for music
    widget._zx_cleanup()


def test_back_forward_mouse_buttons_and_breadcrumb_folders(tmp_path) -> None:  # noqa: ANN001
    import zipfile

    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    archive = tmp_path / "trip.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("2024/zomer/a.txt", b"a")
        z.writestr("2024/winter/b.txt", b"b")
        z.writestr("2025/c.txt", b"c")

    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.resize(1000, 650)
    window.show()
    _open(window, archive)

    def where() -> str:
        return "/".join(node.name for node in window._nav[1:])

    window._enter_folder(window._nav[0].children["2024"])
    window._enter_folder(window._nav[-1].children["zomer"])
    window.go_up()
    assert where() == "2024"
    window.go_back()
    assert where() == "2024/zomer"
    window.go_back()
    window.go_back()
    assert where() == ""
    window.go_back()  # nothing further back
    assert where() == ""
    window.go_forward()
    assert where() == "2024"

    # The mouse's back button, pressed over the file list.
    viewport = window._table.viewport()
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.BackButton,
        Qt.MouseButton.BackButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(viewport, press)
    assert where() == ""

    # The "/" after 2024 lists its folders; picking one jumps sideways.
    window._enter_folder(window._nav[0].children["2024"])
    window._enter_folder(window._nav[-1].children["zomer"])
    picker = window._folder_picker(1)
    assert picker.matches() == ["winter", "zomer"]
    assert picker.list.currentItem().text() == "zomer"  # where we are
    picker.field.setText("wint")
    assert picker.matches() == ["winter"]
    picker._pick(picker.list.item(0))
    assert where() == "2024/winter"
    window.go_back()
    assert where() == "2024/zomer"
    window.close()


def test_an_archive_inside_an_archive_opens_and_goes_back(tmp_path) -> None:  # noqa: ANN001
    import io
    import zipfile

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("deep/note.txt", b"hello from the inner zip")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("old/inner.zip", inner.getvalue())
        z.writestr("old/a.txt", b"a")

    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.resize(1000, 650)
    window.show()
    _open(window, outer)
    window._enter_folder(window._nav[0].children["old"])
    outer_backend = window._backend
    window._open_nested(window._nav[-1].children["inner.zip"])
    app.processEvents()

    assert window._backend is not outer_backend and window._outer_levels
    assert window._archive_label == "inner.zip"
    assert window._archive_name_label.text() == "outer.zip › inner.zip"
    assert "deep" in window._nav[0].children
    assert not window._backend.writable  # read-only
    window._enter_folder(window._nav[0].children["deep"])
    node = window._nav[-1].children["note.txt"]
    assert window._backend.read_bytes(node.entry) == b"hello from the inner zip"

    window.go_up()  # deep -> inner root
    window.go_up()  # out of the inner archive, back where we were
    assert window._backend is outer_backend and not window._outer_levels
    assert [n.name for n in window._nav[1:]] == ["old"]
    assert window._archive_name_label.text() == "outer.zip"

    window._open_nested(window._nav[-1].children["inner.zip"])
    window.close_archive()  # closes both
    assert window._backend is None and not window._outer_levels
    window.close()


def test_folder_picker_shows_twenty_and_filters_hundreds() -> None:
    from archy.core.tree import TreeNode
    from archy.ui.folder_picker import MAX_SHOWN, FolderPicker

    _app()
    folders = [TreeNode(f"map {i:03d}", f"map {i:03d}", True) for i in range(469)]
    picked = []
    picker = FolderPicker(folders, None, picked.append)
    assert len(picker.matches()) == MAX_SHOWN == 20
    assert not picker.more.isHidden() and "449" in picker.more.text()
    picker.field.setText("map 46")
    assert picker.matches() == [f"map 46{i}" for i in range(9)]  # 460 to 468
    picker.field.setText("nothing like it")
    assert picker.matches() == [] and "No folder" in picker.more.text()
    picker.field.setText("map 468")
    picker._pick(picker.list.item(0))
    assert [node.name for node in picked] == ["map 468"]
    # Opened from inside "map 300", the list shows that folder (and its neighbours).
    there = FolderPicker(folders, "map 300", picked.append)
    assert "map 300" in there.matches() and there.list.currentItem().text() == "map 300"


def test_clicking_empty_space_or_another_folder_clears_the_selection_text(tmp_path) -> None:  # noqa: ANN001
    import zipfile

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("one.txt", b"1")
        z.writestr("sub/two.txt", b"2")
    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.resize(900, 600)
    window.show()
    _open(window, archive)
    window._select_path("one.txt")
    app.processEvents()
    assert "one.txt" in window._status_selection.text()
    window._selection.clearSelection()  # what a click on empty space does
    app.processEvents()
    assert window._status_selection.text() == "No selection"
    assert "one.txt" not in window._status_msg.text()

    window._select_path("one.txt")
    window._enter_folder(window._nav[0].children["sub"])  # e.g. via the breadcrumb
    assert window._status_selection.text() == "No selection"
    assert "one.txt" not in window._status_msg.text()
    window.close()


def test_a_click_in_the_thumbnail_view_previews_the_file(tmp_path) -> None:  # noqa: ANN001
    # The grid views select only the item, not the whole row: a plain click
    # there must still preview the file (it once showed "No selection").
    import zipfile

    from PySide6.QtCore import QItemSelectionModel

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("note.txt", b"hello")
    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.resize(900, 600)
    window.show()
    _open(window, archive)
    window._switch_view(2)
    index = window._proxy.index(0, 0)
    window._selection.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    app.processEvents()
    assert "note.txt" in window._status_selection.text()
    assert window._preview_title.full_text() == "note.txt"
    window.close()


def test_slideshow_hides_the_cursor_when_the_mouse_rests() -> None:
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QLabel

    from archy.ui.main_window import chrome

    app = _app()
    shown = QLabel("photo")
    window = chrome.FullscreenWindow(shown, lambda content: None, lambda step: None)
    window.resize(800, 600)
    window.show()
    window.activateWindow()
    app.processEvents()
    window.isActiveWindow = lambda: True  # offscreen windows are not always activated
    noop = lambda *args: None  # noqa: E731
    window.enable_slideshow_bar(
        on_prev=noop, on_toggle=noop, on_next=noop, on_seconds=noop, on_stop=noop
    )
    before = QGuiApplication.overrideCursor()
    assert not window.cursor_hidden
    window._hide_bar()  # what the 2.5 s idle timer does
    assert window.cursor_hidden and not window.bar.isVisible()
    assert QGuiApplication.overrideCursor().shape() == chrome.Qt.CursorShape.BlankCursor
    window._last_cursor = None  # the mouse moved
    window._check_cursor()
    assert not window.cursor_hidden and window.bar.isVisible()
    assert QGuiApplication.overrideCursor() is before
    window._hide_bar()
    window.close()  # leaving the slideshow always brings the cursor back
    assert not window.cursor_hidden and QGuiApplication.overrideCursor() is before


def test_thumbnail_tiles_grow_to_fill_the_row(tmp_path) -> None:  # noqa: ANN001
    import time
    import zipfile

    from archy.ui import theme
    from archy.ui.grid_delegate import MAX_GROW
    from archy.ui.main_window import MainWindow

    archive = tmp_path / "photos.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for i in range(30):
            z.writestr(f"{i:02}.png", _png("#336699"))
    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    _open(window, archive)
    for view_index, view in ((2, window._grid), (3, window._large)):
        window._switch_view(view_index)
        base_w = view.itemDelegate()._base[0]
        widths = set()
        for width in (950, 1080, 1210, 1500):
            window.resize(width, 800)
            for _ in range(10):
                app.processEvents()
                time.sleep(0.005)
            first = view.visualRect(window._proxy.index(0, 0))
            row = [
                view.visualRect(window._proxy.index(i, 0))
                for i in range(30)
                if view.visualRect(window._proxy.index(i, 0)).top() == first.top()
            ]
            gap = view.viewport().width() - row[-1].right()
            # The row is filled: what is left is less than the spacing plus a
            # scroll bar, never most of a tile (unless the tiles are as large
            # as they may get, with one or two columns).
            capped = first.width() == int(base_w * MAX_GROW)
            assert gap < 30 or capped, (width, gap, first.width())
            assert base_w <= first.width() <= base_w * MAX_GROW
            widths.add(first.width())
        assert len(widths) > 1  # the tiles follow the width
    window.close()


def test_splash_shows_while_starting_and_closes_with_the_window() -> None:
    from PySide6.QtWidgets import QWidget

    from archy.ui import splash as splash_module

    app = _app()
    splash = splash_module.show_splash(app)
    assert splash.isVisible() and not splash.pixmap().isNull()
    window = QWidget()
    window.show()
    splash.finish(window)
    app.processEvents()
    assert not splash.isVisible()
    window.close()


def test_documents_get_thumbnails_and_html_counts_as_a_document(tmp_path) -> None:  # noqa: ANN001
    import time
    import zipfile

    from archy.core.tree import TreeNode
    from archy.ui import theme
    from archy.ui.archive_model import category_of, icon_of
    from archy.ui.main_window import MainWindow

    archive = tmp_path / "docs.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("index.html", "<html><body><h1>Welcome</h1></body></html>")
        z.writestr("README.md", "# Archy\n\nLook inside archives.\n")
        z.writestr("notes.txt", "Shopping list\n- bread\n")
        z.writestr("report.docx", b"PK\x03\x04 not really")
    page = TreeNode(name="index.html", path="index.html", is_dir=False)
    assert category_of(page) == "document" and icon_of(page) == "doc"

    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.resize(1000, 700)
    window.show()
    _open(window, archive)
    window._switch_view(2)
    deadline = time.monotonic() + 15
    want = {"index.html", "README.md", "notes.txt"}
    while not want <= set(window._thumbs._cache) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert want <= set(window._thumbs._cache)
    assert "report.docx" not in window._thumbs._cache  # no preview: stays an icon
    window.close()


def test_large_photo_is_drawn_smoothly_when_scaled_down() -> None:
    # Fine black/white stripes far below screen resolution must average out
    # to grey (mipmaps), not turn into black/white noise (aliasing).
    from PySide6.QtGui import QColor, QImage, QPainter, QPen

    from archy.ui.image_view import ImageView

    app = _app()
    image = QImage(4000, 3000, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setPen(QPen(QColor("black"), 2))
    for x in range(0, 4000, 5):
        painter.drawLine(x, 0, x, 3000)
    painter.end()
    view = ImageView()
    view.resize(420, 320)
    view.show()
    view.set_image(image)
    app.processEvents()
    assert view.zoom_percent() < 15
    shot = view.viewport().grab().toImage()
    middle = [
        QColor(shot.pixel(x, y)).lightness()
        for x in range(shot.width() // 3, 2 * shot.width() // 3, 3)
        for y in range(shot.height() // 3, 2 * shot.height() // 3, 3)
    ]
    assert max(middle) - min(middle) < 100, (min(middle), max(middle))  # noise: 0..255
    view.close()


def test_folder_loading_shows_it_is_busy_before_the_first_file() -> None:
    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.show()
    window._on_prefetch_started(300)
    bar = window._prefetch_bar
    assert window._prefetch_label.isVisible()
    assert (bar.minimum(), bar.maximum()) == (0, 0)  # a busy bar, moving on its own
    window._on_prefetch_activity(-1, -1)  # still busy, nothing known
    assert (bar.minimum(), bar.maximum()) == (0, 0)
    window._prefetch_since -= 75  # busy for a while: the time is shown
    window._on_prefetch_activity(-1, -1)
    assert window._prefetch_label.text().endswith("1:15")
    window._on_prefetch_activity(512 * 1024 * 1024, 2 * 1024 * 1024 * 1024)
    assert (bar.maximum(), bar.value()) == (1000, 250)  # follows the bytes read
    assert window._prefetch_label.text().endswith("25%")
    assert "512" in window._prefetch_label.toolTip()  # the amounts on hover
    # Through the signal, with a block far over 2 GB (a 32-bit int overflowed).
    window._prefetcher.activity.emit(3 * 1024**3, 12 * 1024**3)
    assert (bar.maximum(), bar.value()) == (1000, 250)
    window._on_prefetch_done()
    assert not window._prefetch_label.isVisible()
    window.close()


def test_going_back_up_keeps_your_place_in_a_long_folder(tmp_path) -> None:  # noqa: ANN001
    import zipfile

    from PySide6.QtWidgets import QApplication

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    archive = tmp_path / "long.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for i in range(80):
            z.writestr(f"map{i:02d}/foto{i:02d}.jpg", b"x")
            z.writestr(f"map{i:02d}/notes.txt", b"x")

    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.resize(1000, 600)
    window.show()
    _open(window, archive)

    def current() -> str | None:
        index = window._selection.currentIndex()
        node = window._node_at(index) if index.isValid() else None
        return node.name if node is not None else None

    def visible(view) -> bool:  # noqa: ANN001
        rect = view.visualRect(window._selection.currentIndex())
        return view.viewport().rect().intersects(rect)

    for view_index in (0, 2):  # the list, and the thumbnails
        window._switch_view(view_index)
        view = window._current_view()
        root = window._nav[0]
        window._enter_folder(root.children["map65"])
        window._go_to_depth(0)  # the path bar, back to the top level
        for _ in range(3):
            app.processEvents()
        assert current() == "map65"  # the folder you came out of, selected
        assert window._selection.isSelected(window._selection.currentIndex())
        assert view.verticalScrollBar().value() > 0 and visible(view)  # not at the top

        # Up with the keyboard/button and Back go to the same place.
        window._enter_folder(root.children["map70"])
        window.go_up()
        app.processEvents()
        assert current() == "map70"

    # A file you were at in a folder: the cursor goes back there, without
    # selecting it (that would open its preview).
    window._switch_view(0)
    window._enter_folder(window._nav[0].children["map10"])
    window._select_path("map10/notes.txt")
    window.go_up()
    window._enter_folder(window._nav[0].children["map10"])
    QApplication.processEvents()
    assert current() == "notes.txt"
    assert not window._selection.selectedIndexes()
    window.close()


def _jpeg(color: tuple[int, int, int]) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buffer, "JPEG")
    return buffer.getvalue()


def test_a_photo_in_memory_shows_without_waiting_for_another_read(tmp_path) -> None:  # noqa: ANN001
    # A photo still being read (here: a read that never ends) must not hold up
    # the preview of one that is in memory already.
    import zipfile

    from PySide6.QtWidgets import QApplication

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    archive = tmp_path / "photos.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("a_slow.jpg", _jpeg((200, 20, 20)))
        z.writestr("b_ready.jpg", _jpeg((20, 200, 20)))
    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.show()
    _open(window, archive)
    submitted: list[str] = []
    window._loader.submit = lambda _rid, _b, entry, *_a, **_k: submitted.append(entry.path)
    ready = window._nav[0].children["b_ready.jpg"]
    window._backend.read_bytes(ready.entry)  # in memory now (the read cache)

    window._select_path("a_slow.jpg")
    QApplication.processEvents()
    assert submitted == ["a_slow.jpg"]
    assert window._current_image_view() is None  # still loading
    window._select_path("b_ready.jpg")
    QApplication.processEvents()
    assert submitted == ["a_slow.jpg"]  # not queued behind it
    assert window._current_image_view() is not None  # shown at once
    window.close()


def test_a_stale_preview_read_is_skipped() -> None:
    from archy.ui.preview_loader import PreviewLoader, _ReadTask

    class Backend:
        reads = 0

        def read_bytes(self, entry, max_bytes=None):  # noqa: ANN001, ANN202
            Backend.reads += 1
            return b"x"

    _app()
    loader = PreviewLoader()
    got: list[int] = []
    loader.loaded.connect(lambda rid, _data: got.append(rid))
    _ReadTask(loader, 1, Backend(), object(), None, lambda: True).run()
    _ReadTask(loader, 2, Backend(), object(), None, lambda: False).run()
    assert Backend.reads == 1 and got == [2]


def test_locate_button_brings_the_selection_back_into_view(tmp_path) -> None:  # noqa: ANN001
    import zipfile

    from PySide6.QtWidgets import QApplication

    from archy.ui import theme
    from archy.ui.main_window import MainWindow

    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for i in range(150):
            z.writestr(f"file{i:03d}.txt", b"x")
    app = _app()
    theme.apply(app)
    window = MainWindow()
    window.resize(1000, 600)
    window.show()
    _open(window, archive)
    button = window._locate_button
    assert not button.isEnabled()  # nothing selected yet
    for view_index in (0, 3):
        window._switch_view(view_index)
        view = window._current_view()
        window._select_path("file120.txt")
        assert button.isEnabled()
        view.verticalScrollBar().setValue(0)  # scrolled away to the top
        QApplication.processEvents()
        rect = view.visualRect(window._selection.currentIndex())
        assert not view.viewport().rect().intersects(rect)
        button.click()
        QApplication.processEvents()
        rect = view.visualRect(window._selection.currentIndex())
        assert view.viewport().rect().intersects(rect)
    window.close()
