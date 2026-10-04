# PyInstaller spec for Archy - one spec, all three OSes.
#
# Build (from the repo root, inside the project venv):
#   pyinstaller packaging/archy.spec --noconfirm
#
# Output (a one-folder bundle, used by every installer):
#   dist/Archy/            Linux + Windows  (wrapped into AppImage / Inno)
#   dist/Archy.app/        macOS            (wrapped into a .dmg)
#
# The bundle is a "onedir" build (COLLECT), because the macOS .app and the
# Windows/Linux installers all package a folder, not a single loose exe.
#
# What this spec makes sure ends up in the bundle:
#   - the bundled OFL fonts (the app loads them from resources/fonts at startup)
#   - the native libarchive shared library, when it can be located, so the app
#     reads the broad set of formats without a system install (see native.py)
#   - py7zr's submodules (some are imported lazily and PyInstaller misses them)
# PySide6 (incl. the Qt multimedia + PDF plugins and QtWebEngine used by the
# preview) is handled by PyInstaller's bundled PySide6 hooks.

import ctypes.util
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

# SPECPATH is the directory holding this .spec file (packaging/); the repo root
# is its parent.
ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

APP_NAME = "Archy"
ENTRY = str(SRC / "archy" / "__main__.py")

# Single source of truth for the version: src/archy/__init__.py __version__.
APP_VERSION = "0.0.0"
for _line in (SRC / "archy" / "__init__.py").read_text().splitlines():
    if _line.strip().startswith("__version__"):
        APP_VERSION = _line.split("=", 1)[1].strip().strip("\"'")
        break

# App icons: PyInstaller uses the .ico for the Windows exe and the .icns for the
# macOS .app bundle. Linux gets its icon through the AppImage (packaging/appimage).
ICON_ICO = str(ROOT / "packaging" / "archy.ico")
ICON_ICNS = str(ROOT / "packaging" / "archy.icns")

# --- data files -------------------------------------------------------------
# Ship the resources folder (fonts, the in-app logo, UI translations) next to the code, at the
# same relative path the app reads from (archy/resources/...).
datas = [
    (str(SRC / "archy" / "resources" / "fonts"), "archy/resources/fonts"),
    (str(SRC / "archy" / "resources" / "icons"), "archy/resources/icons"),
    (str(SRC / "archy" / "resources" / "i18n"), "archy/resources/i18n"),
]

# --- native libraries -------------------------------------------------------
# libarchive-c loads the native library via ctypes at runtime, which PyInstaller
# cannot detect on its own. Locate it and bundle it so the packaged app is
# self-contained. On Windows the DLL is provided during packaging (see
# packaging/windows/README); if nothing is found we fall back to the system
# library at runtime rather than failing the build.
def _locate_libarchive():
    # 1) explicit override (matches core/native.py's LIBARCHIVE handling)
    env = os.environ.get("LIBARCHIVE")
    if env and Path(env).is_file():
        return env
    # 2) macOS: prefer the Homebrew keg-only dylib over the old system one
    if sys.platform == "darwin":
        for c in (
            "/opt/homebrew/opt/libarchive/lib/libarchive.dylib",
            "/opt/homebrew/lib/libarchive.dylib",
            "/usr/local/opt/libarchive/lib/libarchive.dylib",
            "/usr/local/lib/libarchive.dylib",
        ):
            if Path(c).is_file():
                return c
    # 3) the normal ctypes lookup (Linux, Windows-with-DLL-on-PATH)
    found = ctypes.util.find_library("archive")
    if found and Path(found).is_file():
        return found
    return None


binaries = []
_arch_lib = _locate_libarchive()
if _arch_lib:
    binaries.append((_arch_lib, "."))
# py7zr ships a couple of small native deps (e.g. its codecs) - collect any.
binaries += collect_dynamic_libs("py7zr")
# pycryptodomex (encrypted temp files, AES zips) loads its C modules by file
# path; pyinstaller-hooks-contrib's hook-Cryptodome bundles them automatically.

# --- hidden imports ---------------------------------------------------------
# py7zr imports some codec/registry modules lazily; pull them all in. The other
# runtime deps (pyzipper, charset_normalizer, psutil, libarchive) are imported
# normally and found by the analysis.
hiddenimports = collect_submodules("py7zr")
# Imported lazily (only when Qt cannot read a HEIC/AVIF photo), so name them.
hiddenimports += ["pi_heif", "PIL.Image", "PIL.ImageOps"]
# QtWebEngine renders HTML previews and is imported lazily (services/web_view.py);
# naming it lets PyInstaller's PySide6 hooks bundle QtWebEngineProcess and its
# resources.
hiddenimports += ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"]
binaries += collect_dynamic_libs("pi_heif")

a = Analysis(
    [ENTRY],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim things we never use to keep the bundle smaller.
    excludes=["tkinter", "test", "unittest", "pydoc"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# One executable, launched windowed (no console). The .ico is used for the
# Windows exe; it is ignored on other platforms (macOS uses the BUNDLE icns).
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,  # macOS: deliver "Open with" file paths as argv
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_ICO,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

# macOS: wrap the collected bundle into a proper .app, and register the archive
# file types so "Open with" and dock drops reach the app (app.py already handles
# the QFileOpenEvent). The OS still needs this Info.plist to advertise them.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=ICON_ICNS,
        bundle_identifier="nl.archy.app",
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            # The languages Archy ships (resources/i18n), so macOS treats the app
            # as localized and native panels (file dialogs) follow the user's
            # language too.
            "CFBundleDevelopmentRegion": "en",
            "CFBundleLocalizations": ["en", "nl"],
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "Archive",
                    "CFBundleTypeRole": "Viewer",
                    "LSHandlerRank": "Alternate",
                    "LSItemContentTypes": [
                        "public.zip-archive",
                        "org.7-zip.7-zip-archive",
                        "public.tar-archive",
                        "org.gnu.gnu-zip-archive",
                        "com.rarlab.rar-archive",
                    ],
                },
            ],
        },
    )
