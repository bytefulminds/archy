from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from archy.core.backend import ArchiveEntry, EntryType

# Turn the flat entry list a backend yields into a folder tree, in memory and
# with no Qt dependency. The UI wraps this with a Qt model; keeping the tree
# here means it can be built and tested headless.


@dataclass(slots=True)
class TreeNode:
    """One node in the archive tree.

    ``entry`` is the backing ArchiveEntry when the archive contained an explicit
    record for this path. Directories implied only by a child's path (common in
    zip and tar) get a synthesized node with ``entry`` None.
    """

    name: str
    path: str
    is_dir: bool
    entry: ArchiveEntry | None = None
    children: dict[str, TreeNode] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return self.entry.size if self.entry is not None else 0

    @property
    def modified(self) -> datetime | None:
        return self.entry.modified if self.entry is not None else None

    def sorted_children(self) -> list[TreeNode]:
        """Directories first, then files, each alphabetical (case-insensitive)."""
        return sorted(
            self.children.values(),
            key=lambda n: (not n.is_dir, n.name.casefold()),
        )


def _split(path: str) -> list[str]:
    return [part for part in path.replace("\\", "/").split("/") if part]


def build_tree(entries: Iterable[ArchiveEntry]) -> TreeNode:
    """Build a root TreeNode from a flat list of entries.

    Missing intermediate directories are synthesized. If both an implicit and an
    explicit record exist for a directory, the explicit entry is attached.
    """
    root = TreeNode(name="", path="", is_dir=True)

    for entry in entries:
        parts = _split(entry.path)
        if not parts:
            continue

        node = root
        for depth, part in enumerate(parts):
            is_last = depth == len(parts) - 1
            child = node.children.get(part)
            if child is None:
                child_path = "/".join(parts[: depth + 1])
                # Only the last part can be a file; everything above is a dir.
                child_is_dir = (not is_last) or entry.type is EntryType.DIRECTORY
                child = TreeNode(name=part, path=child_path, is_dir=child_is_dir)
                node.children[part] = child

            if is_last:
                # Attach the real entry, and let an explicit dir record win over
                # an earlier implicit guess.
                child.entry = entry
                if entry.type is EntryType.DIRECTORY:
                    child.is_dir = True
            node = child

    return root


def iter_file_entries(node: TreeNode) -> list[ArchiveEntry]:
    """All file entries at or below ``node`` (folders expanded), for extraction."""
    out: list[ArchiveEntry] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.is_dir:
            stack.extend(current.children.values())
        elif current.entry is not None:
            out.append(current.entry)
    return out
