from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from archy.core import backend_for
from archy.core.backends.zip_backend import ZipBackend


def _make_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("keep/a.txt", "original a")
        zf.writestr("edit/note.txt", "before")
        zf.writestr("keep/b.bin", bytes(range(64)))
    return path


def _read_all(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i.filename) for i in zf.infolist()}


def test_write_bytes_and_save_in_memory(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    backend = backend_for(p)
    assert isinstance(backend, ZipBackend)
    assert backend.has_unsaved_changes is False

    backend.write_bytes("edit/note.txt", b"after edit")
    assert backend.has_unsaved_changes is True
    backend.save()
    assert backend.has_unsaved_changes is False

    contents = _read_all(p)
    assert contents["edit/note.txt"] == b"after edit"
    assert contents["keep/a.txt"] == b"original a"  # untouched entry preserved
    assert contents["keep/b.bin"] == bytes(range(64))
    assert not list(tmp_path.glob("*.zxtmp"))  # no temp file left behind


def test_unchanged_entries_are_copied_as_they_are(tmp_path: Path) -> None:
    # A save copies what did not change byte for byte (still compressed),
    # instead of unpacking and packing everything again.
    p = _make_zip(tmp_path / "a.zip")
    before = _raw_entries(p)
    backend = backend_for(p)
    backend.write_bytes("edit/note.txt", b"changed")
    backend.save()
    after = _raw_entries(p)
    assert after["keep/a.txt"] == before["keep/a.txt"]
    assert after["keep/b.bin"] == before["keep/b.bin"]
    assert after["edit/note.txt"] != before["edit/note.txt"]
    assert not list(tmp_path.glob("*.zxtmp"))


def _raw_entries(path: Path) -> dict[str, bytes]:
    """Each entry's stored bytes (compressed, encrypted as they are)."""
    out = {}
    with zipfile.ZipFile(path) as zf, open(path, "rb") as fh:
        for info in zf.infolist():
            fh.seek(info.header_offset + 26)
            skip = int.from_bytes(fh.read(2), "little") + int.from_bytes(fh.read(2), "little")
            fh.seek(skip, 1)
            out[info.filename] = fh.read(info.compress_size)
    return out


def test_add_and_remove_entries(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    backend = backend_for(p)
    backend.write_bytes("new/added.txt", b"hello new")
    backend.remove("keep/b.bin")
    backend.save()

    contents = _read_all(p)
    assert contents["new/added.txt"] == b"hello new"
    assert "keep/b.bin" not in contents
    assert contents["keep/a.txt"] == b"original a"


def test_save_no_pending_is_noop(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    before = p.read_bytes()
    backend = backend_for(p)
    backend.save()
    assert p.read_bytes() == before


def test_add_from_disk_and_rename(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    src = tmp_path / "from_disk.txt"
    src.write_bytes(b"disk bytes")
    backend = backend_for(p)

    backend.add(str(src), "added/from_disk.txt")
    backend.rename("edit/note.txt", "edit/renamed.txt")
    assert backend.has_unsaved_changes is True
    backend.save()

    contents = _read_all(p)
    assert contents["added/from_disk.txt"] == b"disk bytes"
    assert contents["edit/renamed.txt"] == b"before"  # content preserved
    assert "edit/note.txt" not in contents
    assert contents["keep/a.txt"] == b"original a"
    assert not list(tmp_path.glob("*.zxtmp"))


def test_effective_entries_reflect_pending(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    backend = backend_for(p)
    backend.write_bytes("new/x.txt", b"hi")
    backend.remove("keep/b.bin")
    backend.rename("keep/a.txt", "keep/a2.txt")

    paths = {e.path for e in backend.list_effective_entries()}
    assert "new/x.txt" in paths  # staged add is visible
    assert "keep/b.bin" not in paths  # staged delete is hidden
    assert "keep/a2.txt" in paths and "keep/a.txt" not in paths  # staged rename

    # a staged edit is served by read_bytes before save
    entry = next(e for e in backend.list_effective_entries() if e.path == "new/x.txt")
    assert backend.read_bytes(entry) == b"hi"


def test_add_dir(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    backend = backend_for(p)
    backend.add_dir("new/empty")
    assert backend.has_unsaved_changes is True
    # the staged empty folder shows up as a directory entry
    dirs = {e.path for e in backend.list_effective_entries() if e.is_dir}
    assert "new/empty/" in dirs
    backend.save()
    with zipfile.ZipFile(p) as zf:
        assert any(i.is_dir() and i.filename.rstrip("/") == "new/empty" for i in zf.infolist())


def test_discard_changes(tmp_path: Path) -> None:
    p = _make_zip(tmp_path / "a.zip")
    before = p.read_bytes()
    backend = backend_for(p)
    backend.write_bytes("new/x.txt", b"hi")
    backend.remove("keep/a.txt")
    assert backend.has_unsaved_changes is True
    backend.discard_changes()
    assert backend.has_unsaved_changes is False
    backend.save()  # nothing pending -> no-op
    assert p.read_bytes() == before


def test_pooled_readers_see_saved_changes(tmp_path):
    # Readers are pooled for speed; after save() the archive must be reopened
    # (and on Windows the pool must not keep the file open during the save).
    import zipfile

    from archy.core import backend_for

    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("x.txt", b"old")
    backend = backend_for(archive)
    entry = next(e for e in backend.list_entries() if e.path == "x.txt")
    assert backend.read_bytes(entry, cache=False) == b"old"
    backend.write_bytes("x.txt", b"new content")
    backend.save()
    entry = next(e for e in backend.list_entries() if e.path == "x.txt")
    assert backend.read_bytes(entry, cache=False) == b"new content"
    backend.close()
    assert backend._idle_readers == []


# ------------------------------------------------------- encrypted archives
def _zipcrypto_zip(path: Path, files: dict[str, bytes], password: bytes) -> None:
    """A zip with legacy ZipCrypto entries (stored), as made by Windows and
    older tools; Python cannot write those itself."""
    import os
    import struct
    import zlib

    def update(keys: list[int], byte: int) -> None:
        keys[0] = zlib.crc32(bytes([byte]), keys[0] ^ 0xFFFFFFFF) ^ 0xFFFFFFFF
        keys[1] = ((keys[1] + (keys[0] & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
        keys[2] = zlib.crc32(bytes([keys[1] >> 24]), keys[2] ^ 0xFFFFFFFF) ^ 0xFFFFFFFF

    def encrypt(data: bytes, crc: int) -> bytes:
        keys = [0x12345678, 0x23456789, 0x34567890]
        for byte in password:
            update(keys, byte)
        out = bytearray()
        for byte in os.urandom(11) + bytes([crc >> 24]) + data:
            temp = (keys[2] | 2) & 0xFFFF
            out.append(byte ^ (((temp * (temp ^ 1)) >> 8) & 0xFF))
            update(keys, byte)
        return bytes(out)

    body = bytearray()
    central = bytearray()
    for name, data in files.items():
        crc = zlib.crc32(data)
        enc = encrypt(data, crc)
        encoded = name.encode()
        offset = len(body)
        body += (
            struct.pack(
                "<4sHHHHHIIIHH",
                b"PK\x03\x04",
                20,
                1,
                0,
                0,
                0x21,
                crc,
                len(enc),
                len(data),
                len(encoded),
                0,
            )
            + encoded
            + enc
        )
        central += (
            struct.pack(
                "<4sHHHHHHIIIHHHHHII",
                b"PK\x01\x02",
                20,
                20,
                1,
                0,
                0,
                0x21,
                crc,
                len(enc),
                len(data),
                len(encoded),
                0,
                0,
                0,
                0,
                0,
                offset,
            )
            + encoded
        )
    end = struct.pack(
        "<4sHHHHIIH", b"PK\x05\x06", 0, 0, len(files), len(files), len(central), len(body), 0
    )
    path.write_bytes(bytes(body + central + end))


def _aes_zip(path: Path, files: dict[str, bytes], password: bytes) -> None:
    import pyzipper

    with pyzipper.AESZipFile(path, "w", compression=pyzipper.ZIP_DEFLATED) as zf:
        zf.setpassword(password)
        zf.setencryption(pyzipper.WZ_AES, nbits=256)
        for name, data in files.items():
            zf.writestr(name, data)


def _read_encrypted(path: Path, password: bytes) -> dict[str, bytes]:
    import pyzipper

    with pyzipper.AESZipFile(path) as zf:
        zf.setpassword(password)
        return {i.filename: zf.read(i.filename) for i in zf.infolist() if not i.is_dir()}


FILES = {
    "photos/a.jpg": bytes(range(256)) * 40,
    "notes/todo.txt": b"buy milk",
    "notes/old.txt": b"remove me",
    "big/video.mov": b"\x00\x01" * 50_000,
}


@pytest.mark.parametrize("make", [_zipcrypto_zip, _aes_zip], ids=["zipcrypto", "aes"])
def test_editing_an_encrypted_zip_keeps_it_encrypted(tmp_path: Path, make) -> None:  # noqa: ANN001
    import pyzipper

    path = tmp_path / "secret.zip"
    make(path, FILES, b"geheim")
    before = _raw_entries(path)
    backend = backend_for(path)
    backend.set_password("geheim")
    assert backend.edit_profile().format == "zip"
    backend.write_bytes("notes/todo.txt", b"buy milk and eggs")  # changed
    backend.write_bytes("notes/new.txt", b"brand new")  # added
    backend.remove("notes/old.txt")
    backend.rename("big/video.mov", "big/holiday.mov")
    backend.save()

    contents = _read_encrypted(path, b"geheim")
    assert contents == {
        "photos/a.jpg": FILES["photos/a.jpg"],
        "notes/todo.txt": b"buy milk and eggs",
        "notes/new.txt": b"brand new",
        "big/holiday.mov": FILES["big/video.mov"],
    }
    after = _raw_entries(path)
    # Untouched and renamed entries: the same encrypted bytes as before.
    assert after["photos/a.jpg"] == before["photos/a.jpg"]
    assert after["big/holiday.mov"] == before["big/video.mov"]
    # Every entry is encrypted; new and changed ones need the password too.
    with pyzipper.AESZipFile(path) as zf:
        assert all(i.flag_bits & 0x1 for i in zf.infolist())
        with pytest.raises(RuntimeError):
            zf.read("notes/new.txt")
        zf.setpassword(b"wrong")
        with pytest.raises(RuntimeError):
            zf.read("notes/todo.txt")
    assert not list(tmp_path.glob("*.zxtmp"))


def test_a_rename_does_not_read_the_file(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    path = tmp_path / "secret.zip"
    _aes_zip(path, FILES, b"geheim")
    backend = backend_for(path)
    backend.set_password("geheim")

    def no_reading(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("a rename must not read the entry")

    monkeypatch.setattr(backend, "read_bytes", no_reading)
    backend.rename("big/video.mov", "big/renamed.mov")
    names = {e.path for e in backend.list_effective_entries()}
    assert "big/renamed.mov" in names and "big/video.mov" not in names
    backend.save()
    assert "big/renamed.mov" in _read_encrypted(path, b"geheim")


def test_entries_with_a_data_descriptor_are_copied_whole(tmp_path: Path) -> None:
    # Written to a stream that cannot seek, zipfile puts the sizes after the
    # data (a data descriptor); those bytes must travel with the entry.
    import io

    class Unseekable(io.RawIOBase):
        def __init__(self) -> None:
            self.data = bytearray()

        def writable(self) -> bool:
            return True

        def write(self, b) -> int:  # noqa: ANN001
            self.data += b
            return len(b)

    sink = Unseekable()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.txt", b"first " * 100)
        with zf.open("b.bin", "w", force_zip64=True) as fh:  # a zip64 descriptor
            fh.write(b"second " * 100)
        zf.writestr("c.txt", b"third")
    path = tmp_path / "streamed.zip"
    path.write_bytes(bytes(sink.data))
    with zipfile.ZipFile(path) as zf:
        assert all(i.flag_bits & 0x08 for i in zf.infolist())
    backend = backend_for(path)
    backend.remove("c.txt")
    backend.write_bytes("d.txt", b"fourth")
    backend.save()
    assert _read_all(path) == {
        "a.txt": b"first " * 100,
        "b.bin": b"second " * 100,
        "d.txt": b"fourth",
    }
    with zipfile.ZipFile(path) as zf:
        assert zf.testzip() is None


def test_a_failed_check_leaves_the_original(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    from archy.core.errors import ArchiveError

    path = _make_zip(tmp_path / "a.zip")
    before = path.read_bytes()
    backend = backend_for(path)
    backend.write_bytes("edit/note.txt", b"after")

    def fail(*_args) -> None:  # noqa: ANN002
        raise ArchiveError("check failed")

    monkeypatch.setattr(backend, "_check_new", fail)
    with pytest.raises(ArchiveError):
        backend.save()
    assert path.read_bytes() == before
    assert backend.has_unsaved_changes  # still staged: nothing was lost
    assert not list(tmp_path.glob("*.zxtmp"))


def test_new_files_in_a_plain_zip_stay_plain(tmp_path: Path) -> None:
    path = _make_zip(tmp_path / "a.zip")
    backend = backend_for(path)
    backend.write_bytes("new.txt", b"plain")
    backend.save()
    with zipfile.ZipFile(path) as zf:
        assert not any(i.flag_bits & 0x1 for i in zf.infolist())
        assert zf.read("new.txt") == b"plain"


def test_save_reports_progress_and_can_be_cancelled(tmp_path: Path) -> None:
    from archy.core.backends.sevenzip_writer import SaveCancelled

    path = tmp_path / "secret.zip"
    _aes_zip(path, FILES, b"geheim")
    before = path.read_bytes()
    backend = backend_for(path)
    backend.set_password("geheim")
    backend.write_bytes("notes/new.txt", b"brand new")
    plan = backend.save_plan()
    assert plan["files"] == len(FILES) + 1 and plan["needed"] > plan["bytes"] > 0

    with pytest.raises(SaveCancelled):
        backend.save(is_cancelled=lambda: True)
    assert path.read_bytes() == before and backend.has_unsaved_changes
    assert not list(tmp_path.glob("*.zxtmp"))

    seen: list[tuple[int, int, str, int]] = []
    backend.save(on_progress=lambda *args: seen.append(args))
    done = [d for d, _t, _n, _f in seen]
    assert done == sorted(done) and seen[-1][0] == seen[-1][1]
    assert seen[-1][3] == len(FILES) + 1
