from __future__ import annotations

import bisect
import lzma
import threading
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from archy.core.backends.sevenzip_direct import _AES, _BLOCK, _Cipher
from archy.core.errors import ArchiveError, EncryptedArchiveError

# Reads from compressed solid blocks (LZMA2 or LZMA, encrypted or not) without
# decoding them from the start every time.
#
# In a solid 7z every block is one compressed stream: to reach a file, py7zr
# decodes everything before it in the block (and checks the CRC of all of it).
# In a block of several GB that means minutes for a folder near its end, again
# for every folder. Two things make that cheaper here, for archives as they are
# (nothing needs to be repacked):
#
# 1. Dictionary resets. 7-Zip compressing with several threads splits the
#    LZMA2 stream into pieces that each start with an empty dictionary (every
#    4x the dictionary size: 64 MB at the normal level, 256 MB at ultra).
#    Decoding can start at any of those. They are found by walking the LZMA2
#    chunk headers (a few bytes every 64 KB of the stream, nothing decoded)
#    and remembered for the session, so a folder costs at most one piece of
#    decoding before its first file instead of everything before it.
# 2. Parked decoders. After a read the decoder is kept where it stopped, so
#    the next folder further on in the same block continues from there. That
#    helps every archive, also one with LZMA or a single-threaded LZMA2
#    stream (no resets past the start): browsing forward costs only the
#    folder's own bytes.
#
# Bytes that are decoded only to get past them are not CRC-checked (py7zr
# checks them all); every file that is handed over is.
#
# Only blocks of one LZMA2 or LZMA coder, optionally behind 7zAES, qualify:
# others (BCJ filters, PPMd, BZip2, ...) keep going through py7zr. Nothing is
# written to disk; this module only reads the index py7zr already parsed.

_LZMA2 = b"!"
_LZMA = b"\x03\x01\x01"
_IN_CHUNK = 1024 * 1024  # compressed bytes read (and decrypted) at a time
_OUT_CHUNK = 4 * 1024 * 1024  # decoded bytes per step (cancelling is checked per step)
_PARKED = 3  # decoders kept for a next read
# A file this far past the decoder (and no reset point in between) is read by
# another decoder, so this one stays where it is for the next folder. 7-Zip
# sorts a solid block by file type: a folder's photos sit together and its
# text files far further on, and each kind then has a decoder that only moves
# forward while browsing folder after folder.
_FAR = 64 * 1024 * 1024
_PARK_DICT_LIMIT = 256 * 1024 * 1024  # a decoder with a larger dictionary is not kept
_PARK_MEMORY = 384 * 1024 * 1024  # dictionaries of all kept decoders together
# Without a second reset after this many decoded bytes (beyond four times the
# dictionary), the stream was made by a single thread: walking stops.
_RESET_SPACING_FLOOR = 256 * 1024 * 1024


class SolidCancelled(Exception):
    """The caller's is_cancelled() became true during a read."""


@dataclass(frozen=True, slots=True)
class Member:
    """One file in a solid block."""

    name: str
    offset: int  # within the block's decoded data
    size: int
    crc: int | None


