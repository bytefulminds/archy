"""Web pages from an archive rendered by QtWebEngine (services/web_view.py).

QtWebEngine has to be set up before the QApplication exists, and the other UI
tests already made one, so this runs in a fresh Python process. It skips where
QtWebEngine (or the Qt GUI stack) cannot load."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SKIP = 77

_SCRIPT = textwrap.dedent(
    """
    import sys, time
    try:
        from archy.services import web_view
        if not web_view.prepare():
            sys.exit(77)
        from PySide6.QtWidgets import QApplication
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except Exception as exc:
        print("skip:", exc)
        sys.exit(77)

    app = QApplication([])
    from archy.services import text_view
    from archy.services.providers import make_text_preview

    asked = []
    files = {
        "css/site.css": b"body { background: #00aa00; margin: 0; }"
                        b" .row { display: flex; gap: 10px; }",
    }

    def read(reference):
        asked.append(reference)
        return files.get(reference)

    read.base = "site"
    page = (
        '<html><head><link rel="stylesheet" href="css/site.css">'
        '<link rel="stylesheet" href="https://example.invalid/x.css"></head>'
        '<body><div class="row"><p>alpha</p><p>beta alpha</p></div>'
        '<img src="../../outside.png"><a href="https://example.invalid">x</a>'
        '</body></html>'
    )
    text_view._show_source["html"] = False
    widget, _edit = make_text_preview("index.html", page, editable=False, resources=read)
    widget.resize(500, 300)
    widget.show()
    view = widget.rendered
    assert type(view).__name__ == "QWebEngineView", type(view)
    loaded = []
    view.loadFinished.connect(loaded.append)

    def pump(seconds, until=lambda: False):
        end = time.monotonic() + seconds
        while time.monotonic() < end and not until():
            app.processEvents()
            time.sleep(0.01)

    pump(30, lambda: bool(loaded))
    assert loaded == [True], loaded
    pump(1.0)
    # The stylesheet came from the archive, relative to the page, and nothing
    # remote was asked for (the real reader also refuses anything above the
    # archive's top, see test_ui_smoke).
    assert "css/site.css" in asked, asked
    assert not any("example" in a for a in asked), asked
    color = view.grab().toImage().pixelColor(5, 5)
    assert (color.red(), color.green(), color.blue()) == (0, 170, 0), color.name()

    widget.open_find()
    widget.find_bar.field.setText("alpha")
    pump(5, lambda: widget.find_bar.counter.text() not in ("", "No matches"))
    assert widget.find_bar.counter.text() == "1/2", widget.find_bar.counter.text()
    widget.find_bar.step(1)
    pump(5, lambda: widget.find_bar.counter.text() == "2/2")
    assert widget.find_bar.counter.text() == "2/2", widget.find_bar.counter.text()
    print("ok")
    """
)


def test_html_page_renders_through_webengine_offline() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("ARCHY_NO_WEBENGINE", None)
    result = subprocess.run(  # noqa: S603 - our own interpreter and script
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )
    if result.returncode == _SKIP:
        pytest.skip("QtWebEngine is not available here")
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-4000:]
    assert result.stdout.strip().endswith("ok")
