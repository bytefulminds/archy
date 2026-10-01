from __future__ import annotations

from archy.core.backend import ArchiveEntry, EntryType
from archy.core.tree import build_tree


def _f(path: str, size: int = 0) -> ArchiveEntry:
    return ArchiveEntry(path=path, type=EntryType.FILE, size=size)


def _d(path: str) -> ArchiveEntry:
    return ArchiveEntry(path=path, type=EntryType.DIRECTORY)


def test_synthesizes_missing_directories() -> None:
    root = build_tree([_f("a/b/c.txt", 10)])
    a = root.children["a"]
    assert a.is_dir and a.entry is None
    b = a.children["b"]
    assert b.is_dir and b.entry is None
    c = b.children["c.txt"]
    assert not c.is_dir
    assert c.size == 10
    assert c.path == "a/b/c.txt"


def test_explicit_directory_entry_is_attached() -> None:
    root = build_tree([_d("a/"), _f("a/x.txt", 3)])
    a = root.children["a"]
    assert a.is_dir
    assert a.entry is not None
    assert set(a.children) == {"x.txt"}


def test_sorted_children_dirs_first_then_alpha() -> None:
    root = build_tree(
        [
            _f("Zebra.txt"),
            _f("apple.txt"),
            _f("dir/inner.txt"),
            _f("Beta/inner.txt"),
        ]
    )
    names = [n.name for n in root.sorted_children()]
    # Directories first (case-insensitive alpha), then files (case-insensitive).
    assert names == ["Beta", "dir", "apple.txt", "Zebra.txt"]


def test_root_is_directory_with_no_entry() -> None:
    root = build_tree([])
    assert root.is_dir
    assert root.entry is None
    assert root.children == {}