@dataclass(frozen=True, slots=True)
class Block:
    """A solid block this module can read."""

    index: int
    start: int  # absolute offset of the block's packed data in the archive
    packed: int  # bytes of packed data (with AES: encrypted and padded)
    coded: int  # bytes of the LZMA/LZMA2 stream (with AES: after decryption)
    size: int  # decoded bytes
    method: bytes  # _LZMA2 or _LZMA
    props: bytes  # the LZMA/LZMA2 coder properties
    aes: bytes | None  # 7zAES properties, None when not encrypted

    @property
    def dict_size(self) -> int:
        if self.method == _LZMA2:
            p = self.props[0] if self.props else 0
            size = 0xFFFFFFFF if p >= 40 else (2 | (p & 1)) << (p // 2 + 11)
        else:
            size = int.from_bytes(self.props[1:5], "little")
        # A dictionary never needs to be larger than what the block holds.
        return max(4096, min(size, self.size))


class SolidIndex:
    """The readable solid blocks of an archive and where each file sits."""

    def __init__(self, members: dict[str, tuple[Block, Member]]) -> None:
        self._members = members

    def __len__(self) -> int:
        return len(self._members)

    def get(self, name: str) -> tuple[Block, Member] | None:
        return self._members.get(name)

    @classmethod
    def build(cls, archive: object) -> SolidIndex:
        """From an open py7zr.SevenZipFile (its parsed header)."""
        members: dict[str, tuple[Block, Member]] = {}
        header = archive.header  # type: ignore[attr-defined]
        streams = getattr(header, "main_streams", None)
        if streams is None:
            return cls(members)
        pack = streams.packinfo
        base = int(archive.afterheader) + int(pack.packpos)  # type: ignore[attr-defined]
        positions = list(pack.packpositions)
        sizes = [int(size) for size in pack.packsizes]
        stream_index = 0
        for number, folder in enumerate(streams.unpackinfo.folders):
            packed_streams = int(
                getattr(folder, "num_packedstreams", None) or len(folder.packed_indices)
            )
            first = stream_index
            stream_index += packed_streams
            files = [f for f in (getattr(folder, "files", None) or ()) if not f.emptystream]
            if packed_streams != 1 or len(files) < 2:
                continue  # a block of one file is cheap to read as it is
            chain = _chain(folder)
            if chain is None:
                continue
            method, props, aes, coded = chain
            packed = sizes[first]
            block = Block(
                index=number,
                start=base + int(positions[first]),
                packed=packed,
                coded=packed if aes is None else coded,
                size=int(folder.get_unpack_size()),
                method=method,
                props=props,
                aes=aes,
            )
            offset = 0
            for f in files:
                size = int(f.uncompressed or 0)
                name = str(f.filename).replace("\\", "/")
                crc = None if f.crc32 is None else int(f.crc32)
                members[name] = (block, Member(name, offset, size, crc))
                offset += size
        return cls(members)


def _chain(folder: object) -> tuple[bytes, bytes, bytes | None, int] | None:
    """(method, properties, AES properties, size of the coded stream) of a
    block made of one LZMA2/LZMA coder, optionally behind 7zAES; None for any
    other block."""
    coders = list(getattr(folder, "coders", None) or ())
    if not coders or len(coders) > 2:
        return None
    for coder in coders:
        if coder.get("numinstreams", 1) != 1 or coder.get("numoutstreams", 1) != 1:
            return None
    methods = [bytes(c.get("method") or b"") for c in coders]
    unpack = [int(s) for s in (getattr(folder, "unpacksizes", None) or ())]
    if len(coders) == 1:
        if methods[0] not in (_LZMA2, _LZMA):
            return None
        return methods[0], bytes(coders[0].get("properties") or b""), None, 0
    # Encrypted: the packed stream goes into AES, whose output feeds the coder.
    if _AES not in methods:
        return None
    a = methods.index(_AES)
    c = 1 - a
    if methods[c] not in (_LZMA2, _LZMA) or len(unpack) != 2:
        return None
    bonds = list(getattr(folder, "bindpairs", None) or ())
    if len(bonds) != 1 or (bonds[0].incoder, bonds[0].outcoder) != (c, a):
        return None
    if list(getattr(folder, "packed_indices", None) or [a]) != [a]:
        return None
    aes = bytes(coders[a].get("properties") or b"")
    return methods[c], bytes(coders[c].get("properties") or b""), aes, unpack[a]


def _filters(block: Block) -> list[dict[str, int]]:
    if block.method == _LZMA2:
        return [{"id": lzma.FILTER_LZMA2, "dict_size": block.dict_size}]
    d = block.props[0]
    lc, d = d % 9, d // 9
    lp, pb = d % 5, d // 5
    return [{"id": lzma.FILTER_LZMA1, "dict_size": block.dict_size, "lc": lc, "lp": lp, "pb": pb}]


class _Source:
    """The block's coded (LZMA/LZMA2) stream, decrypted when needed, read
    from any offset. Its own file handle: one thread at a time."""

    def __init__(self, path: str | Path, block: Block, password: str | None) -> None:
        self._block = block
        self._cipher = _Cipher(block.aes, password) if block.aes is not None else None
        self._fh = open(path, "rb", buffering=0)  # noqa: SIM115 - closed by close()

    def close(self) -> None:
        self._fh.close()

    def _read(self, position: int, size: int) -> bytes:
        self._fh.seek(self._block.start + position)
        data = self._fh.read(size)
        if len(data) != size:
            raise ArchiveError("The archive ends unexpectedly (truncated or damaged)")
        return data

    def read_at(self, position: int, size: int) -> bytes:
        """A few bytes (a chunk header); fewer at the end of the stream."""
        size = max(0, min(size, self._block.coded - position))
        if size == 0:
            return b""
        if self._cipher is None:
            return self._read(position, size)
        aligned = position - position % _BLOCK
        end = position + size
        end += -end % _BLOCK
        previous = self._read(aligned - _BLOCK, _BLOCK) if aligned else None
        plain = self._cipher.decryptor(previous).decrypt(self._read(aligned, end - aligned))
        return plain[position - aligned : position - aligned + size]

    def stream(self, position: int) -> Iterator[bytes]:
        """The stream from ``position`` to its end, in pieces."""
        end = self._block.coded
        if self._cipher is None:
            while position < end:
                piece = self._read(position, min(_IN_CHUNK, end - position))
                position += len(piece)
                yield piece
            return
        aligned = position - position % _BLOCK
        previous = self._read(aligned - _BLOCK, _BLOCK) if aligned else None
        decrypt = self._cipher.decryptor(previous).decrypt
        skip = position - aligned
        while aligned < end:
            size = min(_IN_CHUNK, self._block.packed - aligned)
            plain = decrypt(self._read(aligned, size))
            piece = plain[skip : max(skip, end - aligned)]
            skip = 0
            aligned += size
            if piece:
                yield piece


class _Cursor:
    """A decoder at some point of a block."""

    def __init__(
        self, path: str | Path, block: Block, password: str | None, at: tuple[int, int]
    ) -> None:
        self.block = block
        self.password = password
        self._source = _Source(path, block, password)
        self._input = self._source.stream(at[1])
        self._decoder = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=_filters(block))
        self.position = at[0]  # decoded bytes before the next one this cursor gives

    def close(self) -> None:
        self._source.close()

    def _step(self, limit: int) -> bytes:
        decoder = self._decoder
        idle = 0
        while True:
            if decoder.eof:
                raise ArchiveError("The block ends early (damaged archive)")
            data = b""
            if decoder.needs_input:
                data = next(self._input, b"")
                if not data:
                    raise ArchiveError("The archive ends unexpectedly (truncated or damaged)")
            out = decoder.decompress(data, max_length=limit)
            if out:
                self.position += len(out)
                return out
            idle += 1
            if idle > 64 and not decoder.needs_input:
                raise ArchiveError("The decoder makes no progress (damaged archive)")

    def skip_to(self, target: int, tick: Callable[[int], None]) -> None:
        while self.position < target:
            tick(len(self._step(min(_OUT_CHUNK, target - self.position))))

    def read(self, size: int, sink: Callable[[bytes], None], tick: Callable[[int], None]) -> None:
        end = self.position + size
        while self.position < end:
            out = self._step(min(_OUT_CHUNK, end - self.position))
            sink(out)
            tick(len(out))


