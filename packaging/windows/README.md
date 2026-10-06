# Windows packaging

Produces a standard `Setup.exe` (Inno Setup) that installs Archy into
Program Files with a Start-menu shortcut, an uninstaller, and optional archive
file associations.

## Build

In PowerShell, from the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
# -> packaging\windows\Output\Archy-<version>-setup.exe
```

`build.ps1` does everything: it creates `.venv` with the project and
PyInstaller (Python 3.11+ via the `py` launcher), installs Inno Setup 6 with
winget when it is missing, builds `dist\Archy\` with the venv's Python, runs
`Archy.exe --self-test` on it (stops when a library is missing or an HTML page
cannot be shown) and compiles `archy.iss`. With `-Publish` (and
`$env:GITLAB_TOKEN`) it also adds the installer to the GitLab release.

No native library is required. zip, 7z and tar (.gz/.bz2/.xz) are read with pure
Python (zipfile / py7zr / tarfile). `libarchive.dll` is **optional** and only
adds rar-read plus a few exotic formats (cpio/iso/cab); Windows builds ship fine
without it.

## Notes

- Without `libarchive.dll`, opening a rar/cpio/iso/... shows a clear "this format
  needs libarchive" message; zip/7z/tar keep working. To enable those formats,
  set `$env:LIBARCHIVE` to the path of `libarchive.dll` before running
  `build.ps1`: the spec bundles it next to `Archy.exe`, where `native.py` finds
  it at runtime. Sourcing
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
