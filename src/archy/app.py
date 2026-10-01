from __future__ import annotations

import sys


def _install_hang_watchdog(log_path: str) -> None:
    """Diagnostic: if the GUI thread stops responding for a few seconds, dump the
    stack traces of ALL threads to ``log_path``. Enabled only when the
    ARCHY_HANG_LOG environment variable points at a file, so it costs nothing
    in normal use. Lets a freeze be captured without a terminal or manual signal.
    """
    import faulthandler
    import threading
    import time

    from PySide6.QtCore import QTimer

    beat = [time.monotonic()]

    timer = QTimer()
    timer.setInterval(1000)
    timer.timeout.connect(lambda: beat.__setitem__(0, time.monotonic()))
    timer.start()
    _WATCHDOG_KEEPALIVE.append(timer)  # keep the timer alive for the app's life

    def watch() -> None:
        dumped = False
        while True:
            time.sleep(2.0)
            stalled = time.monotonic() - beat[0]
            if stalled > 6.0 and not dumped:
                try:
                    with open(log_path, "a") as fh:
                        fh.write(f"\n=== Archy UI frozen for {stalled:.0f}s ===\n")
                        faulthandler.dump_traceback(file=fh, all_threads=True)
                    dumped = True
                except Exception:  # noqa: BLE001 - diagnostics are best-effort
                    pass
            elif stalled < 3.0:
                dumped = False  # UI recovered; arm again for the next freeze

    threading.Thread(target=watch, daemon=True).start()


_WATCHDOG_KEEPALIVE: list = []

# Windows groups taskbar buttons by AppUserModelID. Without an explicit one a
# `python -m archy` run is filed under python.exe and shows Python's icon. The
# installer's shortcuts carry the same ID (packaging/windows/archy.iss), so a
# pinned Archy and the running app share one taskbar button.
WINDOWS_APP_ID = "Archy.Archy"


def _set_windows_app_id() -> None:
    """Give this process its own taskbar identity on Windows (must run before
    the first window is created). No-op elsewhere."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    except Exception:  # noqa: BLE001 - cosmetic; never block startup
        pass


def _app_icon_path() -> object:
    """The icon for the Dock / taskbar / window title. macOS gets the padded,
    rounded variant that matches other Dock icons; elsewhere the full square."""
    from pathlib import Path

    icons = Path(__file__).resolve().parent / "resources" / "icons"
    preferred = icons / ("archy_macos.png" if sys.platform == "darwin" else "archy.png")
    return preferred if preferred.exists() else icons / "archy.png"


def _apply_app_icon(app: object) -> None:
    """Replace the default Python icon: QApplication.setWindowIcon sets the Dock
    icon on macOS and the window/taskbar icon on Windows and Linux."""
    from PySide6.QtGui import QIcon

    path = _app_icon_path()
    if path.exists():  # type: ignore[attr-defined]
        app.setWindowIcon(QIcon(str(path)))  # type: ignore[attr-defined]


def _install_file_open_handler(app: object, window: object) -> None:
    """Open an archive from a command-line path argument, and keep listening for
    macOS QFileOpenEvents (Open With / dock drop) for the app's lifetime."""
    import sys
    from pathlib import Path

    from PySide6.QtCore import QEvent, QObject

    def _open(path: Path) -> None:
        if path.exists() and path.is_file():
            window.open_archive(path)  # type: ignore[attr-defined]

    class _FileOpenFilter(QObject):
        def eventFilter(self, _obj: object, event: object) -> bool:  # noqa: N802
            if event.type() == QEvent.Type.FileOpen:
                name = event.file()  # type: ignore[attr-defined]
                if name:
                    _open(Path(name))
                return True
            return False

    file_open_filter = _FileOpenFilter()
    app.installEventFilter(file_open_filter)  # type: ignore[attr-defined]
    _WATCHDOG_KEEPALIVE.append(file_open_filter)  # keep it alive for the app's life

    for arg in sys.argv[1:]:
        candidate = Path(arg)
        if candidate.exists() and candidate.is_file():
            _open(candidate)
            break


def run() -> int:
    """Start the Qt application and show the main window.

    Imported lazily so that importing the package (e.g. in tests that only
    touch archy.core) does not require PySide6 to be loaded.
    """
    import os

    from PySide6.QtCore import QLoggingCategory
    from PySide6.QtWidgets import QApplication

    from archy.ui import theme
    from archy.ui.main_window import MainWindow
    from archy.ui.settings import Settings

    # Silence Qt's informational multimedia/ffmpeg category chatter
    # (e.g. "Using Qt multimedia with FFmpeg version ...").
    QLoggingCategory.setFilterRules("qt.multimedia.ffmpeg=false")

    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("Archy")
    app.setOrganizationName("Archy")
    _apply_app_icon(app)
    _hang_log = os.environ.get("ARCHY_HANG_LOG")
    if _hang_log:
        _install_hang_watchdog(_hang_log)
    # The UI language first (every text is looked up when a widget is built):
    # the chosen one, or for "auto" the system language when Archy has it.
    from archy import i18n
    from archy.ui.settings import system_languages

    _startup = Settings()
    i18n.set_language(i18n.resolve(_startup.language, system_languages()))
    # Apply the user's chosen accent pair before building the theme/widgets.
    theme.set_accent(_startup.accent, _startup.accent2)
    theme.apply(app)

    window = MainWindow()
    window.show()

    # File associations (app side): open an archive passed on the command line
    # (Windows/Linux "Open with", or `archy <file>`), and one delivered via a
    # macOS QFileOpenEvent (Open With / drop on the dock icon), now or later. The
    # OS-level registration of the file types belongs to the installers (packaging/).
    _install_file_open_handler(app, window)

    code = app.exec()

    # Video previews are torn down by events posted to the event loop (see
    # providers._dispose_player_async). A preview still open at quit posted its
    # teardown during closeEvent, but the loop has stopped, so the player is
    # still alive; letting interpreter shutdown destroy it from Python would run
    # its destructor while holding the GIL - the deadlock the teardown avoids.
    # All real cleanup already ran in closeEvent, so exit hard in that case.
    from archy.services import providers

    if providers._PENDING_VIDEO_TEARDOWNS:
        os._exit(code)
    return code
