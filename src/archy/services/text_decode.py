from __future__ import annotations

import codecs
from dataclasses import dataclass

from archy.i18n import tr

# Pure text decoding for the preview panel - no Qt. Bytes come straight from the
# archive (already in memory); we decode them for display and cap the amount we
# render so a huge log file cannot lock up the UI.
#
# Order of decisions (most certain first):
# 1. A byte order mark (UTF-8, UTF-16, UTF-32) says exactly what the file is.
# 2. Valid UTF-8 is UTF-8. Random legacy text is practically never valid UTF-8,
#    so this is safe, and it is what almost every modern file is. Detection
#    libraries guess poorly on short texts: "Café ☕" came out as cp1125
#    ("Caf├й тШХ"), which is why emoji and quotes looked garbled.
# 3. UTF-16 without a BOM, recognised by its pattern of zero bytes.
# 4. Otherwise a legacy 8-bit encoding. charset-normalizer's guess is used when
#    it is a plausible one; old DOS / Mac code pages it tends to pick for
#    Western text (cp437, cp1125, mac_roman, ...) give way to Windows-1252,
#    the encoding of nearly every older Western text file.

# Render at most this many bytes; the rest is elided with a marker.
MAX_PREVIEW_BYTES = 2_000_000

_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),  # before UTF-16 LE: it starts the same way
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

# Code pages that are almost never right for a text file met today, but that
# detection likes to pick for short Western text.
_UNLIKELY_PREFIXES = ("mac_", "hp_", "ptcp", "kz1048", "cp037", "cp1006", "cp1125", "cp273")
_UNLIKELY_DOS = {f"cp{n}" for n in (437, 720, 737, 775, 850, 852, 855, 856, 857, 858, 860)} | {
    f"cp{n}" for n in (861, 862, 863, 864, 865, 866, 869, 874, 875, 424, 500, 1026, 1140)
}


@dataclass(frozen=True, slots=True)
class DecodedText:
    text: str
    encoding: str
    truncated: bool


def _bom_encoding(data: bytes) -> str | None:
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return encoding
    return None


def _utf16_without_bom(data: bytes) -> str | None:
    """'utf-16-le' / 'utf-16-be' when ``data`` looks like mostly-Latin UTF-16."""
    sample = data[:4096]
    if len(sample) < 8:
        return None
    sample = sample[: len(sample) // 2 * 2]
    pairs = len(sample) // 2
    even_zero = sum(1 for i in range(0, len(sample), 2) if sample[i] == 0)
    odd_zero = sum(1 for i in range(1, len(sample), 2) if sample[i] == 0)
    if odd_zero > pairs * 0.4 and even_zero < pairs * 0.05:
        candidate = "utf-16-le"
    elif even_zero > pairs * 0.4 and odd_zero < pairs * 0.05:
        candidate = "utf-16-be"
    else:
        return None
    try:
        sample.decode(candidate)
    except UnicodeDecodeError:
        return None
    return candidate


def looks_binary(data: bytes) -> bool:
    """Heuristic: a NUL byte in the first chunk means "not text" - except in
    UTF-16/32 text, where zero bytes are normal."""
    head = data[:4096]
    if b"\x00" not in head:
        return False
    return _bom_encoding(head) is None and _utf16_without_bom(head) is None


def _decode_utf8(chunk: bytes, *, truncated: bool) -> str | None:
    try:
        return chunk.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A preview cut can split the last character; that is still UTF-8.
        if truncated and exc.start >= len(chunk) - 3 and exc.reason == "unexpected end of data":
            return chunk[: exc.start].decode("utf-8", errors="replace")
        return None


def _plausible(encoding: str) -> bool:
    name = encoding.lower().replace("-", "_")
    # UTF-8 already failed, and real UTF-16/32 was recognised by its BOM or
    # zero bytes; a UTF guess here is the detector misreading legacy bytes
    # (charset-normalizer 3.5.2 calls 'Café “mooi” – €5' in cp1252 UTF-16-BE).
    if name.startswith("utf"):
        return False
    return not (name.startswith(_UNLIKELY_PREFIXES) or name in _UNLIKELY_DOS)


def _decode_legacy(chunk: bytes) -> tuple[str, str]:
    guess = None
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(chunk).best()
        if best is not None and best.encoding and _plausible(best.encoding):
            guess = best.encoding
    except Exception:  # noqa: BLE001 - detection is best-effort
        guess = None
    for encoding in (guess, "cp1252"):
        if encoding is None:
            continue
        try:
            return chunk.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return chunk.decode("latin-1"), "latin-1"  # never fails


def decode_text(data: bytes, *, limit: int | None = MAX_PREVIEW_BYTES) -> DecodedText:
    """Decode bytes to text, see the module comment for the order of checks.

    ``limit`` caps how many bytes are decoded (preview); pass None to decode
    everything (editing, where truncating would lose data on save). The
    returned encoding is the one to write the text back with.
    """
    truncated = limit is not None and len(data) > limit
    chunk = data[:limit] if limit is not None else data

    text: str | None = None
    encoding = _bom_encoding(chunk) or _utf16_without_bom(chunk)
    if encoding is not None:
        text = chunk.decode(encoding, errors="replace")
    else:
        text = _decode_utf8(chunk, truncated=truncated)
        if text is not None:
            encoding = "utf-8"  # also for plain ASCII: edits may add any character
        else:
            text, encoding = _decode_legacy(chunk)

    if truncated:
        text += tr("preview.truncated")

    return DecodedText(text=text, encoding=encoding, truncated=truncated)
