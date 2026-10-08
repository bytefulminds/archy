"""Archy's own reader for compressed solid 7z blocks (sevenzip_solid.py):
jumps to dictionary resets, keeps decoders where they stopped, and hands over
the same bytes py7zr would."""

from __future__ import annotations

import lzma
import os
import random
import zlib

import py7zr
import pytest

from archy.core.backends.sevenzip_solid import (
    _AES,
    _LZMA2,
    Block,
    Member,
    SolidIndex,
    SolidReader,
)
from archy.core.errors import ArchiveError, EncryptedArchiveError
from archy.core.factory import backend_for

MB = 1024 * 1024


def _text(size: int, seed: int) -> bytes:
    rng = random.Random(seed)
    words = [f"word{i}" for i in range(500)]
    out = bytearray()
    while len(out) < size:
        out += (" ".join(rng.choice(words) for _ in range(20)) + "\n").encode()
    return bytes(out[:size])


def _multithreaded_lzma2(pieces: list[bytes], dict_size: int) -> bytes:
    """An LZMA2 stream the way multi-threaded 7-Zip writes one: each piece
    compressed on its own, so each starts with a dictionary reset."""
    filters = [{"id": lzma.FILTER_LZMA2, "dict_size": dict_size}]
    stream = b""
    for piece in pieces:
        raw = lzma.compress(piece, format=lzma.FORMAT_RAW, filters=filters)
        stream += raw[:-1]  # without its end marker: the pieces follow each other
    return stream + b"\x00"


