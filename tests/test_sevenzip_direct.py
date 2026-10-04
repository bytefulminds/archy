"""Direct reads from 7z archives made with Store (no compression), encrypted
or not: any file, at any offset, without decoding its block from the start."""

from __future__ import annotations

import os
import random
import threading
from pathlib import Path

import pytest

py7zr = pytest.importorskip("py7zr")

from archy.core.backends.sevenzip_backend import SevenZipBackend  # noqa: E402
from archy.core.errors import EncryptedArchiveError  # noqa: E402

STORE = [{"id": py7zr.FILTER_COPY}]
STORE_AES = [{"id": py7zr.FILTER_COPY}, {"id": py7zr.FILTER_CRYPTO_AES256_SHA256}]
LZMA_AES = [{"id": py7zr.FILTER_LZMA2}, {"id": py7zr.FILTER_CRYPTO_AES256_SHA256}]


def _files() -> dict[str, bytes]:
    rng = random.Random(5)
    files = {f"photos/img{i:02d}.jpg": os.urandom(rng.randrange(1, 90_000)) for i in range(25)}
    files["photos/empty.txt"] = b""
    files["clips/clip.mp4"] = os.urandom(1_234_567)
    return files


def _make(path: Path, filters, password=None, header_encrypted=False) -> dict[str, bytes]:  # noqa: ANN001
    files = _files()
    with py7zr.SevenZipFile(
        path, "w", filters=filters, password=password, header_encryption=header_encrypted
    ) as archive:
        for name, data in files.items():
            archive.writestr(data, name)
    return files


def _open(path: Path, password: str | None = None) -> SevenZipBackend:
    backend = SevenZipBackend(path)
    backend.set_password(password)
    list(backend.list_entries())  # the listing builds the index
    return backend


def _entry(backend: SevenZipBackend, name: str):  # noqa: ANN202
    return next(e for e in backend.list_entries() if e.path == name)


@pytest.mark.parametrize(
    ("filters", "password", "header"),
    [(STORE, None, False), (STORE_AES, "geheim", False), (STORE_AES, "geheim", True)],
)
def test_stored_files_read_directly_whole_partly_and_as_a_stream(
    tmp_path, filters, password, header
) -> None:  # noqa: ANN001
    files = _make(tmp_path / "a.7z", filters, password, header)
    backend = _open(tmp_path / "a.7z", password)
    assert all(backend.can_stream(e) for e in backend.list_entries() if e.size)
    for name, data in files.items():
        entry = _entry(backend, name)
        assert backend.read_bytes(entry, cache=False) == data
        assert backend.read_bytes(entry, max_bytes=100, cache=False) == data[:100]
    clip = files["clips/clip.mp4"]
    stream = backend.open_stream(_entry(backend, "clips/clip.mp4"))
    assert stream is not None and stream.size == len(clip)
    rng = random.Random(1)
    with stream:
        for _ in range(100):
            start, length = rng.randrange(len(clip)), rng.randrange(1, 70_000)
            stream.seek(start)
            assert stream.read(length) == clip[start : start + length]
    backend.close()


def test_a_wrong_password_is_reported_not_returned_as_garbage(tmp_path) -> None:  # noqa: ANN001
    _make(tmp_path / "a.7z", STORE_AES, "geheim")  # names readable, data encrypted
    backend = _open(tmp_path / "a.7z", "wrong")
    entry = _entry(backend, "photos/img03.jpg")
    with pytest.raises(EncryptedArchiveError):
        backend.read_bytes(entry, cache=False)
    with pytest.raises(EncryptedArchiveError):
        backend.read_bytes(entry, max_bytes=10, cache=False)  # no CRC: checked once
    with pytest.raises(EncryptedArchiveError):
        backend.open_stream(_entry(backend, "clips/clip.mp4"))
    backend.set_password(None)
    list(backend.list_entries())
    with pytest.raises(EncryptedArchiveError):
        backend.open_stream(_entry(backend, "clips/clip.mp4"))
    backend.close()


