#!/usr/bin/env bash
# Build a Linux AppImage for Archy.
#
# Steps: PyInstaller onedir bundle -> assemble an AppDir -> appimagetool.
# Run from the repo root inside the project venv:
#   ./packaging/build_appimage.sh
#
# Requirements: the project installed with dev extras + pyinstaller, binutils
# (PyInstaller needs objdump on Linux), and appimagetool (downloaded to
# packaging/ automatically when missing).
# The build host also needs libxcb-cursor0 available so it can be bundled
# (Qt's xcb platform plugin needs it at runtime on the user's machine).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP="Archy"
ARCH="${ARCH:-$(uname -m)}"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/archy/__init__.py)"
DIST="$ROOT/dist"
APPDIR="$ROOT/build/${APP}.AppDir"

# The Python of the project venv when there is one (PyInstaller bundles what
# that Python can import), else the active one.
PY="${PYTHON:-}"
[ -z "$PY" ] && [ -x "$ROOT/.venv/bin/python" ] && PY="$ROOT/.venv/bin/python"
[ -z "$PY" ] && PY="$(command -v python3 || command -v python)"

echo ">> PyInstaller bundle ($PY)"
"$PY" -m PyInstaller packaging/archy.spec --noconfirm --distpath "$DIST" --workpath "$ROOT/build"

echo ">> Self-test of the bundle"
QT_QPA_PLATFORM=offscreen "$DIST/$APP/$APP" --self-test

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
OUT="$DIST/${APP}-${VERSION}-linux-${ARCH}.AppImage"
ARCH="$ARCH" "$TOOL" "$APPDIR" "$OUT"
echo ">> Done: $OUT"
