from __future__ import annotations

from archy.i18n import tr
from archy.services.preview import PreviewKind

# Tunables and fixed data for the main window, kept out of the window class so
# they are easy to find and adjust.

_OPEN_PATTERNS = (
    "*.zip *.7z *.tar *.tar.gz *.tgz *.tar.bz2 *.tbz2 "
    "*.tar.xz *.txz *.tar.zst *.rar *.cab *.iso *.gz *.bz2 *.xz *.zst"
)


def open_filter() -> str:
    """File-open dialog filter, in the UI language."""
    return tr("open.filter", patterns=_OPEN_PATTERNS)


# The three bars along the bottom of the window (sidebar summary, file-panel
# status strip, preview footer) share one height, so their top edges form one
# straight line across the window.
FOOTER_HEIGHT = 56

# Do not auto-read non-text entries larger than this into memory for preview.
_PREVIEW_SIZE_LIMIT = 64 * 1024 * 1024
# For a read-only text preview, only the first slice is read (files can be huge).
_TEXT_PREVIEW_BYTES = 1024 * 1024
# A video preview is loaded into an in-memory buffer. How many copies of its
# bytes exist at once while it loads: read from the archive (the archive
# library's buffer plus the result) plus the player's own copy; or just the
# player's copy when the folder pass already holds it. The limit is spare RAM
# (see archy.core.memory) divided by this.
_VIDEO_COPIES = 3
_VIDEO_COPIES_BUFFERED = 1
# Text at or below this size can be opened for editing (loaded in full so a save
# never truncates it); larger text stays a read-only, partial preview.
_EDITOR_MAX = 8 * 1024 * 1024

# The sidebar category filters, in order: (filter key, icon). Their labels are
# the translation keys "category.<key>".
_CATEGORIES = (
    ("all", "grid"),
    ("image", "image"),
    ("video", "video"),
    ("document", "doc"),
    ("other", "dots"),
)


def category_label(category: str) -> str:
    """Label for the preview footer ("Image file"), keyed by category."""
    if category not in ("dir", "image", "video", "document", "other"):
        category = "other"
    return tr(f"kind.{category}")


# Kinds shown fullscreen (image/pdf/video).
_MEDIA_KINDS = (PreviewKind.IMAGE, PreviewKind.PDF, PreviewKind.VIDEO)
# Kinds the prev/next arrows step between. Fullscreen stays limited to
# _MEDIA_KINDS, but the arrows above the preview also page through text
# documents (they have no on-image side chevrons of their own).
_NAV_KINDS = (
    PreviewKind.TEXT,
    PreviewKind.IMAGE,
    PreviewKind.PDF,
    PreviewKind.VIDEO,
    PreviewKind.AUDIO,
    PreviewKind.OFFICE,
)
