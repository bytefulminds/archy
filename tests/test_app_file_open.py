"""Files handed over by macOS (QFileOpenEvent) reach the window through the
application's own event(), not an application-wide event filter (which ran
Python for every event of every object and crashed with QtWebEngine). Runs in
a fresh process: the other UI tests already made a QApplication."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_SCRIPT = textwrap.dedent(
    """
    import sys, tempfile
    from pathlib import Path
    try:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtGui import QFileOpenEvent
        from archy.app import _application_class, _install_file_open_handler
        app = _application_class()([])
    except Exception as exc:
        print("skip:", exc)
        sys.exit(77)

    folder = Path(tempfile.mkdtemp())
    first, second = folder / "a.zip", folder / "b.7z"
    first.write_bytes(b"x")
    second.write_bytes(b"x")
    opened = []

    class Window:
        def open_archive(self, path):
            opened.append(path.name)

    # Before the window exists (a double-click that starts the app): kept.
    QCoreApplication.sendEvent(app, QFileOpenEvent(str(first)))
    assert opened == [], opened
    sys.argv = sys.argv[:1]
    _install_file_open_handler(app, Window())
    assert opened == ["a.zip"], opened
    # Later (Open With while Archy runs): straight to the window.
    QCoreApplication.sendEvent(app, QFileOpenEvent(str(second)))
    assert opened == ["a.zip", "b.7z"], opened
    print("ok")
    """
)


def test_file_open_events_reach_the_window() -> None:
    import pytest

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(  # noqa: S603 - our own interpreter and script
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )
    if result.returncode == 77:
        pytest.skip("Qt GUI not available here")
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert result.stdout.strip().endswith("ok")


def test_no_application_wide_event_filter() -> None:
    source = (ROOT / "src" / "archy" / "app.py").read_text(encoding="utf-8")
    assert "app.installEventFilter" not in source
