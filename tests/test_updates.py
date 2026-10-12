"""The update check: asking GitLab for the latest release (services/updates.py)
and what the window does with the answer (ui/main_window/updates.py)."""

from __future__ import annotations

import io
import json

import pytest

from archy.services import updates


def _response(data: dict) -> object:
    class _Response(io.BytesIO):
        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *exc: object) -> None:
            self.close()

    return _Response(json.dumps(data).encode())


def test_versions_compare_as_numbers() -> None:
    assert updates.version_key("v0.10.0") == (0, 10, 0)
    assert updates.version_key("0.4") == (0, 4)
    assert updates.version_key("0.4.0rc1") is None
    assert updates.version_key("latest") is None
    assert updates.is_newer("v0.10.0", "0.9.9")
    assert not updates.is_newer("0.3.0", "0.3.0")
    assert not updates.is_newer("0.2.1", "0.3.0")
    assert not updates.is_newer("garbage", "0.3.0")


def test_the_latest_release_is_read_from_gitlab() -> None:
    asked = []

    def opener(request, timeout):  # noqa: ANN001, ANN202
        asked.append((request.full_url, request.get_header("User-agent"), timeout))
        return _response(
            {
                "tag_name": "v0.4.0",
                "_links": {"self": "https://gitlab.com/bytefulminds/archy/-/releases/v0.4.0"},
            }
        )

    release = updates.fetch_latest("0.3.0", opener=opener)
    assert release == updates.Release(
        "0.4.0", "https://gitlab.com/bytefulminds/archy/-/releases/v0.4.0"
    )
    url, agent, _timeout = asked[0]
    assert url == updates.LATEST_API
    assert agent.startswith("Archy/0.3.0 (")


def test_a_strange_answer_never_opens_another_site() -> None:
    def opener(request, timeout):  # noqa: ANN001, ANN202
        return _response({"tag_name": "v0.4.0", "_links": {"self": "https://evil.example/x"}})

    assert updates.fetch_latest("0.3.0", opener=opener).url == updates.RELEASES_PAGE


def test_a_failed_check_is_a_result_not_an_exception() -> None:
    def offline(request, timeout):  # noqa: ANN001, ANN202
        raise OSError("network is unreachable")

    result = updates.check("0.3.0", opener=offline)
    assert result.latest is None and "unreachable" in result.error

    def bad_tag(request, timeout):  # noqa: ANN001, ANN202
        return _response({"tag_name": "nightly"})

    assert updates.check("0.3.0", opener=bad_tag).latest is None


def test_only_a_newer_not_skipped_version_is_offered() -> None:
    newer = updates.Result(updates.Release("0.4.0", updates.RELEASES_PAGE))
    assert updates.to_offer(newer, "0.3.0") == newer.latest
    assert updates.to_offer(newer, "0.3.0", skipped="0.4.0") is None
    assert updates.to_offer(newer, "0.4.0") is None
    assert updates.to_offer(updates.Result(None, "offline"), "0.3.0") is None
    newest = updates.Result(updates.Release("0.5.0", updates.RELEASES_PAGE))
    assert updates.to_offer(newest, "0.3.0", skipped="0.4.0") == newest.latest


def test_certificates_load() -> None:
    context = updates._tls_context()
    assert context.get_ca_certs() or context.cert_store_stats()["x509"]


# ----------------------------------------------------------------- window
class _Settings:
    def __init__(self, check: str = "ask", skipped: str = "") -> None:
        self.values = {"updates/check": check, "updates/skipped": skipped}

    @property
    def update_check(self) -> str:
        return self.values["updates/check"]

    @property
    def skipped_version(self) -> str:
        return self.values["updates/skipped"]

    def set(self, key: str, value: object) -> None:
        self.values[key] = value

    def sync(self) -> None:
        pass


@pytest.fixture
def window(monkeypatch):  # noqa: ANN001, ANN201
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:  # noqa: BLE001 - no Qt GUI libs: skip
        pytest.skip(f"PySide6 GUI not available: {exc}")
    QApplication.instance() or QApplication([])
    from archy.ui.main_window import MainWindow

    win = MainWindow()
    real = win._settings
    win._settings = _Settings()
    win.checks = []
    monkeypatch.setattr(win, "_run_update_check", lambda *, manual: win.checks.append(manual))
    yield win
    win._settings = real
    win.close()


def _answer(monkeypatch, *answers: object) -> list[str]:  # noqa: ANN001
    """Let the dialogs answer in turn; returns the titles that were shown."""
    from archy.ui import soft_dialog

    shown: list[str] = []
    queue = list(answers)

    def confirm(parent, title, text, buttons):  # noqa: ANN001, ANN202
        shown.append(title)
        return queue.pop(0)

    monkeypatch.setattr(soft_dialog, "confirm", confirm)
    monkeypatch.setattr(soft_dialog, "message", lambda parent, title, text: shown.append(text))
    return shown


def test_the_first_start_asks_once(window, monkeypatch) -> None:  # noqa: ANN001
    shown = _answer(monkeypatch, "on")
    window._update_check_at_start()
    assert shown == ["Check for new versions?"]
    assert window._settings.update_check == "on" and window.checks == [False]
    shown.clear()
    window._update_check_at_start()  # decided: no question any more
    assert shown == [] and window.checks == [False, False]


def test_no_means_no_check(window, monkeypatch) -> None:  # noqa: ANN001
    _answer(monkeypatch, "off")
    window._update_check_at_start()
    assert window._settings.update_check == "off" and window.checks == []
    window._update_check_at_start()
    assert window.checks == []


def test_closing_the_question_asks_again_next_time(window, monkeypatch) -> None:  # noqa: ANN001
    _answer(monkeypatch, None)
    window._update_check_at_start()
    assert window._settings.update_check == "ask" and window.checks == []


def test_a_new_version_can_be_skipped(window, monkeypatch) -> None:  # noqa: ANN001
    from archy import __version__

    newer = ".".join(str(n + 1) for n in updates.version_key(__version__))
    result = updates.Result(updates.Release(newer, updates.RELEASES_PAGE))
    shown = _answer(monkeypatch, "skip")
    window._on_update_checked(result, False)
    assert shown == ["A new version of Archy"]
    assert window._settings.skipped_version == newer
    shown.clear()
    window._on_update_checked(result, False)  # at the next start: quiet
    assert shown == []
    shown = _answer(monkeypatch, "later")
    window._on_update_checked(result, True)  # "Check now" still shows it
    assert shown == ["A new version of Archy"]


def test_check_now_always_answers(window, monkeypatch) -> None:  # noqa: ANN001
    from archy import __version__

    shown = _answer(monkeypatch)
    window._on_update_checked(updates.Result(updates.Release(__version__, "x")), True)
    window._on_update_checked(updates.Result(None, "offline"), True)
    assert shown == [
        f"You have the latest version of Archy ({__version__}).",
        "Archy could not check for a new version: offline",
    ]
    shown.clear()
    window._on_update_checked(updates.Result(None, "offline"), False)  # at start: quiet
    assert shown == []
