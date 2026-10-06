from __future__ import annotations

import contextlib
import mimetypes
import os
import posixpath
import secrets
from collections.abc import Callable

from PySide6.QtCore import QBuffer, QByteArray, QCoreApplication, QObject, Qt, QUrl
from PySide6.QtWidgets import QWidget

# Web pages from an archive rendered by Chromium (QtWebEngine), so their CSS
# is shown in full: flexbox, grid, gradients, web fonts packed next to the
# page, and so on. QTextBrowser (services/text_view.py) only knows a small part
# of CSS and stays the fallback where QtWebEngine is not available.
#
# The page is just as offline and in-memory as every other preview:
#
# - The profile is off the record: no cache, cookies or storage on disk.
# - The page and everything it refers to come from a private URL scheme
#   (archy-page://<token>/<path in the archive>) served from memory by
#   _ArchiveSchemeHandler, which only reads files from the same archive.
# - Every request for anything else (http, https, file, ...) is blocked by
#   _OfflineInterceptor; Chromium already refuses remote URLs from a local
#   scheme, this is the second lock on the door.
# - JavaScript, plugins and the built-in PDF viewer are off, links do not
#   navigate, new windows are refused and downloads are cancelled.
#
# QtWebEngine needs its scheme registered (and OpenGL contexts shared) before
# the QApplication exists: call prepare() first. Without that, available()
# is False and the text view uses QTextBrowser.

SCHEME = b"archy-page"

ResourceReader = Callable[[str], "bytes | None"]

_prepared = False
_runtime: _Runtime | None = None


def prepare() -> bool:
    """Register the page scheme; must run before the QApplication is created.
    Returns whether QtWebEngine can be used."""
    global _prepared
    if _prepared:
        return True
    if os.environ.get("ARCHY_NO_WEBENGINE"):
        return False
    if QCoreApplication.instance() is not None:
        return False  # too late: the scheme cannot be registered any more
    try:
        from PySide6.QtWebEngineCore import QWebEngineUrlScheme
    except Exception:  # noqa: BLE001 - QtWebEngine is optional
        return False
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        # Chromium's sandbox refuses to run as root (containers, CI).
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    scheme = QWebEngineUrlScheme(SCHEME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.LocalScheme
        | QWebEngineUrlScheme.Flag.LocalAccessAllowed
    )
    QWebEngineUrlScheme.registerScheme(scheme)
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    _prepared = True
    return True


def available() -> bool:
    """True when pages can be shown with QtWebEngine in this process."""
    return _prepared and QCoreApplication.instance() is not None


class _Page:
    def __init__(self, path: str, base: str, resources: ResourceReader | None) -> None:
        self.html = b""
        self.path = path  # the page's path in the archive
        self.base = base  # its folder ("" at the top)
        self.resources = resources


class _Runtime:
    """The shared off-the-record profile with the scheme handler and the
    request blocker, created on first use."""

    def __init__(self) -> None:
        from PySide6.QtWebEngineCore import (
            QWebEngineProfile,
            QWebEngineSettings,
            QWebEngineUrlRequestInterceptor,
            QWebEngineUrlRequestJob,
            QWebEngineUrlSchemeHandler,
        )

        pages: dict[str, _Page] = {}
        self.pages = pages

        class _ArchiveSchemeHandler(QWebEngineUrlSchemeHandler):
            def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:  # noqa: N802
                url = job.requestUrl()
                page = pages.get(url.host())
                data = _serve(page, url.path()) if page is not None else None
                if data is None:
                    job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                    return
                body, mime = data
                buffer = QBuffer(job)  # lives as long as the request
                buffer.setData(QByteArray(body))
                buffer.open(QBuffer.OpenModeFlag.ReadOnly)
                job.reply(mime, buffer)

        class _OfflineInterceptor(QWebEngineUrlRequestInterceptor):
            def interceptRequest(self, info) -> None:  # noqa: ANN001, N802
                if info.requestUrl().scheme() not in (SCHEME.decode(), "data"):
                    info.block(True)

        app = QCoreApplication.instance()
        profile = QWebEngineProfile(app)  # no storage name: off the record
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        profile.setSpellCheckEnabled(False)
        settings = profile.settings()
        attribute = QWebEngineSettings.WebAttribute
        for name, value in (
            ("JavascriptEnabled", False),
            ("PluginsEnabled", False),
            ("PdfViewerEnabled", False),
            ("LocalContentCanAccessRemoteUrls", False),
            ("LocalContentCanAccessFileUrls", False),
            ("AutoLoadIconsForPage", False),
            ("DnsPrefetchEnabled", False),
            ("NavigateOnDropEnabled", False),
            ("ErrorPageEnabled", False),
            ("ScreenCaptureEnabled", False),
            ("WebGLEnabled", False),
        ):
            with contextlib.suppress(AttributeError):
                settings.setAttribute(getattr(attribute, name), value)
        self.handler = _ArchiveSchemeHandler(profile)
        self.interceptor = _OfflineInterceptor(profile)
        profile.installUrlSchemeHandler(SCHEME, self.handler)
        profile.setUrlRequestInterceptor(self.interceptor)
        profile.downloadRequested.connect(lambda download: download.cancel())
        self.profile = profile
        # Pages must go before their profile: Chromium crashes when the profile
        # is released while a page still uses it (at quit, the QApplication
        # would delete the profile before the window and its preview).
        self.views: dict[str, QWidget] = {}
        app.aboutToQuit.connect(self.release_views)

    def release_views(self) -> None:
        import shiboken6

        for view in list(self.views.values()):
            with contextlib.suppress(RuntimeError):  # already gone
                if shiboken6.isValid(view):
                    shiboken6.delete(view)
        self.views.clear()


