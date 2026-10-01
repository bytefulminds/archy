from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from archy.core import backend_for
from archy.core.backends.sevenzip_backend import SevenZipBackend
from archy.core.backends.zip_backend import ZipBackend
from archy.core.errors import EncryptedArchiveError


def test_aes_zip_read_with_password(tmp_path: Path) -> None:
    pyzipper = pytest.importorskip("pyzipper")
    p = tmp_path / "aes.zip"
    with pyzipper.AESZipFile(
        p, "w", compression=zipfile.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as z:
        z.setpassword(b"hunter2")
        z.writestr("secret.txt", b"top secret")

    backend = backend_for(p)
    assert isinstance(backend, ZipBackend)
    entry = next(e for e in backend.list_entries() if e.path == "secret.txt")
    assert entry.is_encrypted

    with pytest.raises(EncryptedArchiveError):
        backend.read_bytes(entry)  # no password

    backend.set_password("nope")
    with pytest.raises(EncryptedArchiveError):
        backend.read_bytes(entry)  # wrong password

    backend.set_password("hunter2")  # session password
    assert backend.read_bytes(entry) == b"top secret"

    fresh = backend_for(p)
    assert fresh.read_bytes(entry, password="hunter2") == b"top secret"  # explicit
    assert fresh.read_bytes(entry, password="hunter2", max_bytes=3) == b"top"


def test_zipcrypto_read_with_password(tmp_path: Path) -> None:
    if shutil.which("zip") is None:
        pytest.skip("zip CLI not available")
    src = tmp_path / "f.txt"
    src.write_text("legacy secret")
    p = tmp_path / "zc.zip"
    subprocess.run(
        ["zip", "-j", "-P", "hunter2", str(p), str(src)],
        check=True,
        capture_output=True,
    )

    backend = backend_for(p)
    entry = next(e for e in backend.list_entries() if e.path == "f.txt")
    assert entry.is_encrypted
    with pytest.raises(EncryptedArchiveError):
        backend.read_bytes(entry)
    backend.set_password("hunter2")
    assert backend.read_bytes(entry) == b"legacy secret"


def test_7z_header_encrypted_list_and_read(tmp_path: Path) -> None:
    py7zr = pytest.importorskip("py7zr")
    p = tmp_path / "enc.7z"
    with py7zr.SevenZipFile(p, "w", password="s3cr3t", header_encryption=True) as z:
        z.writestr("hidden data", "hidden.txt")

    backend = backend_for(p)
    assert isinstance(backend, SevenZipBackend)
    # Header-encrypted: cannot even list without the password.
    with pytest.raises(EncryptedArchiveError):
        list(backend.list_entries())

    backend.set_password("s3cr3t")
    entries = list(backend.list_entries())
    entry = next(e for e in entries if e.path == "hidden.txt")
    assert backend.read_bytes(entry) == b"hidden data"


def test_7z_data_encrypted_wrong_then_right(tmp_path: Path) -> None:
    py7zr = pytest.importorskip("py7zr")
    p = tmp_path / "enc2.7z"
    with py7zr.SevenZipFile(p, "w", password="right") as z:
        z.writestr("payload", "a.txt")

    backend = backend_for(p)
    entries = list(backend.list_entries())  # names visible (header not encrypted)
    entry = next(e for e in entries if e.path == "a.txt")

    backend.set_password("wrong")
    with pytest.raises(EncryptedArchiveError):
        backend.read_bytes(entry)

    backend.set_password("right")
    assert backend.read_bytes(entry) == b"payload"


def test_7z_key_is_derived_once_per_password(tmp_path):
    # py7zr re-derives the AES key (2^19 SHA-256 rounds) for every block; in a
    # non-solid encrypted archive that is every file. Archy caches it.
    import io

    import pytest

    py7zr = pytest.importorskip("py7zr")
    from archy.core import backend_for

    archive = tmp_path / "enc.7z"
    with py7zr.SevenZipFile(str(archive), "w", password="geheim") as z:
        for i in range(3):
            z.writef(io.BytesIO(bytes([i]) * 1000), f"f{i}.bin")
    backend = backend_for(archive)
    backend.set_password("geheim")
    import py7zr.compressor as compressor

    assert hasattr(compressor.calculate_key, "cache_info")
    for entry in (e for e in backend.list_entries() if not e.is_dir):
        assert backend.read_bytes(entry, cache=False) == bytes([int(entry.path[1])]) * 1000
    assert compressor.calculate_key.cache_info().hits > 0
    backend.close()


def test_7z_encrypted_header_wrong_password_asks_again(tmp_path):
    # "Encrypt file names": a wrong password must come back as a password
    # error (so Archy asks again), not as an unexpected decoder error.
    import io

    import pytest

    py7zr = pytest.importorskip("py7zr")
    from archy.core import backend_for
    from archy.core.errors import EncryptedArchiveError

    archive = tmp_path / "hdr.7z"
    with py7zr.SevenZipFile(str(archive), "w", password="goed", header_encryption=True) as z:
        z.writef(io.BytesIO(b"x" * 2000), "a.txt")
    backend = backend_for(archive)
    with pytest.raises(EncryptedArchiveError):
        list(backend.list_entries())  # no password yet
    backend.set_password("fout")
    with pytest.raises(EncryptedArchiveError):
        list(backend.list_entries())
    backend.set_password("goed")
    assert [e.path for e in backend.list_entries()] == ["a.txt"]
    backend.close()


def test_7z_with_more_than_65536_files_opens(tmp_path, monkeypatch):
    # py7zr rejects archives with more than 65,536 streams ("numstreams ...
    # exceeds limit"); a non-solid backup has one per file. Archy raises the
    # limit. Simulated with a lowered limit to keep the test small.
    import io

    import pytest

    py7zr = pytest.importorskip("py7zr")
    import py7zr.archiveinfo as archiveinfo

    from archy.core import backend_for
    from archy.core.backends import sevenzip_backend

    archive = tmp_path / "a.7z"
    with py7zr.SevenZipFile(str(archive), "w") as z:
        z.writef(io.BytesIO(b"x" * 100), "a.txt")
    monkeypatch.setattr(archiveinfo, "MAX_NUMSTREAMS", 0)  # py7zr's guard trips
    monkeypatch.setattr(sevenzip_backend, "_streams_raised", False)
    backend = backend_for(archive)
    assert [e.path for e in backend.list_entries()] == ["a.txt"]
    assert archiveinfo.MAX_NUMSTREAMS >= 16 * 1024 * 1024
    backend.close()
