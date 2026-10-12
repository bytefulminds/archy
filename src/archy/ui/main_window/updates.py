from __future__ import annotations

import contextlib
import threading

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices

from archy import __version__
from archy.i18n import tr
from archy.services import updates
from archy.ui import soft_dialog

# How long after the window appears the start-up check runs: the window (and
# an archive opened from the command line) comes first.
_START_DELAY_MS = 2500


class _Checker(QObject):
    """Runs one update check on a background thread; ``done`` arrives on the
    UI thread (the receiver lives there, so Qt queues it)."""

    done = Signal(object, bool)  # updates.Result, asked by the user

    def start(self, manual: bool) -> None:
        def run() -> None:
            result = updates.check(__version__)
            with contextlib.suppress(RuntimeError):  # the window closed meanwhile
                self.done.emit(result, manual)

        threading.Thread(target=run, name="archy-update-check", daemon=True).start()


class UpdatesMixin:
    """Tells the user about a newer Archy: asked once whether Archy may check
    at start-up, then a short dialog with Download / Later / Skip this
    version. Installing is up to the user for now (the download page opens).

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    def schedule_update_check(self) -> None:
        """Called by app.run() once the window shows (never in tests)."""
        QTimer.singleShot(_START_DELAY_MS, self._update_check_at_start)

    def check_for_updates_now(self) -> None:
        """Settings > General > Check now: always checks, always answers."""
        self._run_update_check(manual=True)

    # ---------------------------------------------------------------- flow
    def _update_check_at_start(self) -> None:
        choice = self._settings.update_check
        if choice == "ask":
            choice = self._ask_about_update_checks()
        if choice == "on":
            self._run_update_check(manual=False)

    def _ask_about_update_checks(self) -> str:
        answer = soft_dialog.confirm(
            self,
            tr("update.ask_title"),
            tr("update.ask_text"),
            [(tr("update.ask_no"), "off", False), (tr("update.ask_yes"), "on", True)],
        )
        if answer in ("on", "off"):
            self._settings.set("updates/check", answer)
            self._settings.sync()
            return str(answer)
        return "ask"  # closed without an answer: ask again next time

    def _run_update_check(self, *, manual: bool) -> None:
        checker = getattr(self, "_update_checker", None)
        if checker is None:
            checker = _Checker(self)
            checker.done.connect(self._on_update_checked)
            self._update_checker = checker
        checker.start(manual)

    def _on_update_checked(self, result: updates.Result, manual: bool) -> None:
        if manual:
            if result.latest is None:
                soft_dialog.message(
                    self, tr("update.title"), tr("update.failed", reason=result.error or "?")
                )
                return
            if not updates.is_newer(result.latest.version, __version__):
                text = tr("update.latest", current=__version__)
                soft_dialog.message(self, tr("update.title"), text)
                return
            self._offer_update(result.latest)
            return
        release = updates.to_offer(result, __version__, self._settings.skipped_version)
        if release is not None:
            self._offer_update(release)

    def _offer_update(self, release: updates.Release) -> None:
        answer = soft_dialog.confirm(
            self,
            tr("update.available_title"),
            tr("update.available_text", version=release.version, current=__version__),
            [
                (tr("update.skip"), "skip", False),
                (tr("update.later"), "later", False),
                (tr("update.download"), "download", True),
            ],
        )
        if answer == "download":
            QDesktopServices.openUrl(QUrl(release.url))
        elif answer == "skip":
            self._settings.set("updates/skipped", release.version)
            self._settings.sync()
        # "later" or closed: asked again at the next start.
