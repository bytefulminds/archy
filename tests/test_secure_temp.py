from __future__ import annotations

import os

import pytest

from archy.core.secure_temp import EncryptedTempStore

pytest.importorskip("Cryptodome")


def test_round_trip_and_random_access(tmp_path):
    store = EncryptedTempStore(tmp_path)
    data = os.urandom(3 * 1024 * 1024 + 123)
    blob = store.put(data)
    assert blob.size == len(data)
    assert store.read(blob) == data
    # Any byte range, including ones that start and end mid-block.
    for offset, size in ((0, 1), (15, 2), (17, 33), (1_000_001, 70_000), (len(data) - 5, 99)):
        assert store.read(blob, offset, size) == data[offset : offset + size]
    with store.open(blob) as reader:
        reader.seek(500)
        first = reader.read(100)
        second = reader.read(100)
    assert first + second == data[500:700]
    store.clear()


def test_nothing_readable_lands_on_disk(tmp_path):
    # The point of the store: no plaintext and no original names on disk.
    store = EncryptedTempStore(tmp_path)
    secret = b"WEDDING-PHOTO-" * 5000
    writer = store.writer()
    for i in range(0, len(secret), 777):
        writer.write(secret[i : i + 777])  # odd chunk sizes, crossing blocks
    blob = writer.close()
    raw = blob.path.read_bytes()
    assert len(raw) == len(secret)
    assert b"WEDDING" not in raw
    assert blob.path.parent.parent == tmp_path
    assert "." not in blob.path.name  # random name, no extension
    assert store.read(blob) == secret
    store.clear()
    assert not blob.path.exists()


def test_each_session_has_its_own_key(tmp_path):
    # Leftovers from a previous (crashed) session cannot be read back.
    first = EncryptedTempStore(tmp_path)
    blob = first.put(b"private" * 100)
    second = EncryptedTempStore(tmp_path)
    assert second.read(blob) != b"private" * 100
    first.clear()


def test_same_data_twice_gives_different_ciphertext(tmp_path):
    store = EncryptedTempStore(tmp_path)
    a = store.put(b"x" * 4096)
    b = store.put(b"x" * 4096)
    assert a.path.read_bytes() != b.path.read_bytes()  # fresh nonce per blob
    store.clear()
