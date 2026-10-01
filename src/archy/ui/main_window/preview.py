from __future__ import annotations

import contextlib

from PySide6.QtCore import (
    QEvent,
    QItemSelectionModel,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from archy.core import memory
from archy.core.tree import TreeNode
from archy.i18n import tr
from archy.services.encrypted_device import EncryptedBlobDevice
from archy.services.formatting import format_modified, format_ratio, format_size
from archy.services.preview import PreviewKind, kind_for_name
from archy.services.providers import (
    build_preview_widget,
    build_preview_widget_from_device,
    make_text_edit,
)
from archy.services.text_decode import decode_text, looks_binary
from archy.ui import soft_dialog, theme
from archy.ui.archive_model import (
    category_of,
)
from archy.ui.main_window.chrome import (
    FullscreenWindow as _FullscreenWindow,
)
from archy.ui.main_window.constants import (
    _EDITOR_MAX,
    _MEDIA_KINDS,
    _NAV_KINDS,
    _PREVIEW_SIZE_LIMIT,
    _TEXT_PREVIEW_BYTES,
    _VIDEO_COPIES,
    _VIDEO_COPIES_BUFFERED,
    FOOTER_HEIGHT,
    category_label,
)


class PreviewMixin:
    """Builds and drives the preview pane (media chrome, loading, display).

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    # ------------------------------------------------------------- preview
    def _build_preview_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("previewPanel")
        panel.setMinimumWidth(420)
        col = QVBoxLayout(panel)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        head = QFrame()
        head.setObjectName("previewHeader")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(13, 0, 10, 0)
        hl.setSpacing(10)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(1)
        self._preview_title = QLabel(tr("preview.none_selected"))
        self._preview_title.setObjectName("previewTitle")
        self._preview_sub = QLabel("")
        self._preview_sub.setObjectName("previewSub")
        titles.addWidget(self._preview_title)
        titles.addWidget(self._preview_sub)
        hl.addLayout(titles, stretch=1)

        self._info_button = self._preview_action(
            "info", tr("preview.file_info"), self._show_file_info
        )
        self._rotate_button = self._preview_action(
            "rotate_right", tr("preview.rotate"), self._rotate_preview
        )
        self._prev_button = self._preview_action(
            "chevron_left", tr("preview.previous"), lambda: self._navigate_media(-1)
        )
        self._next_button = self._preview_action(
            "chevron_right", tr("preview.next"), lambda: self._navigate_media(1)
        )
        hl.addWidget(self._info_button)
        hl.addWidget(self._rotate_button)
        hl.addWidget(self._prev_button)
        hl.addWidget(self._next_button)
        col.addWidget(head)

        self._preview_container = QWidget()
        self._preview_container.setObjectName("imageStage")
        self._preview_layout = QVBoxLayout(self._preview_container)
        self._preview_layout.setContentsMargins(14, 14, 14, 6)
        self._build_stage_overlay(self._preview_container)
        self._preview_container.installEventFilter(self)
        col.addWidget(self._preview_container, stretch=1)

        footer = QFrame()
        footer.setObjectName("previewFooter")
        footer.setFixedHeight(FOOTER_HEIGHT)
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(14, 10, 14, 10)
        fl.setSpacing(12)
        meta = QVBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(2)
        self._footer_kind = QLabel("—")
        self._footer_kind.setObjectName("footerKind")
        self._footer_meta = QLabel("")
        self._footer_meta.setObjectName("footerMeta")
        meta.addWidget(self._footer_kind)
        meta.addWidget(self._footer_meta)
        fl.addLayout(meta, stretch=1)

        self._extract_menu_button = QPushButton(tr("preview.extract_menu"))
        self._extract_menu_button.setIcon(theme.symbol_icon("unarchive", 18, theme.TEXT_PRIMARY))
        self._extract_menu = QMenu(self)
        self._extract_menu.addAction(tr("preview.extract_selection"), self.extract_selected)
        self._extract_menu.addAction(tr("preview.extract_all"), self.extract_all)
        self._extract_menu_button.setMenu(self._extract_menu)
        self._extract_menu_button.setEnabled(False)

        self._fullscreen_button = QPushButton(tr("preview.fullscreen"))
        self._fullscreen_button.setObjectName("primaryAction")
        self._fullscreen_button.setIcon(theme.symbol_icon("fullscreen", 18, theme.ACCENT_ON))
        self._fullscreen_button.clicked.connect(self.toggle_fullscreen)
        self._fullscreen_button.setEnabled(False)
        fl.addWidget(self._extract_menu_button)
        fl.addWidget(self._fullscreen_button)
        col.addWidget(footer)

        self._show_placeholder(tr("preview.select_file"))
        return panel

    # ------------------------------------------------ on-image stage overlay
    def _build_stage_overlay(self, stage: QWidget) -> None:
        """Floating controls drawn over the image stage: left/right nav chevrons
        and a bottom-centre zoom pill (zoom out / % / zoom in | rotate | full)."""
        self._stage_nav_prev = self._stage_nav_button(
            stage, "chevron_left", lambda: self._navigate_media(-1)
        )
        self._stage_nav_next = self._stage_nav_button(
            stage, "chevron_right", lambda: self._navigate_media(1)
        )

        self._stage_controls = QFrame(stage)
        self._stage_controls.setObjectName("stageControls")
        row = QHBoxLayout(self._stage_controls)
        row.setContentsMargins(6, 5, 6, 5)
        row.setSpacing(2)
        zoom_out = self._stage_pill_button(
            "zoom_out", tr("preview.zoom_out"), self._zoom_out_preview
        )
        self._stage_zoom_val = QLabel("100%")
        self._stage_zoom_val.setObjectName("stageZoomVal")
        self._stage_zoom_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zoom_in = self._stage_pill_button("zoom_in", tr("preview.zoom_in"), self._zoom_in_preview)
        divider = QLabel("│")
        divider.setObjectName("stageDivider")
        rotate = self._stage_pill_button("rotate_right", tr("preview.rotate"), self._rotate_preview)
        full = self._stage_pill_button(
            "fullscreen", tr("preview.fullscreen"), self.toggle_fullscreen
        )
        row.addWidget(zoom_out)
        row.addWidget(self._stage_zoom_val)
        row.addWidget(zoom_in)
        row.addWidget(divider)
        row.addWidget(rotate)
        row.addWidget(full)

        self._stage_overlay_widgets = (
            self._stage_nav_prev,
            self._stage_nav_next,
            self._stage_controls,
        )
        self._show_stage_overlay(False)

    def _stage_nav_button(self, parent: QWidget, icon: str, slot) -> QToolButton:  # noqa: ANN001
        button = QToolButton(parent)
        button.setObjectName("stageNav")
        button.setIcon(theme.symbol_icon(icon, 20, theme.TEXT_PRIMARY))
        button.setIconSize(QSize(20, 20))
        button.setFixedSize(30, 30)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def _stage_pill_button(self, icon: str, tip: str, slot) -> QToolButton:  # noqa: ANN001
        button = QToolButton()
        button.setObjectName("stageZoom")
        button.setIcon(theme.symbol_icon(icon, 18, "#DFE7EC"))
        button.setIconSize(QSize(18, 18))
        button.setFixedSize(26, 26)
        button.setToolTip(tip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def _show_stage_overlay(self, visible: bool) -> None:
        for widget in self._stage_overlay_widgets:
            widget.setVisible(visible)
        if visible:
            self._position_stage_overlay()

    def _position_stage_overlay(self) -> None:
        stage = self._preview_container
        w, h = stage.width(), stage.height()
        cy = (h - 30) // 2
        self._stage_nav_prev.move(24, cy)
        self._stage_nav_next.move(w - 30 - 24, cy)
        self._stage_controls.adjustSize()
        cw = self._stage_controls.width()
        self._stage_controls.move((w - cw) // 2, h - self._stage_controls.height() - 22)
        for widget in self._stage_overlay_widgets:
            widget.raise_()

    def _current_image_view(self):  # noqa: ANN201
        item = self._preview_layout.itemAt(0)
        widget = item.widget() if item else None
        return getattr(widget, "_zx_image_view", None) if widget else None

    def _zoom_in_preview(self) -> None:
        view = self._current_image_view()
        if view is not None:
            view.zoom_in()

    def _zoom_out_preview(self) -> None:
        view = self._current_image_view()
        if view is not None:
            view.zoom_out()

    def _rotate_preview(self) -> None:
        # The rotate glyph points counter-clockwise, so rotate that way to match.
        view = self._current_image_view()
        if view is not None:
            view.rotate_ccw()

    def _on_preview_scale_changed(self, percent: int) -> None:
        self._stage_zoom_val.setText(f"{percent}%")

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001, N802
        if obj is self._preview_container and event.type() == QEvent.Type.Resize:
            self._position_stage_overlay()
        return super().eventFilter(obj, event)

    def _preview_action(self, icon_name: str, tip: str, slot) -> QToolButton:  # noqa: ANN001
        button = QToolButton()
        button.setObjectName("previewAction")
        button.setAutoRaise(True)
        button.setIcon(theme.symbol_icon(icon_name, 20, theme.TEXT_SECONDARY))
        button.setIconSize(QSize(20, 20))
        button.setToolTip(tip)
        button.clicked.connect(slot)
        button.setEnabled(False)
        return button

    # ------------------------------------------------------------- preview
    def _clear_preview_widget(self) -> None:
        while self._preview_layout.count():
            item = self._preview_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Release media resources (stop players, close documents/buffers,
                # let go of any spilled temp file) before the widget is deleted.
                cleanup = getattr(widget, "_zx_cleanup", None)
                if callable(cleanup):
                    with contextlib.suppress(Exception):  # cleanup is best-effort
                        cleanup()
                widget.deleteLater()

    def _clear_editor(self) -> None:
        self._editor = None
        self._editing_node = None
        self._edit_encoding = "utf-8"
        self._update_save_action()

    def _set_preview_widget(self, widget: QWidget, *, full_bleed: bool = False) -> None:
        # While fullscreen, previews render into the fullscreen window instead of
        # the side panel (so arrow-key navigation shows there).
        if self._fs_window is not None:
            self._fs_window.set_content(widget)
            return
        # Controls/inputs keep a 10px margin from the window edges; only pannable
        # media (images, PDF) is shown edge to edge.
        self._clear_preview_widget()
        if full_bleed:
            self._preview_layout.setContentsMargins(14, 14, 14, 6)
        else:
            self._preview_layout.setContentsMargins(10, 10, 10, 10)
        self._preview_layout.addWidget(widget)
        self._bind_stage_overlay(widget)
        # A video preview takes keyboard focus so Space (play/pause) and the
        # wheel (frame-step while paused) work right after selecting it.
        if hasattr(widget, "_video_player"):
            widget.setFocus()

    def _bind_stage_overlay(self, widget: QWidget) -> None:
        """Show the on-image zoom pill / nav chevrons only for image previews and
        keep the zoom readout in sync with the viewer."""
        view = getattr(widget, "_zx_image_view", None)
        has_image = view is not None
        self._rotate_button.setEnabled(has_image)
        if has_image:
            view.set_fill_mode(self._settings.preview_fill)
            view.scale_changed.connect(self._on_preview_scale_changed)
            self._on_preview_scale_changed(view.zoom_percent())
        self._show_stage_overlay(has_image)

    def _show_placeholder(self, text: str) -> None:
        self._clear_editor()
        self._set_fullscreenable(False)
        self._set_nav_enabled(False)
        self._set_preview_title(tr("preview.none_selected"), "")
        self._set_footer(None)
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        self._set_preview_widget(label)
        self._temp.clear_preview()

    def _show_loading(self, name: str) -> None:
        # An animated (indeterminate) bar, so a slow decode never looks hung.
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(24, 24, 24, 24)
        box.setSpacing(14)
        box.addStretch(1)
        label = QLabel(tr("preview.loading", name=name))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        bar = QProgressBar()
        bar.setObjectName("loadingBar")
        bar.setRange(0, 0)  # indeterminate: the chunk animates
        bar.setTextVisible(False)
        bar.setFixedWidth(240)
        box.addWidget(label)
        box.addWidget(bar, alignment=Qt.AlignmentFlag.AlignHCenter)
        box.addStretch(1)
        self._set_preview_widget(holder)

    def _set_preview_title(self, title: str, sub: str) -> None:
        self._preview_title.setText(title)
        self._preview_sub.setText(sub)

    def _set_footer(self, node: TreeNode | None) -> None:
        if node is None or node.is_dir:
            self._footer_kind.setText("—")
            self._footer_meta.setText("")
            return
        cat = category_of(node)
        self._footer_kind.setText(category_label(cat))
        ext = node.name.rsplit(".", 1)[-1].upper() if "." in node.name else tr("kind.other")
        self._footer_meta.setText(f"{ext} · {format_size(node.size)}")

    def _media_size_limit(self, kind: PreviewKind, node: TreeNode | None = None) -> int:
        # Video plays from a memory buffer, so large clips play when there is
        # room - measured live against spare RAM (free minus the reserve always
        # left for the OS, see archy.core.memory). Reading it briefly holds the
        # bytes up to three times (archive read + result + the player's copy);
        # one already in the folder buffer only needs the player's copy. Too
        # big: the temp-file fallback (if on) or a "too large" message, never a
        # machine that runs out of memory.
        if kind is PreviewKind.VIDEO:
            buffered = (
                node is not None
                and node.entry is not None
                and self._backend is not None
                and self._backend.has_buffered(node.entry.path)
            )
            fits = memory.spare() // (_VIDEO_COPIES_BUFFERED if buffered else _VIDEO_COPIES)
            fixed = memory.limits().video_memory_bytes  # Settings > Advanced
            return fits if fixed is None else min(fits, fixed)
        return _PREVIEW_SIZE_LIMIT

    def _set_fullscreenable(self, flag: bool) -> None:
        self._fullscreenable = flag
        self._fullscreen_action.setEnabled(flag)
        self._fullscreen_button.setEnabled(flag)

    def _set_nav_enabled(self, flag: bool) -> None:
        """Enable the prev/next arrows above the preview. Used for every
        previewable file (documents included, since they have no side chevrons);
        at the ends of the folder a click simply does nothing."""
        self._prev_button.setEnabled(flag)
        self._next_button.setEnabled(flag)

    def toggle_fullscreen(self) -> None:
        if self._fs_window is not None:
            self._fs_window.close()
            return
        if not self._fullscreenable:
            return
        item = self._preview_layout.itemAt(0)
        widget = item.widget() if item else None
        if widget is None:
            return
        self._req_id += 1  # ignore any pending preview load while fullscreen
        self._preview_layout.removeWidget(widget)
        self._fs_window = _FullscreenWindow(widget, self._exit_fullscreen, self._navigate_media)
        self._fs_window.showFullScreen()

    def _navigate_media(self, step: int) -> None:
        rows = self._proxy.rowCount()
        current = self._selection.currentIndex()
        row = current.row() if current.isValid() else -1
        r = row
        while True:
            r += step
            if r < 0 or r >= rows:
                return  # no more previewable files in this direction
            index = self._proxy.index(r, 0)
            node = self._node_at(index)
            if node is not None and not node.is_dir and kind_for_name(node.name) in _NAV_KINDS:
                self._selection.setCurrentIndex(
                    index,
                    QItemSelectionModel.SelectionFlag.ClearAndSelect
                    | QItemSelectionModel.SelectionFlag.Rows,
                )  # drives the preview (into fullscreen if active)
                self._current_view().scrollTo(index)
                return

    def _exit_fullscreen(self, widget: QWidget) -> None:
        self._fs_window = None
        full_bleed = self._req_kind in (PreviewKind.IMAGE, PreviewKind.PDF)
        self._set_preview_widget(widget, full_bleed=full_bleed)

    def _show_file_info(self) -> None:
        current = self._selection.currentIndex()
        node = self._node_at(current) if current.isValid() else None
        if node is None or node.is_dir:
            return
        compressed = node.entry.compressed_size if node.entry is not None else None
        lines = [
            tr("info.name", value=node.name),
            tr("info.path", value=node.path),
            tr("info.type", value=category_label(category_of(node))),
            tr("info.size", value=format_size(node.size)),
        ]
        if compressed is not None:
            lines.append(tr("info.compressed", value=format_size(compressed)))
            lines.append(tr("info.ratio", value=format_ratio(node.size, compressed)))
        if node.modified is not None:
            lines.append(tr("info.modified", value=format_modified(node.modified)))
        soft_dialog.message(self, tr("preview.file_info"), "\n".join(lines))

    def _preview_node(self, node: TreeNode, request_id: int) -> None:
        if self._backend is None or node.entry is None:
            return
        # Clear any lingering status from a previous preview (e.g. the temp-file
        # notice), so it never sticks around while showing a different file.
        self._set_status(tr("status.ready"))
        # First file opened in this folder -> warm the whole folder in one pass.
        self._warm_folder()
        kind = kind_for_name(node.name)

        # Small text in a writable, seek-friendly archive opens for editing,
        # loaded in full on the UI thread (zip reads are seek-based, so fast).
        # Slow backends (solid 7z) use the threaded path below instead, so a big
        # block decode shows the loading bar rather than freezing the window.
        if (
            kind is PreviewKind.TEXT
            and self._backend.writable
            and self._backend.fast_seek
            and not self._is_locked()
            and node.size <= _EDITOR_MAX
        ):
            self._open_editable_text(node)
            return

        too_big = kind is not PreviewKind.TEXT and node.size > self._media_size_limit(kind, node)
        from_disk = kind in (PreviewKind.PDF, PreviewKind.VIDEO) and (
            self._temp.mode() == "disk"  # Always use temporary files
            or (too_big and self._temp.enabled())  # To disk when memory is tight
        )
        if from_disk:
            # Video/PDF from the ENCRYPTED temp store: streamed in (never held
            # whole in memory) unless the folder pass already put it there,
            # then read through a decrypting device - nothing raw on disk.
            self._req_node = node
            self._req_kind = kind
            self._clear_editor()
            if self._backend.spilled_blob(node.entry.path) is None:
                self._show_loading(tr("preview.spilling", name=node.name))
            else:
                self._show_loading(node.name)
            self._loader.submit_spill(
                request_id,
                self._backend,
                node.entry,
                self._temp,
                lambda rid=request_id: rid != self._req_id,
            )
            return
        if too_big:
            hint = "" if self._temp.enabled() else tr("preview.temp_hint")
            self._show_placeholder(
                tr("preview.too_big", name=node.name, size=format_size(node.size), hint=hint)
            )
            return

        # Everything else is read off the UI thread so the window never freezes.
        self._req_node = node
        self._req_kind = kind
        self._clear_editor()
        self._show_loading(node.name)
        max_bytes = _TEXT_PREVIEW_BYTES if kind is PreviewKind.TEXT else None
        self._loader.submit(request_id, self._backend, node.entry, max_bytes)

    def _open_editable_text(self, node: TreeNode) -> None:
        assert self._backend is not None and node.entry is not None
        data = self._read_unlocking(node)
        if data is None:
            return

        if looks_binary(data):
            self._show_placeholder(tr("preview.binary", name=node.name))
            return

        decoded = decode_text(data, limit=None)
        if self._is_locked():
            # Reading it needed a password: keep it read-only (no save-back).
            self._clear_editor()
            self._set_fullscreenable(False)
            self._set_nav_enabled(True)
            self._set_preview_widget(make_text_edit(decoded.text, editable=False))
            return
        editor = make_text_edit(decoded.text, editable=True)
        self._set_fullscreenable(False)
        self._set_nav_enabled(True)
        self._set_preview_widget(editor)
        self._editor = editor
        self._editing_node = node
        self._edit_encoding = decoded.encoding
        editor.document().modificationChanged.connect(self._update_save_action)
        self._update_save_action()

    def _on_preview_loaded(self, request_id: int, data: bytes) -> None:
        if request_id != self._req_id or self._req_node is None:
            return
        node = self._req_node
        if self._req_kind is PreviewKind.TEXT:
            if looks_binary(data):
                self._show_placeholder(tr("preview.binary", name=node.name))
                return
            decoded = decode_text(data)
            text = decoded.text
            if node.size > len(data):
                text += tr("preview.text_partial")
            self._set_fullscreenable(False)
            self._set_nav_enabled(True)
            self._set_preview_widget(make_text_edit(text, editable=False))
        else:
            full_bleed = self._req_kind in (PreviewKind.IMAGE, PreviewKind.PDF)
            self._set_fullscreenable(self._req_kind in _MEDIA_KINDS)
            self._set_nav_enabled(True)
            self._set_preview_widget(build_preview_widget(node.name, data), full_bleed=full_bleed)
        # This in-memory preview replaces any previously spilled one; drop its
        # temp file now that the old widget has released it.
        self._temp.clear_preview()

    def _on_spill_progress(self, request_id: int, done: int, total: int) -> None:
        if request_id != self._req_id:
            return
        if total > 0:
            self._set_status(tr("status.spilling", percent=done * 100 // total))

    def _on_preview_spilled(self, request_id: int, blob: object) -> None:
        if request_id != self._req_id or self._req_node is None:
            return
        node = self._req_node
        full_bleed = self._req_kind in (PreviewKind.IMAGE, PreviewKind.PDF)
        self._set_fullscreenable(self._req_kind in _MEDIA_KINDS)
        self._set_nav_enabled(True)
        device = EncryptedBlobDevice(self._temp.store, blob)
        self._set_preview_widget(
            build_preview_widget_from_device(node.name, device), full_bleed=full_bleed
        )
        # Make it visible where this preview comes from. Stays until the next
        # selection (which resets the status in _preview_node).
        self._set_status(tr("status.shown_from_temp"))

    def _on_preview_failed(self, request_id: int, reason: str) -> None:
        if request_id != self._req_id or self._req_node is None:
            return
        node = self._req_node
        if reason == "encrypted":
            pw = self._prompt_password(tr("password.prompt", name=node.name))
            if pw is None:
                self._show_placeholder(tr("preview.encrypted_no_password", name=node.name))
                return
            if self._backend is not None:
                self._backend.set_password(pw)
            self._archive_encrypted = True
            self._update_lock()
            self._req_id += 1
            self._preview_node(node, self._req_id)
        else:
            self._show_placeholder(tr("preview.read_failed", name=node.name, error=reason))

    def _on_thumbnail_ready(self, _path: str = "") -> None:
        # A thumbnail finished decoding, or a folder pass finished; repaint the
        # grids so new thumbnails show and pending ones are re-requested. On a
        # big folder results arrive by the hundred per second, and each repaint
        # runs the (Python) tile painter for every visible tile, so repaints are
        # coalesced to ~25 per second instead of one per thumbnail.
        timer = getattr(self, "_thumb_repaint_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(40)
            timer.timeout.connect(self._repaint_grids)
            self._thumb_repaint_timer = timer
        if not timer.isActive():
            timer.start()

    def _repaint_grids(self) -> None:
        for view in (self._grid, self._large):
            if view.isVisible():
                view.viewport().update()
