from __future__ import annotations

from archy.core import memory
from archy.ui import theme
from archy.ui.settings_dialog import open_settings


class SettingsMixin:
    """Opens the settings screen and applies changes live.

    Mixin for :class:`MainWindow`; relies on attributes/methods defined there.
    """

    # ------------------------------------------------------------ settings
    def open_settings_dialog(self) -> None:
        if open_settings(self, self._settings, on_prune=self._temp.prune):
            self._apply_settings()

    def _apply_settings(self) -> None:
        # Resources and temp-file mode: all live. Caches and threads resize now;
        # the folder buffer follows from the next folder pass.
        memory.configure(self._settings.limits())
        self._thumbs.apply_limits()
        if self._backend is not None:
            self._backend.apply_limits()
            self._temp.configure_backend(self._backend)
        self._apply_accent()
        self._apply_density()
        self._apply_preview_fill()
        # The start view applies on the next open; reflect it now too when an
        # archive is open and we are not showing search results.
        if self._backend is not None and not self._searching:
            index = self._settings.start_view
            self._view_group.button(index).setChecked(True)
            self._switch_view(index)

    def _apply_accent(self) -> None:
        """Re-theme the app with the chosen accent pair. QSS-driven surfaces,
        the palette and the tile painters update live; rebuild the few
        accent-tinted icons that were created once at construction."""
        theme.set_accent(self._settings.accent, self._settings.accent2)
        theme.refresh_app()
        self._save_button.setIcon(theme.symbol_icon("download", 18, theme.ACCENT_ON))
        if hasattr(self, "_archive_icon"):
            self._archive_icon.setPixmap(
                theme.symbol_icon("folder_zip", 18, theme.ACCENT).pixmap(18, 18)
            )
        for view in (self._grid, self._large, self._compact, self._table):
            view.viewport().update()

    def _apply_density(self) -> None:
        compact = self._settings.compact_density
        for view in (self._grid, self._large):
            view.itemDelegate().set_compact(compact)
            view.fit_tiles()
            view.scheduleDelayedItemsLayout()
        row_h = 24 if compact else 28
        self._table.verticalHeader().setDefaultSectionSize(row_h)
        self._compact.setSpacing(0 if compact else 1)

    def _apply_preview_fill(self) -> None:
        view = self._current_image_view()
        if view is not None:
            view.set_fill_mode(self._settings.preview_fill)
