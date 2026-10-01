from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from archy.ui.theme import palette

# The app's icon set: inline line-SVGs drawn to match the Soft Depth design, so
# every glyph is an exact vector (no icon font, no external files). Call
# symbol_icon("<name>", size, color) to get a tinted QIcon. Results are cached
# per (name, size, color).
#
# Each value below is the inner SVG of a 24x24 symbol; elements carry
# class="ico" (a 1.6px stroked line icon) or class="ico-fill" (a solid shape),
# which _render_svg_icon turns into the requested color at render time.
_ICON_SVG: dict[str, str] = {
    "search": '<circle class="ico" cx="11" cy="11" r="6.5"/><path class="ico" d="M20 20l-4.3-4.3"/>',  # noqa: E501
    "folder": '<path class="ico" d="M4 6.5c0-.83.67-1.5 1.5-1.5H10l1.6 2h7.4c.83 0 1.5.67 1.5 1.5v8.5c0 1.1-.9 2-2 2H5.5c-.83 0-1.5-.67-1.5-1.5z"/>',  # noqa: E501
    "refresh": '<path class="ico" d="M4 12a8 8 0 0 1 13.6-5.7M20 12a8 8 0 0 1-13.6 5.7"/><path class="ico" d="M17 4v3.5h-3.5M7 20v-3.5h3.5"/>',  # noqa: E501
    "minus": '<path class="ico" d="M5 12h14"/>',
    "square": '<rect class="ico" x="6" y="6" width="12" height="12" rx="1.5"/>',
    "restore": '<rect class="ico" x="8" y="8" width="10" height="10" rx="1.5"/><path class="ico" d="M6 15V7a1.5 1.5 0 0 1 1.5-1.5H15"/>',  # noqa: E501
    "x": '<path class="ico" d="M6 6l12 12M18 6L6 18"/>',
    "grid": '<rect class="ico" x="4" y="4" width="6.5" height="6.5" rx="1.3"/><rect class="ico" x="13.5" y="4" width="6.5" height="6.5" rx="1.3"/><rect class="ico" x="4" y="13.5" width="6.5" height="6.5" rx="1.3"/><rect class="ico" x="13.5" y="13.5" width="6.5" height="6.5" rx="1.3"/>',  # noqa: E501
    "image": '<rect class="ico" x="3.5" y="4.5" width="17" height="15" rx="2"/><circle class="ico" cx="9" cy="10" r="1.6"/><path class="ico" d="M4 17l5-5 3.5 3.5L16 12l4 4.5"/>',  # noqa: E501
    "video": '<rect class="ico" x="3" y="6" width="13" height="12" rx="2"/><path class="ico" d="M16.5 10l4-2.4v8.8l-4-2.4z"/>',  # noqa: E501
    "doc": '<path class="ico" d="M7 3.5h7l4 4V19a1.5 1.5 0 0 1-1.5 1.5H7A1.5 1.5 0 0 1 5.5 19V5A1.5 1.5 0 0 1 7 3.5z"/><path class="ico" d="M14 3.5V8h4"/><path class="ico" d="M8.5 13h7M8.5 16h5"/>',  # noqa: E501
    "dots": '<circle class="ico-fill" cx="6" cy="12" r="1.5"/><circle class="ico-fill" cx="12" cy="12" r="1.5"/><circle class="ico-fill" cx="18" cy="12" r="1.5"/>',  # noqa: E501
    "chev_right": '<path class="ico" d="M9 6l7 6-7 6"/>',
    "chev_left": '<path class="ico" d="M15 6l-7 6 7 6"/>',
    "chev_down": '<path class="ico" d="M6 9l6 6 6-6"/>',
    "list": '<path class="ico" d="M4 6.5h16M4 12h16M4 17.5h16"/>',
    "compact": '<path class="ico" d="M4 6.5h9M4 12h9M4 17.5h9"/><circle class="ico" cx="19" cy="6.5" r="1"/><circle class="ico" cx="19" cy="12" r="1"/><circle class="ico" cx="19" cy="17.5" r="1"/>',  # noqa: E501
    "thumb": '<rect class="ico" x="4" y="4" width="7" height="7" rx="1.3"/><rect class="ico" x="13" y="4" width="7" height="7" rx="1.3"/><rect class="ico" x="4" y="13" width="7" height="7" rx="1.3"/><rect class="ico" x="13" y="13" width="7" height="7" rx="1.3"/>',  # noqa: E501
    "thumb_lg": '<rect class="ico" x="3.5" y="3.5" width="8.5" height="8.5" rx="1.3"/><rect class="ico" x="12" y="3.5" width="8.5" height="8.5" rx="1.3"/><rect class="ico" x="3.5" y="12" width="8.5" height="8.5" rx="1.3"/><rect class="ico" x="12" y="12" width="8.5" height="8.5" rx="1.3"/>',  # noqa: E501
    "info": '<circle class="ico" cx="12" cy="12" r="8.2"/><path class="ico" d="M12 11v5.2"/><circle cx="12" cy="8.3" r="1" fill="currentColor"/>',  # noqa: E501
    "rotate": '<path class="ico" d="M4.5 10.5A7.5 7.5 0 1 1 5 15"/><path class="ico" d="M4.5 5.5v5h5"/>',  # noqa: E501
    "zoom_out": '<circle class="ico" cx="11" cy="11" r="6.5"/><path class="ico" d="M20 20l-4.3-4.3"/><path class="ico" d="M8.2 11h5.6"/>',  # noqa: E501
    "zoom_in": '<circle class="ico" cx="11" cy="11" r="6.5"/><path class="ico" d="M20 20l-4.3-4.3"/><path class="ico" d="M11 8.2v5.6M8.2 11h5.6"/>',  # noqa: E501
    "fullscreen": '<path class="ico" d="M4 9V5h4M20 9V5h-4M4 15v4h4M20 15v4h-4"/>',
    "play": '<path class="ico-fill" d="M6.5 4.5v15l13-7.5z"/>',
    "pause": '<path class="ico-fill" d="M7 4.5h3v15H7zM14 4.5h3v15h-3z"/>',
    "back10": '<path class="ico" d="M6 6.5A8.5 8.5 0 1 1 4.4 13"/><path class="ico" d="M6 4v3.2H9.2"/>',  # noqa: E501
    "fwd10": '<path class="ico" d="M18 6.5A8.5 8.5 0 1 0 19.6 13"/><path class="ico" d="M18 4v3.2h-3.2"/>',  # noqa: E501
    "download": '<path class="ico" d="M12 3.5v11.5M8 11.5l4 4 4-4"/><path class="ico" d="M4.5 17v2.5A1.5 1.5 0 0 0 6 21h12a1.5 1.5 0 0 0 1.5-1.5V17"/>',  # noqa: E501
    "check": '<path class="ico" d="M5 12.5l4.5 4.5L19 7.5" style="stroke-width:2.2"/>',
    "folder_plus": '<path class="ico" d="M4 6.5c0-.83.67-1.5 1.5-1.5H10l1.6 2h7.4c.83 0 1.5.67 1.5 1.5v8.5c0 1.1-.9 2-2 2H5.5c-.83 0-1.5-.67-1.5-1.5z"/><path class="ico" d="M12 10.5v5M9.5 13h5"/>',  # noqa: E501
    "cog": '<circle class="ico" cx="12" cy="12" r="3"/><path class="ico" d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>',  # noqa: E501
    "file_plus": '<path class="ico" d="M13 3.5H7A1.5 1.5 0 0 0 5.5 5v14A1.5 1.5 0 0 0 7 20.5h10A1.5 1.5 0 0 0 18.5 19V9L13 3.5z"/><path class="ico" d="M13 3.5V9h5.5"/><path class="ico" d="M11.5 12.5v4M9.5 14.5h4"/>',  # noqa: E501
    "lock": '<rect class="ico" x="5" y="10.5" width="14" height="10" rx="2"/><path class="ico" d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/><circle cx="12" cy="15.5" r="1.4" fill="currentColor"/>',  # noqa: E501
    "lock_open": '<rect class="ico" x="5" y="10.5" width="14" height="10" rx="2"/><path class="ico" d="M8 10.5V7.5a4 4 0 0 1 7.7-1.6"/><circle cx="12" cy="15.5" r="1.4" fill="currentColor"/>',  # noqa: E501
    "camera": '<path class="ico" d="M4 8.5A1.5 1.5 0 0 1 5.5 7H8l1.2-2h5.6L16 7h2.5A1.5 1.5 0 0 1 20 8.5V18a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18z"/><circle class="ico" cx="12" cy="13" r="3.3"/>',  # noqa: E501
}

