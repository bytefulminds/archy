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
    AUDIO = auto()
    OFFICE = auto()  # Word, Excel, PowerPoint (services/office.py)
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
    ".tsv",
    ".htm",
    ".xhtml",
    ".markdown",
    ".jsonl",
    ".geojson",
}
_IMAGE_EXT = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
    ".ico",
    ".svg",
    ".heic",  # iPhone photos (HEIF); decoded by Qt on macOS, pillow-heif elsewhere
    ".heif",
    ".hif",
    ".avif",
    # Camera RAW (the embedded preview) and Photoshop (the composite image);
    # see services/raw_decode.py.
    ".cr2",
    ".cr3",
    ".crw",
    ".nef",
    ".nrw",
    ".arw",
    ".srf",
    ".sr2",
    ".dng",
    ".raf",
    ".orf",
    ".rw2",
    ".rwl",
    ".pef",
    ".srw",
    ".3fr",
    ".iiq",
    ".erf",
    ".kdc",
    ".mos",
    ".mrw",
    ".psd",
    ".psb",
}
_PDF_EXT = {".pdf"}
_VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv"}
_AUDIO_EXT = {
    ".mp3",
    ".flac",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".oga",
    ".opus",
    ".wma",
    ".aif",
    ".aiff",
}


_OFFICE_EXT = {".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm"}  # see services/office.py


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
    if ext in _AUDIO_EXT:
        return PreviewKind.AUDIO
    if ext in _OFFICE_EXT:
        return PreviewKind.OFFICE
    return PreviewKind.UNSUPPORTED
