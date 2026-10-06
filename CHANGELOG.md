# Changelog

All notable changes to Archy. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Archy uses
[semantic versioning](https://semver.org) from 0.1.0 on.

The first entry, 0.0.1, is the starting point: what Archy could do when the
public repository was created. Everything under [Unreleased] goes into the
next release.

## [Unreleased]

### Added

- **Camera RAW previews.** CR2, CR3, NEF, ARW, RAF, ORF, RW2, PEF, DNG and
  more show the preview the camera stores inside the file (often full or half
  resolution) as thumbnail and preview, upright, with the camera details. No
  RAW converter needed.
- **Photoshop previews.** PSD files show their flattened image, or their
  thumbnail when that cannot be read.
- **Music.** MP3, FLAC, WAV, M4A/AAC, Ogg, Opus, WMA and AIFF play in the
  preview, with cover art, title, artist and album.
- **Slideshow.** F5 or the play button under a photo shows the folder's photos
  full screen, one after another; Space pauses, the time per photo is set in
  Settings.
- **Back and forward.** Arrows at the start of the path, the mouse's back and
  forward buttons and Alt+Left / Alt+Right (Cmd+[ / Cmd+] on macOS) go back and
  forward through the folders you visited.
- **Folders next to this one.** Click a / in the path to jump to a folder at
  that level.
- **Archives inside an archive (experimental).** Double-click a zip, 7z or tar
  inside the archive to open it like a folder: from memory, read-only, with
  the way back in the path.
- **About Archy** in Settings: the version, made by Bytefulminds, the license,
  and the open source software Archy uses with their licenses.
- **Sort in every view.** A sort button next to the views sorts the folder by
  name, type, size or date modified, ascending or descending; it works in the
  thumbnail views too and follows the column headers of the list view. Folders
  always stay first, and Archy remembers the choice.

### Changed

- The view buttons above the file list show only their icons when the window
  is too narrow for their labels.
- The file name (or "No file selected") is centred in the preview header.
- Items with the same size or date (such as folders) are ordered by name
  instead of in no particular order.

### Fixed

- The icon on the active view button was hard to see on its teal background.
- Opening another archive could show thumbnails from the previous one for
  files with the same path.
- The grey path (in search results) and size under a selected thumbnail were
  hard to read on the teal selection; they are now shown in a light colour.
- Closing an archive (or opening another one) while a folder was still
  loading, or with a file selected, left the selection and the progress bars in
  the status strip under the file list until the next archive opened. Leaving a
  folder while it loads also ends its progress bar now.

## [0.1.0] - 2026-10-04

The first release with the big-backup work: direct reads from stored 7z
archives, rendered previews, find in a file and a manual. Installers for
Windows, macOS and Linux follow.

### Added

- **Text previews with colour and structure.** JSON, YAML, TOML, HTML/XML and
  Markdown are shown with syntax colours (the colours of VS Code's dark theme).
  CSV and TSV get "rainbow" columns: every column its own colour, with the
  delimiter detected automatically and quoted fields followed across lines.
- **Rendered views with a switch to the source.** HTML and Markdown open
  rendered, CSV opens as a table; a Rendered/Source (Table/Source) switch shows
  the text behind it, and the choice is remembered for the session. HTML is
  shown on a white page, Markdown in Archy's own colours.
- **Pages use their own stylesheets and images from the archive.** A rendered
  HTML or Markdown file reads the CSS and images it refers to from the same
  archive, in memory. Nothing outside the archive is ever loaded: no remote
  images, fonts or scripts, and links do not navigate away.
- **Find in the previewed file.** Ctrl+F (Cmd+F on macOS) with the focus in a
  text preview searches that file: all matches highlighted, Enter / Shift+Enter
  for the next / previous one, Esc to close. Works in the source, the rendered
  page and the CSV table. With the focus elsewhere, Ctrl+F still filters the
  current folder.
- **Draggable video seek bar.** The handle can now be dragged; the time follows
  the handle while dragging. Clicking still jumps.
- **HEIC, HEIF, AVIF and TIFF photos.** iPhone photos (HEIC) preview and get
  thumbnails on every platform: through Qt on macOS, through pi-heif elsewhere
  (pi-heif is the decode-only build of pillow-heif, without the GPL encoder).
  AVIF goes through Pillow.
- **Videos in a stored 7z play straight from the archive.** In a 7z made with
  Store (no compression), encrypted or not, a video starts at once and is read
  while it plays, instead of being loaded into memory first; it no longer has
  to fit in memory at all. PDFs open the same way.
- **Web pages look the way a browser shows them.** HTML is now rendered by
  Chromium (QtWebEngine), so all of CSS works: flexbox, grid, gradients,
  variables, web fonts packed next to the page. It stays offline and in
  memory: an off-the-record profile, the page and its files served from the
  archive only, every other request blocked, no JavaScript, and links do not
  navigate. Find (Ctrl+F) works in it too. Where QtWebEngine is not available,
  Qt's own renderer is used as before.
- **File details under the name.** The line under the file name in the preview
  now shows what is useful for that file instead of repeating the type and size
  from the bottom of the pane: a photo's size in pixels, camera, exposure and
  date taken; a video's length, resolution, frame rate and codec; a PDF's page
  count, title and author; a text file's lines and encoding; a folder's number
  of items. The info window lists them all, including the lens.
- **Mixed 7z archives are pointed out.** When a 7z made with Store also holds
  files in compressed blocks (7-Zip adds dragged files compressed without
  asking), a notice above the file list says how many, because those files open
  much slower. No dialog: it is information only.
- `CHANGELOG.md` (this file).

### Changed

- **Stored 7z archives are read directly.** Archy reads a file from a 7z made
  with Store by seeking to it (also with AES-256, which can be decrypted from
  any 16-byte boundary), instead of going through py7zr's index walk. On a
  100,000-file backup a single read went from about 0.3 s to under a
  millisecond, and a click on a photo no longer waits for the folder that is
  being loaded in the background.
- Video thumbnails from a stored 7z read only the part of the video they need,
  one video at a time.
- Archy needs more disk space once installed: QtWebEngine, which draws the web
  pages, adds about 200 MB.
- The README is now a showcase with screenshots: what Archy can do, how to
  start, and the current version. All the detail moved to the new
  [MANUAL.md](MANUAL.md). The screenshots are made from a synthetic demo
  archive by `scripts/readme_screenshots/take_screenshots.py`.
- The open-source repository is generated from an allowlist
  (`scripts/export_public.py` in the maintainers' repository).

### Fixed

- **Photos are shown upright.** The camera's orientation (EXIF) is now applied
  in the preview and the thumbnails; portrait photos from cameras and phones
  were shown on their side.
- **Video thumbnails are upright.** Portrait clips from phones (stored as
  landscape frames with a rotation flag) got sideways thumbnails.
- Clicking a photo or video in a large encrypted 7z while its folder was still
  loading waited for the whole folder to finish (25 s in a test with 250 large
  photos; now 0.5 s).
- `scripts/check_libarchive.py` crashed on a function that no longer existed.
- The zoom percentage under a photo could fall outside its button in a small
  window (the button kept the width of the previous, shorter value).
- The Compressed and Ratio columns no longer show a misleading size (such as
  "1148%") for files in a solid 7z block, where the compressed size belongs to
  the whole block and not to one file; they are left empty instead.

## [0.0.1] - 2026-10-01

The first public version: the state of Archy when the repository was created.

### Archives

- Reads zip (including ZipCrypto and WinZip AES), 7z (including AES-256 with
  encrypted file names), tar, tar.gz, tar.bz2 and tar.xz on every platform;
  with libarchive installed also rar, iso, cpio, cab, xar and ar.
- Large archives: 7z files with more than 65,536 entries, the index read on a
  background thread with a spinner, and a whole folder decoded in one pass
  with all thumbnails made along the way (fast on solid 7z backups).
- Passwords are asked once per archive and kept in memory only.

### Previews, all in memory

- Images, video (play/pause, 10 s steps, frame-by-frame stepping while paused,
  full screen), PDF, and text and source code with automatic encoding
  detection and colour emoji.
- Four views (list, compact, thumbnails, large thumbnails), a sidebar that
  filters by type, search through the whole archive (Ctrl+Shift+F) and a filter
  for the current folder (Ctrl+F).

### Extracting and editing

- Extract a selection or everything, with a progress window (current file,
  speed, time left), CRC checks, cancel or continue in the background, and
  drag-out to extract where you drop.
- Edit zip and 7z: add files and folders (also by dragging them in), create
  folders, rename, delete and edit text files. Changes are collected and
  written on save as a complete new version next to the original, checked,
  and only then put in its place. A 7z is saved as a stream on a background
  thread and keeps its password, encrypted names, Store or compression,
  non-solid blocks and file times.
- An edit lock with a tooltip that says why: encrypted archives start locked;
  an encrypted 7z made with Store can be unlocked on purpose.

### Privacy and memory

- Nothing is written to disk except when extracting or saving. An optional
  temp-files mode (off by default) takes over for folders or videos that do
  not fit in memory, always encrypted with AES-256 and a key that exists only
  in memory for that session.
- A memory budget of its own (a share of RAM with a reserve for the system),
  with every buffer adjustable in Settings > Advanced.

### Interface

- The "Soft Depth" dark theme with adjustable accent colours; settings for
  language, start view, density, preview fit, temp files and memory.
- English and Dutch, following the system language; another language is one
  JSON file (see TRANSLATING.md).
- Opens archives from the command line, "Open with" (Windows, Linux) and the
  Dock (macOS).

[Unreleased]: https://gitlab.com/bytefulminds/archy/-/compare/v0.1.0...main
[0.1.0]: https://gitlab.com/bytefulminds/archy/-/compare/v0.0.1...v0.1.0
[0.0.1]: https://gitlab.com/bytefulminds/archy/-/tags/v0.0.1
