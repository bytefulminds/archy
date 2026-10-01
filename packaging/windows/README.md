# Windows packaging

Produces a standard `Setup.exe` (Inno Setup) that installs Archy into
Program Files with a Start-menu shortcut, an uninstaller, and optional archive
file associations.

## Prerequisites

- Windows 10/11 (x64), Python 3.11+.
- The project installed with dev extras and PyInstaller:
  `pip install -e ".[dev]" pyinstaller`
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) (provides the `ISCC` compiler).

No native library is required. zip, 7z and tar (.gz/.bz2/.xz) are read with pure
Python (zipfile / py7zr / tarfile). `libarchive.dll` is **optional** and only
adds rar-read plus a few exotic formats (cpio/iso/cab); Windows builds ship fine
without it.

## Build

```bat
:: 1. Build the app bundle (one folder with Archy.exe + _internal\)
pyinstaller packaging\archy.spec --noconfirm

:: 2. (optional) add rar/exotic support: drop libarchive.dll next to the exe
::    copy C:\path\to\libarchive.dll dist\Archy\

:: 3. (optional) sanity-check the bundle
dist\Archy\Archy.exe

:: 4. Build the installer
ISCC packaging\windows\archy.iss
:: -> packaging\windows\Output\Archy-<version>-setup.exe
```

## Notes

- Without `libarchive.dll`, opening a rar/cpio/iso/... shows a clear "this format
  needs libarchive" message; zip/7z/tar keep working. To enable those formats,
  ship the DLL (step 2): `native.py` finds it next to the exe at runtime. Sourcing
  a working `libarchive.dll` with its dependency chain on Windows is fiddly, which
  is exactly why it is optional.
- File associations are behind an **unchecked** task in the installer, so a
  normal install never hijacks `.zip`. When ticked, Archy is added to the
  "Open with" list for zip/7z/tar/gz/rar and can be set as the default by the
  user.
- Code signing is not set up (no certificate). Unsigned installers trigger a
  SmartScreen warning on first run; document the "More info -> Run anyway" step
  for users, or add signing in CI once a certificate exists.
- App icon: the exe icon (via the spec) and the installer icon
  (`SetupIconFile=..\archy.ico`) both use `packaging/archy.ico`.
