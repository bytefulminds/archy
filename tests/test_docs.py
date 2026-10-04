"""The public docs stay consistent with the code: the version shown in the
README and the changelog, and links between README and MANUAL."""

from __future__ import annotations

import re
from pathlib import Path

import archy

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _anchors(markdown: str) -> set[str]:
    """GitLab/GitHub heading anchors: lowercase, punctuation dropped, spaces
    to hyphens."""
    anchors = set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*$", markdown, re.MULTILINE):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).strip().replace(" ", "-")
        anchors.add(slug)
    return anchors


def test_readme_shows_the_current_version() -> None:
    assert f"**Version {archy.__version__}**" in _read("README.md")


def test_changelog_top_release_is_the_current_version() -> None:
    releases = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", _read("CHANGELOG.md"), re.MULTILINE)
    assert releases and releases[0] == archy.__version__


def test_links_between_readme_and_manual_point_to_real_sections() -> None:
    documents = {name: _read(name) for name in ("README.md", "MANUAL.md")}
    for name, text in documents.items():
        for target, anchor in re.findall(r"\]\(((?:README|MANUAL)\.md)?#([\w-]+)\)", text):
            linked = documents[target or name]
            assert anchor in _anchors(linked), f"{name}: broken link to {target}#{anchor}"
