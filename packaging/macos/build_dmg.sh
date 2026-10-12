#!/usr/bin/env bash
# Build Archy for macOS in one go: venv, PyInstaller, self-test, .dmg.
#
#   ./packaging/macos/build_dmg.sh            # -> dist/Archy-<version>-macos-<arch>.dmg
#   ./packaging/macos/build_dmg.sh --publish  # ... and add it to the GitLab release
#
# Needs Python 3.12 (brew install python@3.12) and Homebrew; installs
# libarchive with brew, and the locked versions from requirements/lock.txt into
# .venv (made again when the lock changes). --publish needs
# GITLAB_TOKEN (a personal access token with the api scope).
set -euo pipefail
cd "$(dirname "$0")/../.."

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/archy/__init__.py)"
DMG="dist/Archy-${VERSION}-macos-$(uname -m).dmg"

echo ">> Setup"
brew list libarchive >/dev/null 2>&1 || brew install libarchive
# Every build gets exactly the versions in the lock, on Python 3.12 like the
# other builds: the venv is made again whenever the lock changes.
LOCK="$(shasum -a 256 requirements/lock.txt | cut -d' ' -f1)"
if [ -x .venv/bin/python ] && [ "$(cat .venv/lock.sha256 2>/dev/null)" != "$LOCK" ]; then
    rm -rf .venv
fi
if [ ! -x .venv/bin/python ]; then
    PY312="$(command -v python3.12 || true)"
    [ -n "$PY312" ] || { echo "Python 3.12 not found: brew install python@3.12" >&2; exit 1; }
    "$PY312" -m venv .venv
    .venv/bin/python -m pip install -q --upgrade pip
    .venv/bin/python -m pip install -q --require-hashes -r requirements/lock.txt
    echo "$LOCK" > .venv/lock.sha256
fi
.venv/bin/python -m pip install -q --no-deps --no-build-isolation -e .

echo ">> Build"
rm -rf build dist
.venv/bin/python -m PyInstaller packaging/archy.spec --noconfirm

# No Developer ID: an ad-hoc signature (Apple Silicon runs no unsigned code,
# and Gatekeeper offers "Open Anyway" only for signed apps).
codesign --verify --deep --strict dist/Archy.app 2>/dev/null ||
    codesign --force --deep --sign - dist/Archy.app

echo ">> Self-test"
dist/Archy.app/Contents/MacOS/Archy --self-test

echo ">> Disk image"
STAGE="$(mktemp -d)"
cp -R dist/Archy.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname Archy -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"
echo ">> Done: $DMG"

if [ "${1:-}" = "--publish" ]; then
    .venv/bin/python scripts/publish_build.py "$DMG"
fi
