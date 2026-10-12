"""requirements/lock.txt (what every build installs) agrees with pyproject.toml."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

packaging = pytest.importorskip("packaging")
from packaging.requirements import Requirement  # noqa: E402
from packaging.utils import canonicalize_name  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _locked() -> dict[str, str]:
    text = (ROOT / "requirements" / "lock.txt").read_text(encoding="utf-8")
    return {
        canonicalize_name(name): version
        for name, version in re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", text, re.MULTILINE)
    }


def test_every_dependency_is_locked_within_its_range() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    wanted = [*project["dependencies"], *project["optional-dependencies"]["dev"]]
    locked = _locked()
    for line in wanted:
        requirement = Requirement(line)
        name = canonicalize_name(requirement.name)
        assert name in locked, f"{name} is not in requirements/lock.txt: lock again"
        assert requirement.specifier.contains(locked[name], prereleases=True), (
            f"{name}=={locked[name]} is outside {requirement.specifier}: lock again"
        )


def test_the_build_tools_are_locked() -> None:
    locked = _locked()
    for name in ("pyinstaller", "hatchling", "editables"):
        assert name in locked
