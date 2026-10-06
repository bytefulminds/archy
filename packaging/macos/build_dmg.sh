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

# No Developer ID: the app gets an ad-hoc signature (Apple Silicon refuses to
# run unsigned code, and Gatekeeper only offers "Open Anyway" for signed apps).
# PyInstaller signs the pieces; make sure the bundle as a whole is valid.
echo ">> Checking the ad-hoc signature"
if ! codesign --verify --deep --strict "$DIST/${APP}.app" 2>/dev/null; then
    codesign --force --deep --sign - "$DIST/${APP}.app"
fi
codesign --verify --deep --strict "$DIST/${APP}.app"

echo ">> Staging .dmg contents (app + Applications symlink)"
STAGE="$(mktemp -d)"
cp -R "$DIST/${APP}.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

echo ">> Creating .dmg"
rm -f "$DMG"
hdiutil create -volname "$APP" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
echo ">> Done: $DMG"

# The app is not notarized (no Apple Developer account). On first launch users
# allow it once in System Settings > Privacy & Security > Open Anyway (macOS 15
# and later; on older versions right-click the app -> Open). See
# packaging/macos/README.md.
