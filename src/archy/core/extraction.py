from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from archy.core.backend import ArchiveEntry
from archy.core.errors import ArchiveError

# Extraction is the second (and only other) allowed disk write besides saving an
# edited archive. It streams each entry's blocks straight to the destination
# file, so a 300 GB entry is never held in memory. Paths are sanitised so a
# malicious archive can never write outside the chosen folder ("zip slip").

BLOCK_SIZE = 1024 * 1024

# A per-entry block source: yields the entry's bytes in chunks.
BlockIter = Callable[[ArchiveEntry], Iterator[bytes]]
ProgressCb = Callable[[int, int, str], None]
CancelCb = Callable[[], bool]


@dataclass(slots=True)
class ExtractResult:
    extracted: int = 0
    bytes_written: int = 0
    cancelled: bool = False
    failed: list[tuple[str, str]] = field(default_factory=list)


def safe_join(dest_dir: Path, arcname: str) -> Path | None:
    """Resolve an in-archive path under ``dest_dir``, or None if it escapes it.

    Drops empty/"." segments, rejects "..", drive letters and absolute paths,
    and verifies the final path stays inside ``dest_dir`` (zip-slip guard).
    """
    parts: list[str] = []
    for part in PurePosixPath(arcname.replace("\\", "/")).parts:
        if part in ("", "/", "."):
            continue
        if part == ".." or ":" in part:
            return None
        parts.append(part)
    if not parts:
        return None
    dest = dest_dir.joinpath(*parts)
    try:
        dest.resolve().relative_to(dest_dir.resolve())
    except ValueError:
        return None
    return dest


def run_extract(
    dest_dir: Path,
    entries: Iterable[ArchiveEntry],
    block_iter: BlockIter,
    *,
    on_progress: ProgressCb | None = None,
    is_cancelled: CancelCb | None = None,
) -> ExtractResult:
    """Stream each file entry to ``dest_dir`` using ``block_iter`` for its bytes.

    Directories are created as needed; folder entries are skipped (their files
    carry the structure). Cancellation removes the partially written file.
    """
    files = [e for e in entries if not e.is_dir]
    total = sum(max(e.size, 0) for e in files)
    result = ExtractResult()
    done = 0

    for entry in files:
        if is_cancelled is not None and is_cancelled():
            result.cancelled = True
            break

        dest = safe_join(dest_dir, entry.path)
        if dest is None:
            result.failed.append((entry.path, "onveilig pad overgeslagen"))
            continue

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as out:
                for chunk in block_iter(entry):
                    if is_cancelled is not None and is_cancelled():
                        result.cancelled = True
                        break
                    out.write(chunk)
                    done += len(chunk)
                    result.bytes_written += len(chunk)
                    if on_progress is not None:
                        on_progress(done, total, entry.path)
            if result.cancelled:
                dest.unlink(missing_ok=True)
                break
            result.extracted += 1
        except (OSError, ArchiveError) as exc:
            result.failed.append((entry.path, str(exc)))
        except Exception as exc:  # noqa: BLE001 - record and continue
            result.failed.append((entry.path, str(exc)))

    return result
