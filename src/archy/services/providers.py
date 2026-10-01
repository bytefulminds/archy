from __future__ import annotations

import contextlib
from pathlib import Path

from PySide6.QtCore import QIODevice, Qt
from PySide6.QtGui import QFontDatabase, QImage
from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from archy.i18n import tr
from archy.services.preview import PreviewKind, kind_for_name
from archy.services.text_decode import decode_text, looks_binary
from archy.ui import theme
from archy.ui.image_view import ImageView


class _VideoContainer(QWidget):
    """The video preview's root widget. It is focusable so the keyboard reaches
    it: Space toggles play/pause, and while paused the mouse wheel steps
    frame-by-frame (up = forward, down = back). The builder wires the two
    callbacks below; the mouse wheel works whenever the pointer is over the
    video, focus or not."""

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._zx_toggle = None  # () -> None : play/pause
        self._zx_step = None  # (direction: int) -> None : frame step when paused

    def keyPressEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if event.key() == Qt.Key.Key_Space and self._zx_toggle is not None:
            self._zx_toggle()
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if self._zx_step is not None and event.angleDelta().y() != 0:
            self._zx_step(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)


def _format_ms(ms: int) -> str:
    if ms <= 0:
        return "0:00"
    total = ms // 1000
    return f"{total // 60}:{total % 60:02d}"


# Qt preview widgets built entirely from in-memory bytes. Nothing is written to
# disk. This module imports PySide6, so it is never pulled in by the Qt-free
# core tests; the pure decoding logic lives in text_decode.py and is tested on
# its own.

_INFO_STYLE = "padding:6px 10px;"


def _message(text: str) -> QWidget:
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    return label


class _ClickSlider(QSlider):
    """A slider that jumps to the clicked position (for video seeking)."""

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            width = max(1, self.width())
            frac = min(1.0, max(0.0, event.position().x() / width))
            value = self.minimum() + round((self.maximum() - self.minimum()) * frac)
            self.setValue(int(value))
            self.sliderMoved.emit(int(value))
            event.accept()
            return
        super().mousePressEvent(event)


# Emoji and symbol fonts per platform, tried after the monospace font for any
# character it lacks (Qt falls back on its own too, but naming them makes the
# colour emoji fonts win over a random font that happens to have the glyph).
_FALLBACK_FAMILIES = (
    "Apple Color Emoji",  # macOS
    "Segoe UI Emoji",  # Windows
    "Segoe UI Symbol",
    "Noto Color Emoji",  # Linux
    "Noto Sans Symbols 2",
)


def text_font():  # noqa: ANN201 - QFont
    """The font for text previews: the system monospace font, with emoji and
    symbol fonts as fallbacks."""
    from PySide6.QtGui import QFontInfo

    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    # The resolved family, not the alias ("Monospace" on Linux): an alias that
    # does not match a real font is skipped, and then an emoji font (they have
    # digits and spaces too, for keycaps) would draw the plain text.
    primary = QFontInfo(font).family() or font.family()
    available = set(QFontDatabase.families())
    fallbacks = [f for f in _FALLBACK_FAMILIES if f in available and f != primary]
    font.setFamilies([primary, *fallbacks])
    return font


def make_text_edit(text: str, *, editable: bool) -> QPlainTextEdit:
    """A notepad-style text field using the app palette (readable in any theme).

    ``editable`` False makes it a read-only preview; True lets the user edit and
    the window save the result back into the archive.
    """
    view = QPlainTextEdit()
    view.setReadOnly(not editable)
    view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    view.setFont(text_font())
    view.setPlainText(text)
    view.document().setModified(False)
    return view


def _build_text(name: str, data: bytes) -> QWidget:
    if looks_binary(data):
        return _message(tr("preview.binary", name=name))

    decoded = decode_text(data)
    # Plain notepad-style field: uses the application palette, so it stays
    # readable in both light and dark themes. (Syntax highlighting can come
    # back later as a theme-aware option.)
    view = QPlainTextEdit()
    view.setReadOnly(True)
    view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    view.setFont(text_font())
    view.setPlainText(decoded.text)
    return view


