#!/usr/bin/env bash
# Build a Linux AppImage for Archy.
#
# Steps: PyInstaller onedir bundle -> assemble an AppDir -> appimagetool.
# Run from the repo root inside the project venv:
#   ./packaging/build_appimage.sh
#
# Requirements: the project installed with dev extras + pyinstaller, and
# appimagetool on PATH (or it is downloaded to packaging/ automatically).
# The build host also needs libxcb-cursor0 available so it can be bundled
# (Qt's xcb platform plugin needs it at runtime on the user's machine).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP="Archy"
ARCH="${ARCH:-$(uname -m)}"
DIST="$ROOT/dist"
APPDIR="$ROOT/build/${APP}.AppDir"

echo ">> PyInstaller bundle"
pyinstaller packaging/archy.spec --noconfirm --distpath "$DIST" --workpath "$ROOT/build"

echo ">> Assemble AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -r "$DIST/$APP" "$APPDIR/usr/bin/$APP"

install -m 0755 packaging/appimage/AppRun "$APPDIR/AppRun"
cp packaging/appimage/archy.desktop "$APPDIR/archy.desktop"
cp packaging/appimage/archy.png "$APPDIR/archy.png"
# Also place the icon/desktop where appimagetool and desktops expect them.
mkdir -p "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp packaging/appimage/archy.desktop "$APPDIR/usr/share/applications/"
cp packaging/appimage/archy.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/"

# Bundle libxcb-cursor.so.0 if the host has it (Qt xcb plugin needs it and
# PyInstaller does not pull ctypes-invisible system libs).
for cand in /usr/lib/*/libxcb-cursor.so.0 /usr/lib/libxcb-cursor.so.0; do
    if [ -f "$cand" ]; then
        cp "$cand" "$APPDIR/usr/bin/$APP/_internal/" && echo "   bundled $(basename "$cand")"
        break
    fi
done

echo ">> appimagetool"
TOOL="$ROOT/packaging/appimagetool-${ARCH}.AppImage"
if [ ! -x "$TOOL" ]; then
    echo "   downloading appimagetool"
    curl -fsSL -o "$TOOL" \
      "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
    chmod +x "$TOOL"
fi

# --appimage-extract-and-run avoids needing FUSE in the build environment (CI).
export APPIMAGE_EXTRACT_AND_RUN=1
OUT="$DIST/${APP}-${ARCH}.AppImage"
ARCH="$ARCH" "$TOOL" "$APPDIR" "$OUT"
echo ">> Done: $OUT"