def _lzma2_props(dict_size: int) -> bytes:
    for p in range(41):
        if (2 | (p & 1)) << (p // 2 + 11) >= dict_size:
            return bytes([p])
    raise ValueError(dict_size)


def _block_file(tmp_path, files: list[tuple[str, bytes]], piece: int, aes=None):  # noqa: ANN001, ANN202
    """A file holding one synthetic solid block (after a small header) and
    the Block/Member descriptions SolidIndex.build would give for it."""
    data = b"".join(content for _name, content in files)
    dict_size = 1 * MB
    pieces = [data[i : i + piece] for i in range(0, len(data), piece)]
    coded = _multithreaded_lzma2(pieces, dict_size)
    packed = coded
    properties = None
    if aes is not None:
        from Cryptodome.Cipher import AES
        from py7zr.compressor import calculate_key

        iv = os.urandom(16)
        properties = bytes([0x40 | 6, 0x0F]) + iv  # cycles power 6, no salt, 16-byte IV
        key = calculate_key(aes.encode("utf-16LE"), 6, b"", "sha256")
        padded = coded + bytes(-len(coded) % 16)
        packed = AES.new(key, AES.MODE_CBC, iv).encrypt(padded)
    path = tmp_path / "block.bin"
    path.write_bytes(b"HEADER--" + packed + b"TRAILER")
    block = Block(
        index=0,
        start=8,
        packed=len(packed),
        coded=len(coded),
        size=len(data),
        method=_LZMA2,
        props=_lzma2_props(dict_size),
        aes=properties,
    )
    members = []
    offset = 0
    for name, content in files:
        members.append(Member(name, offset, len(content), zlib.crc32(content)))
        offset += len(content)
    return path, block, members


def _collect(into: dict[str, bytes]):  # noqa: ANN202
    return lambda member, data: into.__setitem__(member.name, data)


def _folders(count: int, per: int, size: int) -> list[tuple[str, bytes]]:
    return [
        (f"f{folder:02d}/file{i}.txt", _text(size, folder * 100 + i))
        for folder in range(count)
        for i in range(per)
    ]


@pytest.mark.parametrize("password", [None, "geheim"])
def test_jumps_to_the_reset_before_a_folder(tmp_path, password) -> None:  # noqa: ANN001
    files = _folders(8, 4, 256 * 1024)  # 8 MB, a reset every 2 MB
    path, block, members = _block_file(tmp_path, files, 2 * MB, aes=password)
    reader = SolidReader(path, SolidIndex({}))
    wanted = [m for m in members if m.name.startswith("f07/")]  # the end of the block
    got: dict[str, bytes] = {}
    reader.read_group(
        [(block, m) for m in wanted], password, lambda m, d: got.__setitem__(m.name, d)
    )
    assert got == {name: content for name, content in files if name.startswith("f07/")}
    assert reader.reset_points(block) >= 4  # found by walking the chunk headers
    assert reader.decoded <= 2 * MB  # one piece at most, not the 7 MB before it
    reader.close()


def test_continues_where_it_stopped_for_the_next_folder(tmp_path) -> None:  # noqa: ANN001
    files = _folders(6, 3, 300 * 1024)
    path, block, members = _block_file(tmp_path, files, 64 * MB)  # one piece: no resets
    reader = SolidReader(path, SolidIndex({}))
    for folder in range(6):
        before = reader.decoded
        wanted = [m for m in members if m.name.startswith(f"f{folder:02d}/")]
        got: dict[str, bytes] = {}
        reader.read_group([(block, m) for m in wanted], None, _collect(got))
        assert len(got) == 3
        # Only the folder's own bytes: the parked decoder was right before it.
        assert reader.decoded - before == sum(m.size for m in wanted)
    reader.close()


def test_files_sorted_by_type_keep_a_decoder_per_kind(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    # 7-Zip can sort a solid block by file type: all photos first, then all
    # text files. Browsing folder after folder then needs a decoder in each.
    from archy.core.backends import sevenzip_solid

    monkeypatch.setattr(sevenzip_solid, "_FAR", 2 * MB)  # 64 MB in real archives
    photos = [(f"f{k}/photo.jpg", _text(3 * MB, k)) for k in range(4)]
    notes = [(f"f{k}/notes.txt", _text(64 * 1024, 50 + k)) for k in range(4)]
    path, block, members = _block_file(tmp_path, photos + notes, 128 * MB)
    by_name = {m.name: m for m in members}
    reader = SolidReader(path, SolidIndex({}))
    costs = []
    for k in range(4):
        before = reader.decoded
        wanted = [by_name[f"f{k}/photo.jpg"], by_name[f"f{k}/notes.txt"]]
        got: dict[str, bytes] = {}
        reader.read_group([(block, m) for m in wanted], None, _collect(got))
        assert len(got) == 2
        costs.append(reader.decoded - before)
    own = 3 * MB + 64 * 1024
    assert costs[0] > 10 * MB  # the first time it decodes up to the text files
    assert all(cost == own for cost in costs[1:]), costs  # then only each folder's own
    reader.close()


def test_partial_read_and_stream(tmp_path) -> None:  # noqa: ANN001
    files = _folders(2, 2, 512 * 1024)
    path, block, members = _block_file(tmp_path, files, 1 * MB)
    reader = SolidReader(path, SolidIndex({}))
    last = members[-1]
    assert reader.read(block, last, None, max_bytes=100) == files[-1][1][:100]
    pieces: list[bytes] = []
    reader.stream(block, members[1], None, pieces.append)
    assert b"".join(pieces) == files[1][1]
    reader.close()


def test_damage_and_a_wrong_password_are_reported(tmp_path) -> None:  # noqa: ANN001
    files = _folders(2, 2, 256 * 1024)
    path, block, members = _block_file(tmp_path, files, 1 * MB, aes="geheim")
    reader = SolidReader(path, SolidIndex({}))
    with pytest.raises(EncryptedArchiveError):
        reader.read(block, members[0], "wrong")
    with pytest.raises(EncryptedArchiveError):
        reader.read(block, members[0], None)
    assert reader.read(block, members[0], "geheim") == files[0][1]
    reader.close()

    path, block, members = _block_file(tmp_path, files, 1 * MB)
    wrong = [Member(m.name, m.offset, m.size, (m.crc or 0) ^ 1) for m in members]
    reader = SolidReader(path, SolidIndex({}))
    with pytest.raises(ArchiveError):
        reader.read(block, wrong[0], None)
    reader.close()


# ------------------------------------------------------- through the backend
def _archive(path, files: dict[str, bytes], filters=None, password=None) -> None:  # noqa: ANN001
    with py7zr.SevenZipFile(path, "w", filters=filters, password=password) as archive:
        for name, data in files.items():
            archive.writestr(data, name)


FILES = {f"photos/p{i}.jpg": os.urandom(200_000) for i in range(3)} | {
    f"docs/d{i}.txt": _text(300_000, i) for i in range(3)
}


@pytest.mark.parametrize(
    ("filters", "password", "own"),
    [
        ([{"id": py7zr.FILTER_LZMA2}], None, True),  # what 7-Zip writes
        ([{"id": py7zr.FILTER_LZMA}], None, True),
        (
            [{"id": py7zr.FILTER_LZMA2}, {"id": py7zr.FILTER_CRYPTO_AES256_SHA256}],
            "geheim",
            True,
        ),
        (None, None, False),  # py7zr's default: LZMA2 behind a BCJ filter (py7zr reads)
        ([{"id": py7zr.FILTER_BZIP2}], None, False),  # py7zr
    ],
)
def test_backend_reads_the_same_bytes(tmp_path, filters, password, own) -> None:  # noqa: ANN001
    path = tmp_path / "a.7z"
    _archive(path, FILES, filters, password)
    backend = backend_for(path)
    if password:
        backend.set_password(password)
    entries = {e.path: e for e in backend.list_entries() if not e.is_dir}
    assert (backend._solid is not None) == own
    for name, data in FILES.items():
        assert backend.read_bytes(entries[name], cache=False) == data
    assert (
        backend.read_bytes(entries["docs/d2.txt"], max_bytes=50, cache=False)
        == FILES["docs/d2.txt"][:50]
    )
    streamed: list[bytes] = []
    assert backend.stream_entry(entries["photos/p1.jpg"], streamed.append)
    assert b"".join(streamed) == FILES["photos/p1.jpg"]
    generation = backend.begin_folder()
    docs = [e for name, e in entries.items() if name.startswith("docs/")]
    backend.read_group(docs, generation)
    for entry in docs:
        assert backend.has_buffered(entry.path)
        assert backend.read_bytes(entry) == FILES[entry.path]
    backend.close()


def test_backend_wrong_password(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "a.7z"
    filters = [{"id": py7zr.FILTER_LZMA2}, {"id": py7zr.FILTER_CRYPTO_AES256_SHA256}]
    _archive(path, FILES, filters, "geheim")
    backend = backend_for(path)
    backend.set_password("wrong")
    entry = next(e for e in backend.list_entries() if e.path == "docs/d0.txt")
    with pytest.raises(EncryptedArchiveError):
        backend.read_bytes(entry, cache=False)
    backend.close()


def test_index_skips_single_file_blocks(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "one.7z"
    _archive(path, {"only.txt": _text(10_000, 1)}, [{"id": py7zr.FILTER_LZMA2}])
    with py7zr.SevenZipFile(path) as archive:
        assert len(SolidIndex.build(archive)) == 0
    assert _AES  # the 7zAES method id is shared with sevenzip_direct