def _build_image(name: str, data: bytes) -> QWidget:
    image = QImage.fromData(data)
    if image.isNull():
        return _message(tr("preview.image_failed", name=name))

    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    viewer = ImageView()
    viewer.setObjectName("imageViewer")
    viewer.set_image(image)

    def _cleanup() -> None:
        viewer.release()

    # Expose the viewer + rotate so the preview panel's own chrome can drive it.
    container._zx_cleanup = _cleanup  # type: ignore[attr-defined]
    container._zx_image_view = viewer  # type: ignore[attr-defined]

    layout.addWidget(viewer, stretch=1)
    return container


def _build_pdf(name: str, source: bytes | Path | QIODevice) -> QWidget:
    try:
        from PySide6.QtCore import QBuffer, QByteArray
        from PySide6.QtPdf import QPdfDocument
        from PySide6.QtPdfWidgets import QPdfView
    except Exception:  # noqa: BLE001 - QtPdf may be absent on some builds
        return _message(tr("preview.no_pdf", name=name))

    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)

    document = QPdfDocument(container)
    buffer = None
    if isinstance(source, QIODevice):
        # Encrypted temp file: QPdfDocument reads through the decrypting device
        # on demand; nothing raw ever touches the disk.
        buffer = source
        document.load(source)
        container._pdf_device = source  # type: ignore[attr-defined]
    elif isinstance(source, (bytes, bytearray)):
        # Feed the bytes to QPdfDocument through an in-memory QBuffer - no temp
        # file. The document reads on demand, so the buffer (and its backing
        # bytes) must stay alive as long as the view: keep them on the container.
        byte_array = QByteArray(bytes(source))
        buffer = QBuffer(container)
        buffer.setData(byte_array)
        buffer.open(QBuffer.OpenModeFlag.ReadOnly)
        document.load(buffer)
        container._pdf_bytes = byte_array  # type: ignore[attr-defined]
        container._pdf_buffer = buffer  # type: ignore[attr-defined]
    else:
        document.load(str(source))

    view = QPdfView(container)
    view.setDocument(document)
    view.setPageMode(QPdfView.PageMode.MultiPage)
    view.setZoomMode(QPdfView.ZoomMode.FitInView)  # page fits the panel

    # Keep strong references alive for the lifetime of the widget.
    def _cleanup() -> None:
        document.close()
        if buffer is not None:
            buffer.close()

    container._pdf_document = document  # type: ignore[attr-defined]
    container._zx_cleanup = _cleanup  # type: ignore[attr-defined]

    layout.addWidget(view)
    return container


# Players being torn down (see _dispose_player_async): (player, audio, buffer)
# tuples. Holding the Python wrappers here guarantees Python never destroys a
# player itself - a Python-side delete runs the destructor while holding the
# GIL, which is exactly the deadlock below. Entries drop out once Qt has
# destroyed the objects. app.run() force-exits at quit if any is still here.
_PENDING_VIDEO_TEARDOWNS: list = []


def _dispose_player_async(player: object, audio: object, buffer: object) -> None:
    """Tear a QMediaPlayer down without ever holding the Python GIL.

    Root cause of the macOS hangs (Qt6 FFmpeg backend): pause(), stop(),
    setSource(), setVolume() and the destructor wait for the
    QFFmpeg::AudioRenderer thread, and that renderer calls back into
    PySide-wrapped objects, which needs the GIL. PySide keeps the GIL held for
    the whole C++ call when Python makes it, so the caller holds the GIL while
    waiting for a renderer that is waiting for the GIL: a deadlock. It does not
    matter which thread the Python call is made from - calling from a separate
    QThread.run() only moved the freeze (the GUI thread then blocked on the GIL).
    Moving the player to another thread is not an option either: its internal
    position timer stays behind on the GUI thread and fires on the deleted
    player (crash).

    So Python makes no blocking call at all. We only POST work: queued slot
    invocations (pause, stop) and deleteLater(). Qt then runs them from its own
    C++ event loop on the GUI thread, where the GIL is released - exactly like a
    plain C++ Qt app, in which this teardown is routine. The renderer can take
    the GIL, finish, and the wait completes. Posting never blocks, so this
    function returns immediately.
    """
    from PySide6.QtCore import QMetaObject

    objects = [obj for obj in (player, audio, buffer) if obj is not None]
    record = (player, audio, buffer)
    _PENDING_VIDEO_TEARDOWNS.append(record)

    def _forget() -> None:
        # The last object has been destroyed by Qt; the wrappers are now inert.
        with contextlib.suppress(ValueError):
            _PENDING_VIDEO_TEARDOWNS.remove(record)

    # Events are processed in posting order, so the last object in the list is
    # destroyed last (player first, while its buffer is still alive).
    objects[-1].destroyed.connect(_forget)  # type: ignore[attr-defined]

    queued = Qt.ConnectionType.QueuedConnection
    for slot in ("pause", "stop"):
        with contextlib.suppress(Exception):
            QMetaObject.invokeMethod(player, slot, queued)
    for obj in objects:
        with contextlib.suppress(Exception):
            obj.deleteLater()  # type: ignore[attr-defined] - only posts an event


