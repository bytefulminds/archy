from __future__ import annotations

from enum import Enum, auto

# Preview always works from bytes held in memory. For PDF and video the bytes
# are wrapped in a QBuffer (an in-memory QIODevice) and handed to the Qt
# viewers - no temp file is ever written.


class PreviewKind(Enum):
    TEXT = auto()
    IMAGE = auto()
    PDF = auto()
    VIDEO = auto()
    UNSUPPORTED = auto()


# Extension groups used to pick a preview provider. Content sniffing can be
# added later; extension is enough to start.
_TEXT_EXT = {
    ".txt",
    ".md",
    ".rst",
    ".log",
    ".ini",
    ".cfg",
    ".conf",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".xml",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".py",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".sh",
    ".bat",
    ".ps1",
    ".sql",
    ".csv",
}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".ico", ".svg"}
_PDF_EXT = {".pdf"}
_VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv"}


def kind_for_name(name: str) -> PreviewKind:
    dot = name.rfind(".")
    ext = name[dot:].lower() if dot != -1 else ""
    if ext in _TEXT_EXT:
        return PreviewKind.TEXT
    if ext in _IMAGE_EXT:
        return PreviewKind.IMAGE
    if ext in _PDF_EXT:
        return PreviewKind.PDF
    if ext in _VIDEO_EXT:
        return PreviewKind.VIDEO
    return PreviewKind.UNSUPPORTED
