"""Is there a newer Archy? Asks GitLab for the latest release.

The only network request Archy makes, and only when the user allowed it (see
Settings > General > Updates). It sends one GET to the public GitLab API, with
Archy's version and the operating system in the User-Agent, and nothing about
the user's files. Qt-free; the window runs it on a background thread.
"""

from __future__ import annotations

import json
import platform
import re
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

LATEST_API = "https://gitlab.com/api/v4/projects/bytefulminds%2Farchy/releases/permalink/latest"
RELEASES_PAGE = "https://gitlab.com/bytefulminds/archy/-/releases"
TIMEOUT = 8.0  # seconds; a slow or absent network just means no answer
_MAX_BYTES = 1024 * 1024  # a release's JSON is a few KB


@dataclass(frozen=True)
class Release:
    version: str  # "0.4.0"
    url: str  # the release's page on GitLab (notes and downloads)


@dataclass(frozen=True)
class Result:
    latest: Release | None  # None when the check failed
    error: str | None = None


def version_key(text: str) -> tuple[int, ...] | None:
    """'v0.4.0' or '0.4.0' -> (0, 4, 0); None for anything else (a
    pre-release such as '0.4.0rc1' is not offered)."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})", text.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(candidate: str, current: str) -> bool:
    new, old = version_key(candidate), version_key(current)
    return new is not None and old is not None and new > old


def _user_agent(current: str) -> str:
    return f"Archy/{current} ({platform.system()})"


def _tls_context() -> ssl.SSLContext:
    """Certificates from certifi, bundled with Archy: a packaged app cannot
    count on the Python it was built with finding the system's certificates
    (a Homebrew Python looks in /opt/homebrew, absent on a user's Mac)."""
    import certifi

    return ssl.create_default_context(cafile=certifi.where())


def _open(request: urllib.request.Request, timeout: float) -> object:
    return urllib.request.urlopen(request, timeout=timeout, context=_tls_context())


def fetch_latest(
    current: str,
    *,
    opener: Callable[..., object] = _open,
    timeout: float = TIMEOUT,
) -> Release:
    """The latest release on GitLab. Raises OSError/ValueError on failure."""
    request = urllib.request.Request(
        LATEST_API,
        headers={"User-Agent": _user_agent(current), "Accept": "application/json"},
    )
    with opener(request, timeout) as response:  # type: ignore[attr-defined]
        data = json.loads(response.read(_MAX_BYTES).decode("utf-8"))
    tag = str(data.get("tag_name", ""))
    key = version_key(tag)
    if key is None:
        raise ValueError(f"unexpected release tag {tag!r}")
    links = data.get("_links") or {}
    url = str(links.get("self") or RELEASES_PAGE)
    if not url.startswith("https://gitlab.com/"):
        url = RELEASES_PAGE  # only ever open our own release page
    return Release(".".join(str(part) for part in key), url)


def check(current: str, **kwargs: object) -> Result:
    """``fetch_latest`` that never raises: the error is in the result."""
    try:
        return Result(fetch_latest(current, **kwargs))  # type: ignore[arg-type]
    except (OSError, ValueError, urllib.error.URLError) as exc:
        reason = getattr(exc, "reason", None) or exc
        return Result(None, str(reason))


def to_offer(result: Result, current: str, skipped: str = "") -> Release | None:
    """The release to tell the user about at start-up: newer than this
    version and not the one they chose to skip."""
    latest = result.latest
    if latest is None or not is_newer(latest.version, current):
        return None
    if skipped and version_key(skipped) == version_key(latest.version):
        return None
    return latest