def _build_video(name: str, source: bytes | Path | QIODevice) -> QWidget:
    try:
        from PySide6.QtCore import QBuffer, QByteArray, QMetaObject, QSize, Qt, QUrl
        from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton
    except Exception:  # noqa: BLE001 - QtMultimedia may be absent
        return _message(tr("preview.no_video", name=name))

    _SKIP_MS = 10_000

    container = _VideoContainer()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)

    video = QVideoWidget(container)
    player = QMediaPlayer(container)
    audio = QAudioOutput(container)
    player.setAudioOutput(audio)
    player.setVideoOutput(video)

    buffer = None
    if isinstance(source, QIODevice):
        # Encrypted temp file (temp-file mode, or too large for RAM): the
        # player reads through the decrypting device, piece by piece. It is
        # torn down after the player, like the in-memory buffer below.
        player.setSourceDevice(source)
        buffer = source
        container._video_device = source  # type: ignore[attr-defined]
    elif isinstance(source, (bytes, bytearray)):
        # In-memory playback: hand the player a QBuffer over the bytes, no temp
        # file.
        byte_array = QByteArray(bytes(source))
        buffer = QBuffer(container)
        buffer.setData(byte_array)
        buffer.open(QBuffer.OpenModeFlag.ReadOnly)
        player.setSourceDevice(buffer)
        container._video_bytes = byte_array  # type: ignore[attr-defined]
        container._video_buffer = buffer  # type: ignore[attr-defined]
    else:
        player.setSource(QUrl.fromLocalFile(str(source)))

    # Controls styled to match the image stage: a glass pill with line-SVG icon
    # buttons (rewind 10s / play-pause / forward 10s), a teal seek slider and a
    # mono time readout.
    def _vbtn(icon: str) -> QToolButton:
        button = QToolButton(container)
        button.setObjectName("videoBtn")
        button.setIcon(theme.symbol_icon(icon, 18, "#DFE7EC"))
        button.setIconSize(QSize(18, 18))
        button.setFixedSize(30, 30)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    back_button = _vbtn("back10")
    play_button = _vbtn("play")
    fwd_button = _vbtn("fwd10")
    position = _ClickSlider(Qt.Orientation.Horizontal, container)
    position.setObjectName("videoSeek")
    time_label = QLabel("0:00 / 0:00", container)
    time_label.setObjectName("videoTime")

    def _sync_time() -> None:
        time_label.setText(f"{_format_ms(player.position())} / {_format_ms(player.duration())}")

    def toggle() -> None:
        # Posted, not called: pause() waits for the audio renderer, which must
        # never happen while Python holds the GIL (see _dispose_player_async).
        playing = player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        QMetaObject.invokeMethod(
            player, "pause" if playing else "play", Qt.ConnectionType.QueuedConnection
        )

    def skip(delta: int) -> None:
        target = player.position() + delta
        target = max(0, min(target, player.duration()))
        player.setPosition(target)

    def step_frame(direction: int) -> None:
        # Only step while paused; while playing the wheel is ignored. One frame
        # is derived from the video frame rate (fallback 25 fps).
        if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            return
        fps = 25.0
        with contextlib.suppress(Exception):
            rate = player.metaData().value(QMediaMetaData.Key.VideoFrameRate)
            if rate:
                fps = float(rate)
        step = max(1, round(1000.0 / fps))
        target = max(0, min(player.position() + direction * step, player.duration()))
        player.setPosition(target)

    def on_duration(duration: int) -> None:
        position.setRange(0, duration)
        _sync_time()

    def on_position(pos: int) -> None:
        if not position.isSliderDown():
            position.setValue(pos)
        _sync_time()

    def on_state(state: object) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        play_button.setIcon(theme.symbol_icon("pause" if playing else "play", 18, "#DFE7EC"))

    play_button.clicked.connect(toggle)
    back_button.clicked.connect(lambda: skip(-_SKIP_MS))
    fwd_button.clicked.connect(lambda: skip(_SKIP_MS))
    position.sliderMoved.connect(player.setPosition)
    player.durationChanged.connect(on_duration)
    player.positionChanged.connect(on_position)
    player.playbackStateChanged.connect(on_state)

    control_bar = QFrame(container)
    control_bar.setObjectName("videoControls")
    controls = QHBoxLayout(control_bar)
    controls.setContentsMargins(10, 6, 12, 6)
    controls.setSpacing(6)
    controls.addWidget(back_button)
    controls.addWidget(play_button)
    controls.addWidget(fwd_button)
    controls.addWidget(position, stretch=1)
    controls.addWidget(time_label)

    def cleanup() -> None:
        # THE hard constraint (from repeated macOS stack dumps): on the Qt6
        # FFmpeg backend, any call that waits for the audio-renderer thread -
        # pause(), stop(), setAudioOutput(None), setSource(), setVolume(), the
        # destructor - deadlocks when Python makes it, because Python holds the
        # GIL the renderer needs. So Python must NEVER make those calls here.
        # We only detach the video sink
        # (that call is safe and frees the about-to-be-deleted QVideoWidget), then
        # post the teardown (queued pause/stop + deleteLater) so Qt runs it from
        # its C++ event loop with the GIL released - no Python call ever waits
        # on the renderer, so nothing can deadlock. See _dispose_player_async.
        with contextlib.suppress(Exception):
            player.positionChanged.disconnect()
            player.durationChanged.disconnect()
            player.playbackStateChanged.disconnect()
        with contextlib.suppress(Exception):
            player.setVideoOutput(None)  # safe; frees the doomed QVideoWidget
        _dispose_player_async(player, audio, buffer)

    container._video_player = player  # type: ignore[attr-defined]
    container._video_audio = audio  # type: ignore[attr-defined]
    container._zx_toggle = toggle  # type: ignore[attr-defined]
    container._zx_step = step_frame  # type: ignore[attr-defined]
    container._zx_cleanup = cleanup  # type: ignore[attr-defined]

    layout.addWidget(video, stretch=1)
    layout.addWidget(control_bar)
    return container


