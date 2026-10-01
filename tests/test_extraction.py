from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from archy.core import backend_for
from archy.core.extraction import safe_join

_FILES = {
    "readme.txt": b"hello",
    "docs/guide.md": b"# guide\n",
    "img/logo.bin": bytes(range(32)),
}


def _make_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in _FILES.items():
            zf.writestr(name, data)
    return path


def _check_extracted(dest: Path) -> None:
    for name, data in _FILES.items():
        out = dest / name
        assert out.is_file(), name
        assert out.read_bytes() == data


# --------------------------------------------------------------- safe_join
def test_safe_join_rejects_traversal(tmp_path: Path) -> None:
    assert safe_join(tmp_path, "../evil.txt") is None
    assert safe_join(tmp_path, "a/../../b") is None
    # An absolute path is made relative under dest (leading slash stripped) - safe.
    abs_ok = safe_join(tmp_path, "/etc/passwd")
    assert abs_ok is not None and abs_ok == tmp_path / "etc" / "passwd"
    ok = safe_join(tmp_path, "a/b/c.txt")
    assert ok is not None and ok == tmp_path / "a" / "b" / "c.txt"


# ------------------------------------------------------------------ zip
def test_zip_extract_all(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    backend = backend_for(p)
    entries = list(backend.list_entries())
    dest = tmp_path / "out"
    result = backend.extract(entries, dest)
    assert result.extracted == 3
    assert result.failed == []
    assert not result.cancelled
    _check_extracted(dest)


def test_zip_extract_selected(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    backend = backend_for(p)
    entry = next(e for e in backend.list_entries() if e.path == "docs/guide.md")
    dest = tmp_path / "out"
    result = backend.extract([entry], dest)
    assert result.extracted == 1
    assert (dest / "docs" / "guide.md").read_bytes() == b"# guide\n"
    assert not (dest / "readme.txt").exists()


def test_zip_extract_rejects_zip_slip(tmp_path: Path) -> None:
    p = tmp_path / "evil.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("../escape.txt", b"pwned")
        zf.writestr("safe.txt", b"ok")
    backend = backend_for(p)
    entries = list(backend.list_entries())
    dest = tmp_path / "out"
    result = backend.extract(entries, dest)
    assert (dest / "safe.txt").read_bytes() == b"ok"
    assert not (tmp_path / "escape.txt").exists()
    assert any("onveilig" in reason for _, reason in result.failed)


def test_zip_extract_cancel(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    backend = backend_for(p)
    entries = list(backend.list_entries())
    dest = tmp_path / "out"
    result = backend.extract(entries, dest, is_cancelled=lambda: True)
    assert result.cancelled
    assert result.extracted == 0


def test_zip_extract_encrypted_with_password(tmp_path: Path) -> None:
    pyzipper = pytest.importorskip("pyzipper")
    p = tmp_path / "enc.zip"
    with pyzipper.AESZipFile(
        p, "w", compression=zipfile.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as z:
        z.setpassword(b"pw")
        z.writestr("secret.txt", b"classified")
    backend = backend_for(p)
    backend.set_password("pw")
    entries = list(backend.list_entries())
    dest = tmp_path / "out"
    result = backend.extract(entries, dest)
    assert result.extracted == 1
    assert (dest / "secret.txt").read_bytes() == b"classified"


# ------------------------------------------------------------ tar.gz
def test_targz_extract(tmp_path: Path) -> None:
    pytest.importorskip("libarchive")
    p = tmp_path / "a.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        for name, data in _FILES.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    backend = backend_for(p)
    entries = list(backend.list_entries())
    dest = tmp_path / "out"
    result = backend.extract(entries, dest)
    assert result.extracted == 3
    _check_extracted(dest)


# ------------------------------------------------------------------ 7z
def test_sevenzip_extract(tmp_path: Path) -> None:
    py7zr = pytest.importorskip("py7zr")
    p = tmp_path / "a.7z"
    with py7zr.SevenZipFile(p, "w") as z:
        for name, data in _FILES.items():
            z.writestr(data, name)
    backend = backend_for(p)
    entries = list(backend.list_entries())
    dest = tmp_path / "out"
    result = backend.extract(entries, dest)
    assert result.extracted == 3
    _check_extracted(dest)


def _make_big_7z(path: Path, count: int = 12, size: int = 400_000) -> dict[str, bytes]:
    import os

    py7zr = pytest.importorskip("py7zr")
    files = {f"map/f{i:02d}.bin": os.urandom(size) for i in range(count)}
    with py7zr.SevenZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(data, name)
    return files


def test_sevenzip_extract_reports_progress_while_running(tmp_path: Path) -> None:
    # py7zr's own extract said nothing until the end; Archy now reports as it
    # writes, so the status bar moves during a long solid-block decode.
    files = _make_big_7z(tmp_path / "a.7z")
    backend = backend_for(tmp_path / "a.7z")
    seen: list[tuple[int, int]] = []
    entries = list(backend.list_entries())
    result = backend.extract(
        entries, tmp_path / "out", on_progress=lambda d, t, n: seen.append((d, t))
    )
    assert result.extracted == len(files) and not result.failed
    total = sum(len(v) for v in files.values())
    assert len(seen) >= len(files)  # at least one update per file, not one at the end
    assert [d for d, _t in seen] == sorted(d for d, _t in seen)
    assert seen[-1] == (total, total)
    for name, data in files.items():
        assert (tmp_path / "out" / name).read_bytes() == data
    assert not list((tmp_path / "out").rglob("*.archy-part"))


def test_sevenzip_extract_cancel_leaves_no_half_files(tmp_path: Path) -> None:
    _make_big_7z(tmp_path / "a.7z")
    backend = backend_for(tmp_path / "a.7z")
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 5  # cancel partway through

    result = backend.extract(list(backend.list_entries()), tmp_path / "out", is_cancelled=cancel)
    assert result.cancelled
    written = [p for p in (tmp_path / "out").rglob("*") if p.is_file()]
    assert len(written) == result.extracted < 12  # only complete files remain
    assert not [p for p in written if p.name.endswith(".archy-part")]


def test_sevenzip_extract_wrong_password_reports_failures(tmp_path: Path) -> None:
    py7zr = pytest.importorskip("py7zr")
    p = tmp_path / "enc.7z"
    with py7zr.SevenZipFile(p, "w", password="goed") as z:
        z.writestr(b"x" * 5000, "a.txt")
    backend = backend_for(p)
    entries = list(backend.list_entries())
    backend.set_password("fout")
    result = backend.extract(entries, tmp_path / "out")
    assert result.extracted == 0
    assert result.failed  # counted honestly, not reported as extracted
    assert not [p for p in (tmp_path / "out").rglob("*") if p.is_file()]


def test_sevenzip_reads_blocks_one_at_a_time_and_frees_them(tmp_path: Path) -> None:
    # Given a path, py7zr decodes every block of an unencrypted archive at once
    # (a thread each) and keeps each block's decompressor until close: on a big
    # multi-block backup that ran out of memory. Archy opens it from a file
    # object (sequential) and releases each block when it is done.
    py7zr = pytest.importorskip("py7zr")
    from py7zr.io import BytesIOFactory

    from archy.core.backends.sevenzip_backend import _open_7z

    p = tmp_path / "a.7z"
    with py7zr.SevenZipFile(p, "w") as z:
        z.writestr(b"x" * 100_000, "a.bin")
        z.writestr(b"y" * 100_000, "b.bin")
    archive = _open_7z(p, None)
    try:
        assert archive._filePassed  # py7zr: file object -> no parallel decode
        archive.extract(targets=["a.bin", "b.bin"], factory=BytesIOFactory(1 << 20))
        folders = archive.header.main_streams.unpackinfo.folders
        assert all(folder.decompressor is None for folder in folders)  # released
    finally:
        archive.close()
