"""Concrete archive backends (subclasses of ``ArchiveBackend``).

- libarchive_backend: broad read-only access to many formats (zip, 7z, tar.*,
  cpio, iso, cab, xar, ar, rar-read) via libarchive.
- zip_backend: read + write for zip (stdlib zipfile; pyzipper for AES).
- sevenzip_backend: read + write for 7z (py7zr).

``factory.backend_for`` picks the right one for a given file.
"""
