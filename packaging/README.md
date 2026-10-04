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

A PyInstaller build is **OS- and architecture-specific**: build each target on
that OS/arch. There is no cross-compilation.

## Quick start

```bash
pip install -e ".[dev]" pyinstaller
pyinstaller packaging/archy.spec --noconfirm   # -> dist/Archy[.app]
```

Then the per-OS installer:

- **Linux:** `./packaging/build_appimage.sh` -> `dist/Archy-<arch>.AppImage`
  (needs `libxcb-cursor0` on the build host; fetches `appimagetool`
  automatically). Verified end-to-end in CI.
- **Windows:** see [`windows/README.md`](windows/README.md) (needs
  `libarchive.dll` + Inno Setup).
- **macOS:** see [`macos/README.md`](macos/README.md) (needs
  `brew install libarchive`; produces an unsigned `.dmg`).

## CI

`.gitlab-ci.yml` has a `build` stage:

- `build:linux-appimage` runs automatically on version tags and builds the
  AppImage in CI (manual otherwise).
- `build:windows` / `build:macos` are manual jobs tagged `windows` / `macos`.
  They only start once matching GitLab runners are connected, so they never
  block a normal pipeline. Enable them by registering those runners.

## Branding

The Archy logo is wired in: `packaging/archy.ico` (Windows exe + installer),
`packaging/archy.icns` (macOS .app), `packaging/appimage/archy.png` (AppImage +
desktop entry), and `src/archy/resources/icons/archy.png` (the in-app header
mark). All are generated from `docs/Archy_square.png`; regenerate them if the
source logo changes.

## Still to do for the installers

- **Windows:** builds without any native lib (zip/7z/tar work). Optional:
  `libarchive.dll` for rar/exotic, and code signing to avoid SmartScreen
  warnings.
- **macOS:** unsigned by design (no Apple Developer account); document the
  Gatekeeper right-click-Open step. Consider a universal2 build if needed.
- **Runners:** connect Windows + macOS GitLab runners to build all three in CI.
- **Fonts in the wheel:** already handled (`pyproject.toml` `artifacts`).
