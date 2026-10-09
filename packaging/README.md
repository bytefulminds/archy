# Packaging Archy

Archy is packaged with **PyInstaller** from one shared spec
(`packaging/archy.spec`), then wrapped into a per-OS installer:

| OS       | Bundle             | Installer          | Build on            |
|----------|--------------------|--------------------|---------------------|
| Linux    | `dist/Archy/`  | AppImage           | Linux (also CI)     |
| Windows  | `dist/Archy/`  | Inno Setup `.exe`  | Windows             |
| macOS    | `dist/Archy.app` | `.dmg` (unsigned) | macOS               |

The spec bundles the OFL fonts, the Qt multimedia (FFmpeg) and PDF plugins used
by the preview, and py7zr's lazily-imported submodules. zip, 7z and tar
(.gz/.bz2/.xz) are read with pure Python, so **no native library is required**.
**libarchive** is optional - it adds rar-read and a few exotic formats
(cpio/iso/cab). When it is present at build time the spec bundles it and
`core/native.py` points `LIBARCHIVE` at the bundled copy at runtime; when it is
absent (typically Windows), those formats are simply reported as unsupported and
everything else works.

Run PyInstaller with the project venv's Python (`python -m PyInstaller`): it
bundles what that Python can import, and the spec stops when a required
library is missing. The build scripts then run `Archy --self-test` on the
bundle, which imports the lazily loaded libraries, round-trips a 7z and an AES
zip in memory and checks the resources.

A PyInstaller build is **OS- and architecture-specific**: build each target on
that OS/arch. There is no cross-compilation.

## Quick start

```bash
pip install -e ".[dev]" pyinstaller
python -m PyInstaller packaging/archy.spec --noconfirm   # -> dist/Archy[.app]
```

Then the per-OS installer:

- **Linux:** `./packaging/build_appimage.sh` ->
  `dist/Archy-<version>-linux-<arch>.AppImage` (needs `binutils` and
  `libxcb-cursor0` on the build host; fetches `appimagetool` automatically).
- **Windows:** `packaging\windows\build.ps1` -> `Archy-<version>-setup.exe`
  (sets up the venv and Inno Setup itself); see
  [`windows/README.md`](windows/README.md).
- **macOS:** `./packaging/macos/build_dmg.sh` -> `Archy-<version>-macos-<arch>.dmg`
  (sets up the venv and libarchive itself); see
  [`macos/README.md`](macos/README.md).

## CI

`.gitlab-ci.yml` has a `build` and a `release` stage:

- `build:linux-appimage`, `build:windows` and `build:macos` run automatically
  on version tags (`vX.Y.Z`): each builds its installer and uploads it to the
  project's package registry. Linux runs on GitLab's shared runners, Windows
  and macOS on the project's own runners (tags `windows` / `macos`, shell
  executor). In other pipelines they are manual test builds.
- `release` waits for the three builds and creates the GitLab Release of the
  tag, with the changelog section of that version as text and the three
  installers as downloads (`scripts/release_notes.py`).

## Branding

The Archy logo is wired in: `packaging/archy.ico` (Windows exe + installer),
`packaging/archy.icns` (macOS .app), `packaging/appimage/archy.png` (AppImage +
desktop entry), and `src/archy/resources/icons/archy.png` (the in-app header
mark). All are generated from `docs/Archy_square.png`; regenerate them if the
source logo changes. The macOS icons (`archy.icns` and
`src/archy/resources/icons/archy_macos.png`, the Dock icon when running from
source) follow Apple's icon grid, a rounded square of 824 of 1024 pixels with a
soft shadow, so Archy is as large as the other apps in the Dock:
`python scripts/make_icons.py` makes both.

## Still to do for the installers

- **Windows:** builds without any native lib (zip/7z/tar work). Optional:
  `libarchive.dll` for rar/exotic, and code signing to avoid SmartScreen
  warnings.
- **macOS:** unsigned by design (no Apple Developer account); document the
  Gatekeeper "Open Anyway" step. Consider a universal2 build if needed.
- **Fonts in the wheel:** already handled (`pyproject.toml` `artifacts`).