def build_preview_widget(name: str, data: bytes) -> QWidget:
    """Return a Qt widget previewing ``data`` (an entry's in-memory bytes).

    Chooses the provider from the file name. Text and images are implemented;
    PDF and video get a placeholder until their providers land.
    """
    kind = kind_for_name(name)
    if kind is PreviewKind.TEXT:
        return _build_text(name, data)
    if kind is PreviewKind.IMAGE:
        return _build_image(name, data)
    if kind is PreviewKind.PDF:
        return _build_pdf(name, data)
    if kind is PreviewKind.VIDEO:
        return _build_video(name, data)
    return _message(tr("preview.unsupported", name=name))


def build_preview_widget_from_device(name: str, device: QIODevice) -> QWidget:
    """Preview a video or PDF from the encrypted temp store, through a
    decrypting QIODevice (see services/encrypted_device.py): the file is read on
    demand, never loaded whole and never on disk unencrypted."""
    kind = kind_for_name(name)
    if kind is PreviewKind.PDF:
        return _build_pdf(name, device)
    if kind is PreviewKind.VIDEO:
        return _build_video(name, device)
    return _message(tr("preview.unsupported", name=name))


def build_preview_widget_from_path(name: str, path: str | Path) -> QWidget:
    """Preview a file that was spilled to a temp file because it was too large
    for RAM (the memory-pressure fallback). Only PDF and video benefit: Qt reads
    both from a path on demand, so the whole file is never loaded into memory.
    """
    kind = kind_for_name(name)
    source = Path(path)
    if kind is PreviewKind.PDF:
        return _build_pdf(name, source)
    if kind is PreviewKind.VIDEO:
        return _build_video(name, source)
    return _message(tr("preview.unsupported", name=name))