def test_a_direct_read_does_not_wait_for_a_folder_pass(tmp_path) -> None:  # noqa: ANN001
    files = _make(tmp_path / "a.7z", STORE_AES, "geheim", header_encrypted=True)
    backend = _open(tmp_path / "a.7z", "geheim")
    entry = _entry(backend, "photos/img07.jpg")
    result: list[bytes] = []
    with backend._io_lock:  # what a running folder pass (or py7zr read) holds
        reader = threading.Thread(target=lambda: result.append(backend.read_bytes(entry)))
        reader.start()
        reader.join(timeout=10)
    assert result == [files["photos/img07.jpg"]]
    backend.close()


def test_compressed_archives_keep_using_py7zr(tmp_path) -> None:  # noqa: ANN001
    files = _make(tmp_path / "a.7z", LZMA_AES, "geheim")
    backend = _open(tmp_path / "a.7z", "geheim")
    entry = _entry(backend, "clips/clip.mp4")
    assert not backend.can_stream(entry)
    assert backend.open_stream(entry) is None
    assert backend.read_bytes(entry, cache=False) == files["clips/clip.mp4"]
    backend.close()


def test_renamed_and_edited_entries(tmp_path) -> None:  # noqa: ANN001
    files = _make(tmp_path / "a.7z", STORE_AES, "geheim")
    backend = _open(tmp_path / "a.7z", "geheim")
    backend.rename("photos/img01.jpg", "photos/renamed.jpg")
    renamed = next(e for e in backend.list_effective_entries() if e.path == "photos/renamed.jpg")
    assert backend.read_bytes(renamed, cache=False) == files["photos/img01.jpg"]
    backend.write_bytes("photos/img02.jpg", b"edited")
    edited = _entry(backend, "photos/img02.jpg")
    assert backend.read_bytes(edited, cache=False) == b"edited"
    assert backend.open_stream(edited) is None  # staged bytes are not in the archive
    backend.close()


def test_stream_entry_copies_a_stored_file_in_chunks(tmp_path) -> None:  # noqa: ANN001
    files = _make(tmp_path / "a.7z", STORE_AES, "geheim")
    backend = _open(tmp_path / "a.7z", "geheim")
    chunks: list[bytes] = []
    assert backend.stream_entry(_entry(backend, "clips/clip.mp4"), chunks.append)
    assert b"".join(chunks) == files["clips/clip.mp4"]
    backend.close()


def test_files_in_a_shared_block_have_no_compressed_size(tmp_path) -> None:  # noqa: ANN001
    # py7zr writes one solid block and reports the whole block's packed size on
    # its first file; that figure is meaningless per file, so none is shown.
    _make(tmp_path / "a.7z", STORE)
    backend = _open(tmp_path / "a.7z")
    assert all(e.compressed_size is None for e in backend.list_entries() if e.size)
    backend.close()


def test_a_store_archive_with_files_added_compressed_is_reported_as_mixed(tmp_path) -> None:  # noqa: ANN001
    # 7-Zip's drag and drop adds files with its default (LZMA2, solid) settings
    # to an archive that was made with Store.
    path = tmp_path / "mixed.7z"
    with py7zr.SevenZipFile(path, "w", filters=STORE) as archive:
        for i in range(4):
            archive.writestr(os.urandom(1000), f"stored/{i}.bin")
    with py7zr.SevenZipFile(path, "a") as archive:  # default: compressed
        for i in range(2):
            archive.writestr(b"text " * 200, f"added/{i}.txt")
    assert _open(path).mixed_storage() == (4, 2)

    _make(tmp_path / "store.7z", STORE)
    _make(tmp_path / "lzma.7z", LZMA_AES, "geheim")
    assert _open(tmp_path / "store.7z").mixed_storage() is None
    assert _open(tmp_path / "lzma.7z", "geheim").mixed_storage() is None
