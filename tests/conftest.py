from __future__ import annotations

import pytest

from archy import i18n
from archy.core import memory

_GB = 1024 * 1024 * 1024


@pytest.fixture(autouse=True)
def _fixed_memory(monkeypatch):
    """Tests must not depend on how much RAM the test machine has free: pin a
    16 GB machine with 12 GB available and Archy itself using 1 GB, with the
    default (Auto) limits. Tests about the policy re-patch these."""
    monkeypatch.setattr(memory, "total", lambda: 16 * _GB)
    monkeypatch.setattr(memory, "available", lambda: 12 * _GB)
    monkeypatch.setattr(memory, "process_rss", lambda: 1 * _GB)
    memory.configure(memory.Limits())
    yield
    memory.configure(memory.Limits())


@pytest.fixture(autouse=True)
def _english_ui(monkeypatch, tmp_path_factory):
    """Tests run with the English UI texts and only the bundled catalogs
    (no language files from the developer's own config folder)."""
    monkeypatch.setattr(
        i18n, "user_language_dir", lambda: tmp_path_factory.getbasetemp() / "no-langs"
    )
    i18n.catalogs(reload=True)
    i18n.set_language("en")
    yield
    i18n.catalogs(reload=True)
    i18n.set_language("en")
