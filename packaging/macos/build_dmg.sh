#!/usr/bin/env bash
# Build a macOS .app and wrap it in a .dmg. Run on macOS, from the repo root,
# inside the project venv:
#   ./packaging/macos/build_dmg.sh
#
# Requirements: Python 3.11+, the project installed with dev extras + pyinstaller,
# and Homebrew's libarchive (`brew install libarchive`) so the spec can bundle
# the dylib into the .app (Homebrew's libarchive is keg-only; the spec looks it
# up automatically, or set LIBARCHIVE=<path to libarchive.dylib> to override).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

APP="Archy"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/archy/__init__.py)"
ARCH="$(uname -m)"  # arm64 (Apple Silicon) or x86_64 (Intel): one .dmg per Mac type
DIST="$ROOT/dist"
DMG="$DIST/${APP}-${VERSION}-macos-${ARCH}.dmg"

echo ">> PyInstaller .app bundle"
pyinstaller packaging/archy.spec --noconfirm --distpath "$DIST" --workpath "$ROOT/build"

test -d "$DIST/${APP}.app" || { echo "ERROR: $DIST/${APP}.app not built"; exit 1; }

echo ">> Staging .dmg contents (app + Applications symlink)"
STAGE="$(mktemp -d)"
cp -R "$DIST/${APP}.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

echo ">> Creating .dmg"
rm -f "$DMG"
hdiutil create -volname "$APP" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
echo ">> Done: $DMG"

# The app is unsigned (no Apple Developer account). On first launch users must
# right-click the app -> Open (or run: xattr -dr com.apple.quarantine <app>).
# See packaging/macos/README.md.
