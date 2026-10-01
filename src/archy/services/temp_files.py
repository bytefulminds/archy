from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from archy.core.secure_temp import Blob, EncryptedTempStore, disk_reserve

if TYPE_CHECKING:
    from collections.abc import Callable

    from archy.core.backend import ArchiveBackend, ArchiveEntry
    from archy.ui.settings import Settings

# Archy's use of the disk, which is always ENCRYPTED (see core/secure_temp.py):
# nothing a user browses is ever written to disk as the raw file.
#
# Work modes (Settings > Temporary files):
# - "memory"   Everything in memory: nothing is written. Too-large previews are
#              refused.
# - "overflow" To disk when memory is tight: RAM first; what does not fit (a folder larger
#              than the budget, a video too big to play from RAM) goes to the
#              encrypted temp store.
# - "disk"     Always use temporary files: the folder pass keeps its files in
#              the encrypted store instead of RAM, and videos/PDFs always play
#              from it. RAM then only holds thumbnails and what is on screen.
#
# One store per session, created on first use under the chosen location, with
# a key that exists only in memory. Changing the location therefore applies
# after a restart (the running session keeps its store).

MODES = ("memory", "overflow", "disk")


class TempManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._store: EncryptedTempStore | None = None
        self._retired: list[EncryptedTempStore] = []  # stores of an earlier location
        self._preview_blobs: list[Blob] = []  # spilled for previews; oldest first

    # --------------------------------------------------------------- mode
    def mode(self) -> str:
        mode = str(self._settings.temp_mode)
        return mode if mode in MODES else "memory"

    def enabled(self) -> bool:
        """True when Archy may write (encrypted) temp files at all."""
        return self.mode() != "memory"

    def prune_on_exit(self) -> bool:
        return bool(self._settings.prune_on_exit)

    # ----------------------------------------------------------- location
    def base_dir(self) -> Path:
        """The directory the store is created in, honouring the location
        setting. Falls back to the system temp dir when a custom directory is
        unset or not usable, so a preview never fails over a bad setting."""
        if self._settings.temp_location == "custom":
            custom = self._settings.temp_dir.strip()
            if custom:
                candidate = Path(custom).expanduser()
                if candidate.is_dir():
                    return candidate
        return Path(tempfile.gettempdir())

    @property
    def store(self) -> EncryptedTempStore:
        if self._store is None:
            self._store = EncryptedTempStore(self.base_dir())
        return self._store

    def configure_backend(self, backend: ArchiveBackend | None) -> None:
        """Tell the backend where its folder pass may keep files. Called when
        an archive opens and when settings change; takes effect from the next
        folder pass. After a location change, new files go to the new location;
        files already written stay readable where they are (the old store is
        kept for them) and are removed with the usual cleanup."""
        if self._store is not None and self._store.base_dir() != self.base_dir():
            self._retired.append(self._store)
            self._store = None
        if backend is not None:
            store = self.store if self.enabled() else None
            backend.configure_spill(store, self.mode())

    # ------------------------------------------------------------ previews
    def spill_entry(
        self,
        backend: ArchiveBackend,
        entry: ArchiveEntry,
        *,
        on_progress: Callable[[int, int, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> Blob | None:
        """Put one entry in the encrypted store for a preview that reads it
        from disk (video/PDF), streaming it so it is never held in memory.

        Reuses the folder pass's blob when it already holds the entry. Returns
        None when cancelled (stale selection) or when the disk is too full.
        """
        existing = backend.spilled_blob(entry.path)
        if existing is not None:
            return existing
        store = self.store
        size = max(int(entry.size or 0), 0)
        if store.free_disk() - size < disk_reserve():
            return None
        writer = store.writer()

        def progress(done: int, total: int) -> None:
            if on_progress is not None:
                on_progress(done, total, entry.path)

        try:
            ok = backend.stream_entry(
                entry, writer.write, on_progress=progress, is_cancelled=is_cancelled
            )
        except BaseException:
            writer.abort()
            raise
        if not ok:
            writer.abort()
            return None
        blob = writer.close()
        self._preview_blobs.append(blob)
        return blob

    def clear_preview(self) -> None:
        """Remove temp files of previews that are no longer shown. The newest
        one may still be open by the player that is being torn down; on
        Windows its delete fails and is retried next time (or at exit)."""
        store = self._store
        if store is None:
            return
        remaining: list[Blob] = []
        for blob in self._preview_blobs:
            store.delete(blob)
            if blob.path.exists():
                remaining.append(blob)
        self._preview_blobs = remaining

    # ------------------------------------------------------------- cleanup
    def prune(self) -> None:
        """Remove everything this session wrote, plus leftovers of earlier
        sessions (unreadable anyway: their key died with them)."""
        self._preview_blobs = []
        for store in [*self._retired, self._store]:
            if store is not None:
                store.clear()
        self._retired = []
        base = self.base_dir()
        for stale in base.glob("archy-*"):
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)
