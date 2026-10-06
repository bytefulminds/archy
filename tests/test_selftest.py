"""``archy --self-test`` (selftest.py) passes in a complete installation."""

from __future__ import annotations

import pytest

try:
    from PySide6.QtWidgets import QApplication as _QApplication  # noqa: F401
except Exception as _exc:  # noqa: BLE001 - missing Qt libs -> skip, don't fail
    pytest.skip(f"PySide6 GUI not available: {_exc}", allow_module_level=True)

from archy import selftest  # noqa: E402


def test_self_test_passes_and_reports_each_check() -> None:
    lines: list[str] = []
    assert selftest.run(lines.append) == 0
    assert lines[-1] == "self-test passed"
    assert "ok    import py7zr" in lines and "ok    7z round trip" in lines
    assert not [line for line in lines if line.startswith("FAIL")]


def test_self_test_reports_a_missing_module(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(selftest, "MODULES", ("archy_no_such_module",))
    lines: list[str] = []
    assert selftest.run(lines.append) == 1
    assert any(line.startswith("FAIL  import archy_no_such_module") for line in lines)