# Friendly aliases: the semantic names the rest of the app asks for, mapped onto
# the drawing names above. This lets call sites say symbol_icon("folder_zip")
# or symbol_icon("rotate_right") without caring which base glyph is reused.
_ICON_ALIASES: dict[str, str] = {
    "folder_zip": "folder",
    "folder_open": "folder",
    "movie": "video",
    "picture_as_pdf": "doc",
    "description": "doc",
    "note_stack": "dots",
    "insert_drive_file": "doc",
    "audio_file": "doc",
    "settings": "cog",
    "sort": "list",
    "more_vert": "dots",
    "view_list": "list",
    "view_headline": "compact",
    "grid_view": "thumb_lg",
    "apps": "thumb",
    "rotate_left": "rotate",
    "rotate_right": "rotate",
    "chevron_left": "chev_left",
    "chevron_right": "chev_right",
    "chevron_down": "chev_down",
    "fullscreen_exit": "fullscreen",
    "unarchive": "download",
    "select_all": "check",
    "play_arrow": "play",
    "fast_forward": "fwd10",
    "fast_rewind": "back10",
    "close": "x",
    "remove": "minus",
    "crop_square": "square",
    "filter_none": "restore",
}
_icon_cache: dict[tuple[str, int, str], QIcon] = {}


def _render_svg_icon(inner: str, size: int, color: str) -> QIcon:
    body = inner.replace('class="ico-fill"', f'fill="{color}" stroke="none"')
    body = body.replace(
        'class="ico"',
        f'fill="none" stroke="{color}" stroke-width="1.6" '
        'stroke-linecap="round" stroke-linejoin="round"',
    )
    body = body.replace("currentColor", color)
    doc = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>'
    renderer = QSvgRenderer(QByteArray(doc.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def symbol_icon(name: str, size: int = 18, color: str | None = None) -> QIcon:
    """A QIcon rendered from the inline line-SVG icon set, tinted to a color."""
    key_name = _ICON_ALIASES.get(name, name)
    inner = _ICON_SVG.get(key_name)
    if inner is None:
        return QIcon()
    color = color or palette.TEXT_SECONDARY
    key = (key_name, size, color)
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached
    icon = _render_svg_icon(inner, size, color)
    _icon_cache[key] = icon
    return icon