def _serve(page: _Page, path: str) -> tuple[bytes, bytes] | None:
    """(content, mime type) for ``path`` (absolute, as in the URL)."""
    path = path.lstrip("/")
    mime = (mimetypes.guess_type(path)[0] or "application/octet-stream").encode()
    if path == page.path:
        return page.html, b"text/html;charset=utf-8"
    if page.resources is None:
        return None
    reference = posixpath.relpath(path, page.base or ".")
    data = page.resources(reference)
    return None if data is None else (data, mime)


def _runtime_instance() -> _Runtime:
    global _runtime
    if _runtime is None:
        _runtime = _Runtime()
    return _runtime


def make_web_view(name: str, resources: ResourceReader | None) -> QWidget:
    """A view for an HTML page from the archive; give it the text with
    ``view.set_html(text)``. Requires available()."""
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView

    runtime = _runtime_instance()
    token = secrets.token_hex(8)
    base = str(getattr(resources, "base", "") or "").strip("/")

    class _OfflinePage(QWebEnginePage):
        def acceptNavigationRequest(self, url: QUrl, kind, main_frame: bool) -> bool:  # noqa: ANN001, N802
            # Only our own load of the page; links do not navigate away.
            return kind != QWebEnginePage.NavigationType.NavigationTypeLinkClicked and (
                url.scheme() == SCHEME.decode() and url.host() == token
            )

        def createWindow(self, _kind):  # noqa: ANN001, ANN202, N802
            return None

    view = QWebEngineView()
    view.setObjectName("webPage")
    page = _OfflinePage(runtime.profile, view)
    view.setPage(page)
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
    view.addAction(page.action(QWebEnginePage.WebAction.Copy))
    view.addAction(page.action(QWebEnginePage.WebAction.SelectAll))

    entry = _Page(posixpath.join(base, name) if base else name, base, resources)
    runtime.pages[token] = entry

    runtime.views[token] = view

    def forget(_obj: QObject | None = None, key: str = token) -> None:
        runtime.pages.pop(key, None)
        runtime.views.pop(key, None)

    view.destroyed.connect(forget)

    def set_html(text: str) -> None:
        entry.html = text.encode("utf-8")
        url = QUrl()
        url.setScheme(SCHEME.decode())
        url.setHost(token)
        url.setPath("/" + entry.path)
        view.load(url)

    view.set_html = set_html  # type: ignore[attr-defined]
    return view


class WebFindTarget:
    """Find in a web page (for the FindBar): Chromium searches and highlights,
    and reports the number of matches afterwards, so this target answers
    through ``on_result(count, index)`` instead of at once."""

    asynchronous = True

    def __init__(self, view: QWidget) -> None:
        self._view = view
        self._text = ""
        self.on_result: Callable[[int, int], None] | None = None

    def search(self, text: str) -> int:
        self._text = text
        self._find(backward=False)
        return 0

    def step(self, direction: int) -> None:
        if self._text:
            self._find(backward=direction < 0)

    def _find(self, *, backward: bool) -> None:
        from PySide6.QtWebEngineCore import QWebEnginePage

        flags = QWebEnginePage.FindFlag.FindBackward if backward else QWebEnginePage.FindFlag(0)

        def done(result) -> None:  # noqa: ANN001 - QWebEngineFindTextResult
            if self.on_result is not None:
                count = result.numberOfMatches()
                self.on_result(count, result.activeMatch() - 1 if count else -1)

        self._view.page().findText(self._text, flags, done)  # type: ignore[attr-defined]

    def first_after_cursor(self) -> int:
        return 0

    def go(self, _index: int) -> None:
        pass  # Chromium already moved to the match

    def clear(self) -> None:
        self._text = ""
        self.on_result = None
        self._view.page().findText("")  # type: ignore[attr-defined]
