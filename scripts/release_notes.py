"""The release notes for one version, taken from CHANGELOG.md.

Used by the release job in .gitlab-ci.yml (the text of the GitLab Release)
and by scripts/publish_build.py:

    python scripts/release_notes.py v0.2.0 > release_notes.md

Exits with an error when the tag does not match ``__version__`` in
src/archy/__init__.py or the changelog has no section for it, so a release
is never published with the wrong version or without notes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DOWNLOADS = """\
### Downloads

- **Windows**: `Archy-{version}-setup.exe`. Windows may show a SmartScreen
  warning because the installer is not signed: choose *More info* > *Run anyway*.
- **macOS**: `Archy-{version}-macos-arm64.dmg` (Apple Silicon) or
  `-x86_64.dmg` (Intel). Drag Archy to Applications. The app is not notarized
  by Apple: the first time, open it, close the warning, then go to *System
  Settings* > *Privacy & Security* and click *Open Anyway* (on macOS 14 and
  older: right-click Archy and choose *Open*).
- **Linux**: `Archy-{version}-linux-x86_64.AppImage`. Make it executable
  (`chmod +x`) and run it.

Not every file may be there right away: the Windows and macOS builds are added
after the Linux one.
"""


def app_version() -> str:
    text = (ROOT / "src" / "archy" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise SystemExit("No __version__ in src/archy/__init__.py")
    return match.group(1)


def version_of(tag: str) -> str:
    """'v0.2.0' -> '0.2.0', checked against __version__."""
    version = tag[1:] if tag.startswith("v") else tag
    if version != app_version():
        raise SystemExit(f"Tag {tag} does not match __version__ {app_version()}")
    return version


def changelog_section(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = re.compile(rf"^## \[{re.escape(version)}\][^\n]*\n", re.MULTILINE)
    match = heading.search(text)
    if match is None:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    following = re.compile(r"^## \[", re.MULTILINE).search(text, match.end())
    body = text[match.end() : following.start() if following else len(text)]
    # Reference links at the bottom of the changelog do not belong here.
    body = re.sub(r"^\[[^\]]+\]: \S+\s*$", "", body, flags=re.MULTILINE)
    return body.strip()


def notes(tag: str) -> str:
    version = version_of(tag)
    return f"{changelog_section(version)}\n\n{DOWNLOADS.format(version=version)}"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: release_notes.py <tag>", file=sys.stderr)
        return 2
    sys.stdout.write(notes(argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
