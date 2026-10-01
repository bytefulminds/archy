<div align="center">

<img src="docs/archy-logo.png" alt="Archy logo" width="128">

# Archy

**Browse, preview and manage archives without extracting them to disk.**

[![pipeline](https://gitlab.com/bytefulminds/archy/badges/main/pipeline.svg)](https://gitlab.com/bytefulminds/archy/-/pipelines)
![license: MIT](https://img.shields.io/badge/license-MIT-45C0CD)
![python: 3.11+](https://img.shields.io/badge/python-3.11%2B-45C0CD)
![platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux-CD7E45)

</div>

<p align="center">
  <img src="docs/screenshots/browse-thumbnails.png" alt="Archy showing a folder of photos inside a 7z archive as thumbnails, with one photo open in the preview pane" width="900">
</p>

Archy is a desktop app for looking inside zip, 7z, tar and other archives the
way you would look at a normal folder. Open an archive, walk its folders, see
thumbnails of every photo, play a video, read a PDF or a source file, and pull
out only what you need. Nothing you browse is written to your disk.

It was built for very large, encrypted personal backups: think a 50 GB 7z with a
hundred thousand files, where "extract everything first" is not an option.

> **Status:** early, but used daily. Version 0.1.0 with installers for Windows,
> macOS and Linux is being prepared. Until then, run it from source (see
> [Getting started](#getting-started)). The interface is available in English
> and Dutch.

## Why Archy

**Private by design.** Reading and previewing happen in memory. The bytes of a
file are decompressed into RAM and handed straight to the viewer, so no copy of
your photos or documents ends up in a temp folder. Passwords are kept in memory
only and are never stored.

**Built for big archives.** When you open a folder, Archy decodes the whole
folder in a single background pass and makes every thumbnail along the way,
with a progress indicator. Once it finishes, the folder browses instantly. Solid
7z archives are read front to back in one go instead of once per file, which is
the difference between seconds and minutes on large backups.

**Gentle on memory.** Archy sets its own memory budget (a share of your RAM with
a reserve kept free for the system), and you can tune every buffer under
*Settings > Advanced*. If a folder or a video does not fit, an optional
temp-files mode can take over (see [Privacy and temp files](#privacy-and-temp-files)).

## Features

- **Formats:** zip (including ZipCrypto and WinZip AES), 7z (including AES-256
  with encrypted file names), tar, tar.gz, tar.bz2 and tar.xz on every platform.
  With [libarchive](https://libarchive.org) installed it also reads rar, iso,
  cpio, cab, xar and ar.
- **Preview in memory:** images, video (with frame-by-frame stepping),
  PDF, text and source code (with automatic encoding detection).
- **Four views:** list, compact, thumbnails and large thumbnails, plus a sidebar
  that filters by type (images, videos, documents, other).
- **Search** the whole archive by name or path, or filter the current folder.
- **Responsive with huge archives:** the index of an archive is read on a
  background thread while a spinner shows, so opening a 100,000-file encrypted
  7z never freezes the window.
- **Extract** a selection or the whole archive, with a progress window that
  shows the file being written, speed and time left. Extraction streams to
  disk, checks CRCs, and can be cancelled or sent to the background. Drag files
  out of Archy to extract them where you drop them.
- **Edit zip and 7z archives:** add files and folders (also by dragging them in),
  create folders, rename and delete. Changes are collected first and written
  when you save: Archy writes a complete new version next to the original,
  checks it, and only then replaces the original, so a failed or cancelled
  save never damages it. A 7z is saved as a stream on a background thread,
  with progress, using a few MB of memory whatever its size, and it keeps its
  password, encrypted file names, Store or compression, non-solid blocks and
  file times. RAR and the other libarchive formats are read-only.
- **Edit lock:** archives Archy only reads show a lock next to their name; its
  tooltip says why. Encrypted archives start locked, so nothing changes by
  accident. An encrypted 7z made with *Store* can be unlocked on purpose with
  one click (a dialog first explains what saving will do); an encrypted,
  compressed 7z stays locked, because rewriting it would mean compressing
  everything again.
- **Settings** for the language, start view, density, preview fit, accent
  colours, the temp-files mode and memory limits. A notice tells you when a
  change only applies after reopening the archive or restarting the app.
- **Languages:** English and Dutch, following the system language by default.
  Another language is one JSON file, without rebuilding Archy; see
  [TRANSLATING.md](TRANSLATING.md). Plural forms for languages such as Polish
  and Russian are supported.
- **File associations:** open an archive from the command line, with "Open with"
  on Windows and Linux, or by dropping it on the Dock icon on macOS.

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/preview-code.png" alt="A Python file from the archive previewed as text"></td>
    <td width="50%"><img src="docs/screenshots/extract-dialog.png" alt="The extraction window showing the current file, a progress bar, speed and time left"></td>
  </tr>
  <tr>
    <td align="center"><sub>Source code and text preview, straight from memory</sub></td>
    <td align="center"><sub>Extracting a large backup: current file, speed, time left</sub></td>
  </tr>
</table>

## Privacy and temp files

Archy only writes to disk when you ask it to: when you extract, and when you
save an edited archive. Everything else stays in memory.

Some archives are simply bigger than your RAM. For those, *Settings >
Temporary files* has three work modes:

| Mode | What it does |
| --- | --- |
| **Everything in memory** (default) | Nothing is written. A file that does not fit in memory cannot be previewed. |
| **To disk when memory is tight** | Memory first. What does not fit (a huge folder, a large video or PDF) goes to the temp folder. |
| **Always use temporary files** | The open folder is unpacked to the temp folder instead of RAM, and videos and PDFs play from there. Lowest memory use, a bit slower. |

Temp files are **always encrypted** with AES-256. The key is random, exists
only in Archy's memory and changes every session. Files get random names
without extensions, so the temp folder reveals nothing except sizes. Archy
cleans up when it closes (or when you press *Clean up now*). If it ever crashes,
what it leaves behind is unreadable without the lost key, and it is removed
the next time Archy cleans up.

<p align="center">
  <img src="docs/screenshots/settings-temp-files.png" alt="The temp-files settings tab with the work mode, location and cleanup options" width="520">
</p>

## Getting started

You need Python 3.11 or newer.

```bash
git clone https://gitlab.com/bytefulminds/archy.git
cd archy
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
archy                              # or: python -m archy
```

You can pass an archive to open it directly: `archy ~/Backups/photos.7z`.

zip, 7z and tar work out of the box on every platform. For rar and the other
libarchive formats, install the native libarchive library:

| Platform | Command |
| --- | --- |
| macOS | `brew install libarchive` |
| Debian / Ubuntu | `sudo apt install libarchive13` |
| Fedora | `sudo dnf install libarchive` |
| Windows | Optional: place `libarchive.dll` next to the app |

`python scripts/check_libarchive.py` tells you whether Archy can find it.
Optionally, `pip install -e ".[video-fallback]"` adds PyAV as a fallback video
decoder.

## Keyboard shortcuts

On macOS, read Cmd for Ctrl.

| Action | Shortcut |
| --- | --- |
| Open an archive | Ctrl+O |
| Close the archive | Ctrl+W |
| Search the whole archive | Ctrl+Shift+F |
| Filter the current folder | Ctrl+F |
| Go up one folder | Backspace |
| Extract selection / everything | Ctrl+E / Ctrl+Shift+E |
| Add files / new folder | Ctrl+Shift+A / Ctrl+Shift+N |
| Rename / delete | F2 / Delete |
| Copy the path inside the archive | Ctrl+Shift+C |
| Save changes | Ctrl+S |
| Full screen preview | F11 |
| Play or pause a video | Space |
| Step one frame (paused video) | Scroll wheel |
| Settings | Ctrl+, |

## Tips for large 7z backups

How a 7z was created matters a lot for browsing speed. In a *solid* archive,
many files are compressed together as one block, so reading one file means
decompressing everything before it in that block. Archy reads a whole folder in
one pass to soften this, but a folder spread over several large blocks is still
slow.

For a backup you want to browse, choose in 7-Zip:

- **Solid block size:** *Non-solid*, or a small block size. This costs a little
  compression but makes every file reachable on its own.
- **Compression level:** *Store* for media that is already compressed
  (JPEG, RAW, video). It is much faster and hardly any bigger.
- **Encryption:** AES-256 with *Encrypt file names* checked. Archy asks for the
  password once per archive and keeps it in memory only.

A backup made this way can also be edited in Archy: click the lock next to its
name to unlock it. Saving then rewrites the archive next to the original (it
needs as much free space as the archive, for a moment) and replaces the original
only after checking the new version.

Archives with more than 65,536 files are fully supported.

## Development

```bash
pip install -e ".[dev]"
pytest                                  # the full test suite
QT_QPA_PLATFORM=offscreen pytest        # the same, without a display
ruff check . && ruff format --check .   # lint and formatting
```

The code is organised in three layers:

```
src/archy/
  core/       archive model, format detection and backends (no Qt)
  services/   preview providers, encrypted temp files, formatting helpers
  ui/         PySide6 windows and widgets
    main_window/  the main window, split into one mixin per concern
    theme/        the "Soft Depth" theme: palette, icons, stylesheet, fonts
packaging/    PyInstaller spec plus the Windows, macOS and Linux installers
scripts/      developer tools, including the README screenshot generator
tests/        pytest suite (headless core tests plus offscreen UI tests)
```

New to the code? Start with [ARCHITECTURE.md](ARCHITECTURE.md). It explains the
layers, the in-memory rule, the backend contract, the memory policy and the
threading model. [packaging/README.md](packaging/README.md) describes how the
installers are built.

Contributions are welcome. Please keep `archy.core` free of Qt, add a test for
behaviour you change, and make sure `pytest` and `ruff` pass before opening a
merge request. Code, comments and docs are in English; UI texts go through
`tr()` and the JSON catalogs (see [TRANSLATING.md](TRANSLATING.md)). Translations
to new languages are very welcome.

## Roadmap

- **0.1.0:** installers for Windows (Inno Setup), macOS (.dmg) and Linux
  (AppImage), built in CI.
- Browse into archives nested inside an archive.
- More interface languages (German, Polish, Russian, ...): contributions welcome.
- Rar support on Windows without a separate libarchive install.
- Breadcrumb dropdowns to jump to sibling folders, and back/forward navigation.

## Built with

[PySide6 / Qt 6](https://doc.qt.io/qtforpython-6/),
[py7zr](https://github.com/miurahr/py7zr),
[pyzipper](https://github.com/danifus/pyzipper),
[libarchive-c](https://github.com/Changaco/python-libarchive-c),
[pycryptodomex](https://www.pycryptodome.org),
[psutil](https://github.com/giampaolo/psutil) and
[charset-normalizer](https://github.com/jawah/charset_normalizer).
The interface uses the Inter, Sora and JetBrains Mono fonts (SIL Open Font
License).

## License

MIT. See [LICENSE](LICENSE).