class _Resets:
    """The dictionary reset points of one LZMA2 block, found lazily by
    walking its chunk headers (no decoding) and kept for the session."""

    def __init__(self, block: Block) -> None:
        self.block = block
        self.points: list[tuple[int, int]] = [(0, 0)]  # (decoded offset, stream offset)
        self.lock = threading.Lock()
        self._decoded = 0
        self._coded = 0
        self.done = False

    def _mark(self) -> None:
        point = (self._decoded, self._coded)
        if point != self.points[-1]:  # the first chunk's reset is (0, 0), already there
            self.points.append(point)

    def before(self, target: int) -> tuple[int, int]:
        """The last known reset point at or before ``target``."""
        return self.points[max(0, bisect.bisect_right(self.points, (target, 1 << 62)) - 1)]

    def walk_to(self, source: _Source, target: int, is_cancelled: Callable[[], bool]) -> None:
        """Walk the chunk chain until it passes ``target`` (decoded offset)."""
        give_up = max(4 * self.block.dict_size, _RESET_SPACING_FLOOR) + _IN_CHUNK
        steps = 0
        while not self.done and self._decoded <= target:
            head = source.read_at(self._coded, 6)
            if not head or head[0] == 0:
                self.done = True
                break
            control = head[0]
            if control in (1, 2):
                if len(head) < 3:
                    self.done = True
                    break
                size = int.from_bytes(head[1:3], "big") + 1
                if control == 1:
                    self._mark()
                step = 3 + size
            elif control >= 0x80:
                if len(head) < 5:
                    self.done = True
                    break
                size = ((control & 0x1F) << 16) + int.from_bytes(head[1:3], "big") + 1
                mode = (control >> 5) & 3
                if mode == 3:
                    self._mark()
                step = 5 + (1 if mode >= 2 else 0) + int.from_bytes(head[3:5], "big") + 1
            else:
                self.done = True  # not an LZMA2 chunk: stop trusting the chain
                break
            self._decoded += size
            self._coded += step
            if len(self.points) == 1 and self._decoded > give_up:
                self.done = True  # one thread made this: no resets to find
            steps += 1
            if steps % 4096 == 0 and is_cancelled():
                raise SolidCancelled


