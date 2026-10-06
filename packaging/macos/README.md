# macOS packaging

Produces `Archy.app` and wraps it in an unsigned `.dmg`.

## Prerequisites

- macOS (Apple Silicon or Intel), Python 3.11+.
- The project installed with dev extras and PyInstaller:
  `pip install -e ".[dev]" pyinstaller`
- Homebrew libarchive: `brew install libarchive`. It is keg-only, so the
  PyInstaller spec looks it up in the usual Homebrew locations and bundles the
  dylib into the app; at runtime `native.py` points `LIBARCHIVE` at the bundled
  copy, so the app is self-contained. To use a specific dylib, set
  `LIBARCHIVE=/path/to/libarchive.dylib` before building.

## Build

```bash
./packaging/macos/build_dmg.sh
# -> dist/Archy.app  and  dist/Archy-<version>-macos-<arch>.dmg
```

Or just the app bundle:

```bash
pyinstaller packaging/archy.spec --noconfirm
```

## Gatekeeper (unsigned build)

There is no Apple Developer certificate, so the app is unsigned and notarization
is skipped. First launch on another Mac is blocked by Gatekeeper. Tell users to:

- Right-click (or Control-click) `Archy.app` -> **Open**, then confirm; or
- run `xattr -dr com.apple.quarantine /Applications/Archy.app` once.

## File associations

The spec's `Info.plist` declares Archy as a viewer for zip / 7z / tar / gzip
/ rar (`CFBundleDocumentTypes`). After the app is in `/Applications`, macOS
offers it under "Open With"; the app already handles the `QFileOpenEvent` that
"Open with" / dock drops deliver (see `app.py`). Users set it as the default via
Finder -> Get Info -> Open with -> Change All.

## Architecture note

A PyInstaller build is architecture-specific (arm64 or x86_64) - it is not a
universal2 binary. Build on each architecture, or set up a universal2 Python +
dependencies if a single fat binary is required. For v0.1.0, ship an arm64 build
(and optionally an x86_64 one) rather than universal2.