def _parkable(cursor: _Cursor) -> bool:
    return cursor.position < cursor.block.size and cursor.block.dict_size <= _PARK_DICT_LIMIT


def _never() -> bool:
    return False


class SolidReader:
    """Reads files from the blocks of a SolidIndex, reusing decoders and
    reset points across reads. Thread-safe."""

    def __init__(self, path: str | Path, index: SolidIndex) -> None:
        self._path = path
        self.index = index
        self._lock = threading.Lock()
        self._parked: list[_Cursor] = []  # most recently used last
        self._resets: dict[int, _Resets] = {}
        self._progress: list[int] | None = None  # [done, total] of a running group read
        self._closed = False
        self.decoded = 0  # bytes decoded over the reader's life (to skip or to hand over)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            parked, self._parked = self._parked, []
        for cursor in parked:
            cursor.close()

    def progress(self) -> tuple[int, int] | None:
        """(decoded bytes, bytes to decode) of a running group read."""
        current = self._progress
        if current is None:
            return None
        return min(current[0], current[1]), current[1]

    def reset_points(self, block: Block) -> int:
        """How many reset points are known for a block (for tests)."""
        resets = self._resets.get(block.index)
        return len(resets.points) if resets is not None else 0

    # ------------------------------------------------------------ cursors
    def _resets_for(self, block: Block) -> _Resets | None:
        if block.method != _LZMA2:
            return None
        with self._lock:
            resets = self._resets.get(block.index)
            if resets is None:
                resets = self._resets[block.index] = _Resets(block)
            return resets

    def _start_for(
        self, block: Block, target: int, password: str | None, is_cancelled: Callable[[], bool]
    ) -> tuple[int, int]:
        """The latest reset point at or before ``target``."""
        resets = self._resets_for(block)
        if resets is None:
            return 0, 0
        with resets.lock:
            if not resets.done:
                source = _Source(self._path, block, password)
                try:
                    resets.walk_to(source, target, is_cancelled)
                finally:
                    source.close()
            return resets.before(target)

    def _parked_for(self, block: Block, target: int, password: str | None) -> int:
        with self._lock:
            positions = [
                c.position
                for c in self._parked
                if c.block.index == block.index and c.password == password and c.position <= target
            ]
        return max(positions, default=-1)

    def _take(
        self,
        block: Block,
        target: int,
        password: str | None,
        is_cancelled: Callable[[], bool],
        exclude: _Cursor | None = None,
    ) -> _Cursor:
        """A decoder positioned at or before ``target``: a parked one or a
        new one at a reset point, whichever leaves less to decode."""
        start = self._start_for(block, target, password, is_cancelled)
        with self._lock:
            best = None
            for cursor in self._parked:
                if (
                    cursor is not exclude
                    and cursor.block.index == block.index
                    and cursor.password == password
                    and start[0] <= cursor.position <= target
                    and (best is None or cursor.position > best.position)
                ):
                    best = cursor
            if best is not None:
                self._parked.remove(best)
                return best
        return _Cursor(self._path, block, password, start)

    def _park(self, cursor: _Cursor) -> None:
        keep = cursor.position < cursor.block.size and cursor.block.dict_size <= _PARK_DICT_LIMIT
        evicted: list[_Cursor] = []
        with self._lock:
            if keep and not self._closed:
                self._parked.append(cursor)
                while len(self._parked) > _PARKED or (
                    sum(c.block.dict_size for c in self._parked) > _PARK_MEMORY
                ):
                    evicted.append(self._parked.pop(0))
            else:
                evicted.append(cursor)
        for old in evicted:
            old.close()

    # -------------------------------------------------------------- reads
    def _check(self, block: Block, member: Member, crc: int) -> None:
        if member.crc is not None and crc != member.crc:
            if block.aes is not None:
                raise EncryptedArchiveError("The password is wrong (checksum mismatch)")
            raise ArchiveError(f"Checksum mismatch: the archive is damaged ({member.name})")

    def _run(
        self,
        block: Block,
        members: list[Member],
        password: str | None,
        on_member: Callable[[Member, Callable[[bytes], None]], Callable[[], None]],
        is_cancelled: Callable[[], bool],
        tick: Callable[[int], None],
        limit: int | None = None,
    ) -> None:
        """Decode ``members`` (sorted by offset) of one block, the first
        ``limit`` bytes of each when given. ``on_member(member)`` returns
        (sink, finish): every decoded piece of that member goes to sink, then
        finish is called (after its CRC check, for a whole member)."""

        def guarded(count: int) -> None:
            if is_cancelled():
                raise SolidCancelled
            self.decoded += count
            tick(count)

        cursor: _Cursor | None = None
        try:
            for member in members:
                if cursor is None or cursor.position != member.offset:
                    # Continue, or switch to a reset point or parked decoder that is closer.
                    keep: _Cursor | None = None
                    if cursor is not None:
                        start = self._start_for(block, member.offset, password, is_cancelled)
                        closer = max(start[0], self._parked_for(block, member.offset, password))
                        if cursor.position > member.offset or closer > cursor.position:
                            self._park(cursor)
                            cursor = None
                        elif member.offset - cursor.position > _FAR and _parkable(cursor):
                            keep = cursor  # stays here; another one reads on
                            self._park(cursor)
                            cursor = None
                    if cursor is None:
                        cursor = self._take(block, member.offset, password, is_cancelled, keep)
                    cursor.skip_to(member.offset, guarded)
                size = member.size if limit is None else min(limit, member.size)
                deliver, finish = on_member(member)
                crc = [0]

                def take(
                    piece: bytes, _deliver: Callable[[bytes], None] = deliver, _crc: list[int] = crc
                ) -> None:
                    _crc[0] = zlib.crc32(piece, _crc[0])
                    _deliver(piece)

                cursor.read(size, take, guarded)
                if size == member.size:
                    self._check(block, member, crc[0])
                finish()
        except lzma.LZMAError as exc:
            if cursor is not None:
                cursor.close()
                cursor = None
            if block.aes is not None:
                raise EncryptedArchiveError(
                    "The password is wrong (the data does not decode)"
                ) from exc
            raise ArchiveError(f"Could not decode block {block.index}: {exc}") from exc
        except BaseException:
            if cursor is not None:
                cursor.close()
                cursor = None
            raise
        finally:
            if cursor is not None:
                self._park(cursor)

    def read(
        self,
        block: Block,
        member: Member,
        password: str | None,
        max_bytes: int | None = None,
        is_cancelled: Callable[[], bool] = _never,
    ) -> bytes:
        """One file (its first ``max_bytes``), CRC-checked when read whole."""
        parts: list[bytes] = []

        def on_member(_m: Member) -> tuple[Callable[[bytes], None], Callable[[], None]]:
            return parts.append, lambda: None

        self._run(block, [member], password, on_member, is_cancelled, lambda _n: None, max_bytes)
        return b"".join(parts)

    def stream(
        self,
        block: Block,
        member: Member,
        password: str | None,
        sink: Callable[[bytes], None],
        is_cancelled: Callable[[], bool] = _never,
    ) -> None:
        """One file, in pieces, CRC-checked at the end."""

        def on_member(_m: Member) -> tuple[Callable[[bytes], None], Callable[[], None]]:
            return sink, lambda: None

        self._run(block, [member], password, on_member, is_cancelled, lambda _n: None)

    def read_group(
        self,
        items: list[tuple[Block, Member]],
        password: str | None,
        deliver: Callable[[Member, bytes], None],
        is_cancelled: Callable[[], bool] = _never,
        on_failed: Callable[[Block, list[Member], Exception], None] | None = None,
    ) -> None:
        """Several files, block by block in archive order, each handed to
        ``deliver`` as soon as it is decoded (and CRC-checked). A block that
        fails goes to ``on_failed`` with the files it did not deliver (or
        raises when there is none); cancelling raises SolidCancelled."""
        blocks: dict[int, tuple[Block, list[Member]]] = {}
        for block, member in items:
            blocks.setdefault(block.index, (block, []))[1].append(member)
        plan = []
        total = 0
        for number in sorted(blocks):
            block, members = blocks[number]
            members.sort(key=lambda m: m.offset)
            first = members[0].offset
            start = max(
                self._start_for(block, first, password, is_cancelled)[0],
                self._parked_for(block, first, password),
            )
            last = members[-1].offset + members[-1].size
            total += last - start
            plan.append((block, members))
        progress = [0, max(total, 1)]
        self._progress = progress

        def tick(count: int) -> None:
            progress[0] += count

        try:
            for block, members in plan:
                delivered: set[str] = set()

                def on_member(
                    member: Member, _done: set[str] = delivered
                ) -> tuple[Callable[[bytes], None], Callable[[], None]]:
                    parts: list[bytes] = []

                    def finish() -> None:
                        _done.add(member.name)
                        deliver(member, b"".join(parts))

                    return parts.append, finish

                try:
                    self._run(block, members, password, on_member, is_cancelled, tick)
                except SolidCancelled:
                    raise
                except Exception as exc:
                    if on_failed is None:
                        raise
                    on_failed(block, [m for m in members if m.name not in delivered], exc)
        finally:
            self._progress = None
