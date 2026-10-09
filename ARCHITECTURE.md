# Archy - architecture

A guide for developers new to the codebase. It explains how the app is put
together, the rules that keep it fast and safe, and where to find things. Read
this once before making changes; it will save you time.

## What the app is

Archy is a desktop app (PySide6 / Qt 6) that browses archive files - zip, 7z,
tar.*, and read-only rar and others - **without extracting them to disk**. You
open an archive, walk its folders, preview files, and (for zip and 7z) add,
rename and delete entries, then save. The code is in English; the UI is
translated (English and Dutch bundled, see "Translations" below).

## The one rule that shapes everything: stay in memory

Browsing and previewing **never write to disk**. An entry's bytes are
decompressed straight into memory and shown from there. Disk is written in only
two places, both explicit and obvious:

1. **Extraction** - the user asks to extract entries to a folder.
2. **Saving an edited archive** - rewrites the archive file itself via a temp
   file plus an atomic replace.

If you are about to write a temporary file to preview or browse something, stop:
that breaks the core promise. The one sanctioned exception is the optional
temp-files mode (see below), which the user turns on explicitly in Settings, and
which only ever writes ENCRYPTED data (`core/secure_temp.py`) - never a raw file
and never a file name.

## The three layers

```
src/archy/
  core/       archive logic - NO Qt allowed here
  services/   helpers that turn bytes/values into things the UI needs
  ui/         everything PySide6 (windows, widgets, workers)
  app.py      wires it together and starts Qt
  __main__.py `python -m archy`
```

The **golden boundary**: `core/` must never `import PySide6`. That keeps the
archive logic pure and unit-testable without a display (the whole `tests/` suite
runs headless against `core/` and the pure parts of `services/`). If you find
yourself wanting Qt in `core/`, the logic belongs in `services/` or `ui/`.

`archy/about.py` (Qt-free) holds what Settings > About shows: the maker, the
third-party components with their licenses, and the notices text built from
`resources/licenses/` (package license files refreshed by
`scripts/collect_licenses.py`).

### core/ - archive logic (no Qt)

- `backend.py` - the heart. Defines `ArchiveBackend`, the abstract base every
  format backend implements, plus the data classes `ArchiveEntry` /
  `ArchiveInfo` and all the in-memory caching (see performance, below).
- `backends/` - the concrete backends:
  - `zip_backend.py` (`ZipBackend`) - read + write for zip (stdlib `zipfile`;
    `pyzipper` for AES).
  - `sevenzip_backend.py` (`SevenZipBackend`) - read + write for 7z (`py7zr`).
  - `sevenzip_direct.py` - direct reads for 7z files made with Store (no
    compression, encrypted or not); see "Stored 7z: direct reads" below.
  - `sevenzip_solid.py` - Archy's own reader for compressed solid blocks
    (LZMA2/LZMA, encrypted or not); see "Compressed solid 7z" below.
  - `sevenzip_writer.py` - the 7z save: a streaming rewrite next to the
    original, checked before it replaces the original (see "Saving a 7z").
  - `tar_backend.py` (`TarBackend`) - read-only tar/tar.gz/tar.bz2/tar.xz via
    the stdlib `tarfile` (no native dependency).
  - `libarchive_backend.py` (`LibArchiveBackend`) - broad **read-only** access to
    rar and exotic formats via libarchive. libarchive is **optional**: when it is
    not available (e.g. Windows without the DLL) those formats report
    UnsupportedFormatError and zip/7z/tar keep working.
  - `nested_backend.py` (`NestedBackend`) - an archive inside an archive,
    read-only from its bytes in memory (zip via pyzipper, 7z via py7zr, tar via
    tarfile, others via libarchive's memory reader). Experimental; never
    written to disk. The window side is `ui/main_window/nested.py`.
- `factory.py` - `backend_for(path)` picks the right backend for a file.
- `detect.py` - format detection by magic bytes + extension.
- `tree.py` - turns a flat entry list into a `TreeNode` folder tree.
- `extraction.py` - streams entries to disk safely (zip-slip guard, block by
  block, never holding a whole entry in memory). Reused by the temp-files
  fallback.
- `errors.py`, `native.py` - error types and libarchive native-library setup.

### The backend contract (read this before touching backends)

- `read_bytes(entry)` returns one entry's decompressed content **in memory** and
  never writes to disk.
- Edits are **staged, not applied**. `add` / `write_bytes` / `remove` / `rename`
  / `add_dir` record the change in `self._pending` (a dict: arcname -> new bytes,
  or `None` for a delete). Nothing on disk changes until `save()`, which writes a
  temp file and atomically replaces the original.
- `list_effective_entries()` returns the on-disk entries **with staged edits
  overlaid**, and `read_bytes` serves staged content first. This is why the UI
  shows unsaved changes immediately. `discard_changes()` throws the staging away.
- Read-only backends (libarchive) leave `writable = False` and raise
  `ReadOnlyArchiveError` from the write methods.

### services/ - stateless helpers

- `formatting.py` - human sizes, dates, ratios.
- `preview.py` - `PreviewKind` (TEXT / IMAGE / PDF / VIDEO / AUDIO /
  UNSUPPORTED) and `kind_for_name` (by extension). Music uses the video
  player without a picture (`providers._AudioStage`: cover art, title, artist).
- `providers.py` - builds the actual Qt preview widget from bytes
  (`build_preview_widget`) or from a temp-file path
  (`build_preview_widget_from_path`, used by the memory-pressure fallback). This
  one file does import Qt - it is the bridge into the UI.
- `text_decode.py` - decode bytes to text with charset fallback.
- `text_view.py` - the text preview: syntax colours (JSON, YAML, TOML,
  HTML/XML, Markdown; VS Code's dark colours) and rainbow CSV through
  `QSyntaxHighlighter` (skipped above `HIGHLIGHT_LIMIT` characters), and for
  HTML/Markdown a rendered page (`OfflineBrowser`: loads stylesheets and
  images only from the same archive through a resource reader, follows no
  links) and for CSV a table (`CsvModel`), each with a Rendered/Source switch,
  plus a `FindBar` for Ctrl+F in the file (`TextPreview.open_find`; the
  window routes Ctrl+F there when the focus is inside the preview). Linked
  stylesheets are inlined first: Qt only loads a `<link>` that says
  `type="text/css"`. HTML goes to `web_view.py` instead when QtWebEngine is
  available.
- `web_view.py` - HTML through Chromium (QtWebEngine) for full CSS. One
  shared off-the-record profile (memory cache, no persistent cookies, so
  nothing of the page lands on disk); the page and its resources come from the
  private `archy-page://<token>/<path>` scheme, served from memory by a
  `QWebEngineUrlSchemeHandler` through the same archive-only resource reader;
  a `QWebEngineUrlRequestInterceptor` blocks every other scheme; JavaScript,
  plugins, the PDF viewer and WebGL are off, link navigation and new windows
  are refused, downloads cancelled. `prepare()` must run before the
  QApplication exists (it registers the scheme and shares OpenGL contexts;
  `app.run()` and the screenshot script call it); without it `available()` is
  False and `text_view` keeps using `OfflineBrowser`. `WebFindTarget` makes the
  `FindBar` work on it (Chromium searches asynchronously and reports the match
  count back). Set `ARCHY_NO_WEBENGINE=1` to force the fallback. The GPU
  driver may keep a shader cache (compiled shaders, no file content) as it
  does for every OpenGL app.
- `file_details.py` - the line under the file name in the preview: pixels,
  camera, lens, exposure and date taken (EXIF via Pillow / pi-heif), video
  length and resolution, PDF pages/title/author, text lines and encoding. Each
  detail is (i18n label key, value). Providers attach a `DetailsSource`
  (`providers.details_of`) to their widget; it can fill in later (a video's
  length once the player has opened it) and the preview pane follows it.
- `image_decode.py` - decode image bytes for the preview and thumbnails:
  Qt's `QImageReader` first (orientation applied, scaled JPEG decoding, GIL
  released), then pi-heif (decode-only pillow-heif) / Pillow for HEIC and AVIF where Qt has no
  plugin (Qt reads HEIC only on macOS). A `name` routes camera RAW and PSD
  to `raw_decode.py` first (Qt would read a RAW's tiny TIFF thumbnail).
- `doc_thumbs.py` - thumbnails of documents, made on the thumbnail workers:
  the top of a PDF's first page (QtPdf), HTML/Markdown drawn with a
  QTextDocument that loads nothing the page refers to, and the first lines of
  a text file. Only the first 16 KB of a text file is read.
- `office.py` - Word, Excel and PowerPoint previews, Qt-free: .docx to an
  HTML page (mammoth), .pptx to one HTML card per slide with its text boxes
  and pictures positioned in percent and font sizes in container units
  (python-pptx), .xlsx to the values of each sheet (openpyxl, read-only,
  `data_only`: the values Excel last calculated, capped at 5,000 rows by 200
  columns). Pictures become resources of the page ("media/..."), served by the
  web view like the files next to an HTML page; EMF/WMF are left out.
  `embedded_thumbnail` reads the docProps/thumbnail picture Office may save;
  `doc_thumbs.office_thumbnail` falls back to a small page of the text.
  `providers` shows pages in the web view (or `OfflineBrowser`) and sheets as
  tables in a `QTabWidget`. Macros are never looked at; password-protected
  Office files (an OLE container, not a zip) are reported as such.
- `raw_decode.py` - camera RAW: the file is scanned for embedded JPEG streams,
  their size read from the JPEG frame header, and the largest one Qt decodes
  is used (lossless SOF3 sensor data is skipped), turned upright by the RAW's
  own orientation (TIFF IFD0, or the CR3 CMT1 box) when the JPEG has none.
  PSD: Pillow's composite image, else the JPEG thumbnail in image resource
  1036/1033. `file_details` reads RAW EXIF from Pillow, the CR3 CMT boxes or
  the embedded JPEG.
- `temp_files.py` - `TempManager`: temp-files mode, the per-session encrypted
  store and its cleanup.
- `encrypted_device.py` - `EncryptedBlobDevice`, a decrypting QIODevice so Qt
  plays/reads encrypted temp files without a raw copy.

### ui/ - the Qt layer

- `main_window/` - the main window, as a package:
  - `__init__.py` - the `MainWindow` class (the app's hub).
  - `constants.py` - tunables and fixed data (size limits, category list, labels).
  - `chrome.py` - small frameless-window helpers (`FullscreenWindow`, `DragBar`,
    `ResizeGrip`) that take callbacks and hold no window state.
- `theme/` - the Soft Depth look, as a package:
  - `palette.py` - colours (fixed tokens + the runtime accent) and `set_accent`.
  - `icons.py` - the inline-SVG icon set and `symbol_icon`.
  - `stylesheet.py` - the big QSS builder for all the chrome.
  - `fonts.py` - loads the three bundled fonts.
  - Use it as `from archy.ui import theme` then `theme.ACCENT`,
    `theme.symbol_icon(...)`, `theme.stylesheet()`. Colour tokens are served live
    (see Theme, below).
- `archive_model.py` - the Qt model/proxy that feed the table and grids from the
  `TreeNode` of the current folder.
- `grid_delegate.py` - custom painting for the thumbnail tiles.
- `image_view.py` - the zoom/pan image viewer.
- `settings.py` (`Settings`, a typed `QSettings` wrapper) and
  `settings_dialog.py` (the Soft Depth settings screen).
- `widgets.py` - reusable custom controls (`SoftComboBox`, `SoftToggle`).
- `soft_dialog.py` - the frameless themed dialog base + confirm/prompt/message
  helpers.
- The background workers (see Threading): `preview_loader.py`,
  `thumbnail_loader.py`, `folder_prefetcher.py`, `extract_worker.py`.

## Performance model (why it feels instant on huge archives)

All of this lives in `core/backend.py`:

- **LRU read cache** - recently read entries are kept in a bounded in-memory
  cache (sized from free RAM) so paging back and forth is instant. Very large
  entries (e.g. video) are not cached, so one big file can't evict everything.
- **Whole-folder prefetch** - the first time you open a file in a folder, the
  backend decodes that folder's files in one pass into a buffer. On a "solid" 7z
  (where each read otherwise decodes from the block start) this turns N slow
  reads into one. It is bounded by a RAM budget and guarded by a **generation
  counter** so a prefetch that finishes after you have navigated away is thrown
  away instead of serving stale bytes.
- **`fast_seek`** - true for zip (cheap random access), false for 7z/libarchive
  (a read may decode a big block or must stream). The UI uses this to decide
  whether a small read may run on the UI thread or must go to a worker, and
  whether thumbnails are read per file (zip) or made by the folder pass. A 7z
  made with Store keeps the folder pass on purpose: one thread reading the
  folder front to back makes the thumbnails faster than many threads reading
  files at once (measured), and a click reads directly anyway (see below).
- **`open_stream(entry)` / `can_stream(entry)`** - a seekable reader over one
  entry, read from the archive on demand, where the format allows it (stored
  7z; None elsewhere). Videos and PDFs then play/open at once through a
  `ReaderDevice` (`services/encrypted_device.py`) instead of being loaded whole
  first, and video thumbnails read only the parts PyAV needs (one at a time).
- **`mixed_storage()`** - (stored files, compressed files) when an archive
  mixes the two (a Store 7z with files later added compressed, typically by
  7-Zip's drag and drop); None otherwise. No I/O: the 7z backend counts it
  while building the `StoreIndex`. The window shows a notice above the file
  list (`_update_archive_notice`), never a dialog.

### Stored 7z: direct reads (`core/backends/sevenzip_direct.py`)

py7zr reads an entry by walking the whole index (every file, for every read)
and decoding its block from the start. A block made with Store needs neither:
its bytes sit in the archive as they are, at an offset the index gives, and
7-Zip's AES-256 is CBC, which decrypts from any 16-byte boundary given the 16
encrypted bytes before it. `StoreIndex.build()` turns py7zr's parsed header into
a span per file (block start, offset in the block, size, CRC, AES properties)
for every block made of Copy/7zAES coders only; `read_whole` (CRC-checked),
`read_span` (any range) and `SpanStream` (a seekable file) read from there with
their own file handle. So these reads skip `_io_lock` (`_reads_freely`): a
click never waits for a folder pass. The folder pass itself reads stored files
the same way, in archive order. Decryption works in place in one buffer: every
extra copy holds the GIL, which the UI thread needs. A partial read or a stream has no CRC to
catch a wrong password, so the password is first proved once on the smallest
encrypted file. Compressed blocks go through `sevenzip_solid.py` (below) or
py7zr.

### Compressed solid 7z (`core/backends/sevenzip_solid.py`)

In a compressed solid block every file sits in one LZMA2/LZMA stream, and py7zr
decodes (and CRC-checks) everything before a file to reach it, from the block
start, for every read. For blocks made of one LZMA2 or LZMA coder, optionally
behind 7zAES (`SolidIndex.build()`; others such as BCJ, PPMd or BZip2 keep
going through py7zr), `SolidReader` decodes with the stdlib `lzma` module in raw
mode and avoids most of that work, on archives as they are:

- **Dictionary resets.** 7-Zip compressing with several threads splits an LZMA2
  stream into pieces that each start with an empty dictionary (every 4x the
  dictionary size). Decoding can start at any of them. `_Resets` finds them by
  walking the LZMA2 chunk headers (a few bytes every 64 KB, nothing decoded;
  with AES each header is decrypted on its own, CBC allows that) up to the
  offset a read needs, and keeps them for the session. A stream with no reset
  after `max(4x dictionary, 256 MB)` was made by one thread: the walk stops.
- **Parked decoders.** After a read the decoder stays where it stopped (up to
  three, within a memory budget for their dictionaries), so the next folder
  further on continues from there. A file more than 64 MB past a decoder is
  read by another one, so a block sorted by file type (all photos, then all
  text files) gets a decoder per kind that only moves forward.
- For every read the reader picks the closest start at or before the file: a
  parked decoder, a reset point, or the block start. Bytes decoded only to get
  past them are not CRC-checked; every file handed over is.

Reads still run under `_io_lock`. `group_progress()` then reports decoded bytes
(from the chosen start to the last wanted file). A read that fails in an
unexpected way (not a wrong password) falls back to py7zr.

Any change here must preserve these properties: do not add per-file disk writes,
do not read whole large entries into RAM on the UI thread, and keep the
generation/`request_id` cancellation intact.

## Threading model (keep the UI thread free)

Anything that could block - decoding a big entry, generating a thumbnail,
extracting - runs off the UI thread on a `QThreadPool`, and results come back to
the GUI thread via Qt signals. Each request carries an **id** (or the prefetch's
**generation**); when the user moves on, the id changes and stale results are
ignored. The workers:

- `preview_loader.py` - reads the selected entry's bytes (or, for the fallback,
  streams it to a temp file) off-thread; emits `loaded` / `spilled` / `failed`.
- `thumbnail_loader.py` - decodes grid thumbnails.
- `folder_prefetcher.py` - drives the whole-folder prefetch above.
- `extract_worker.py` - runs extraction with progress + cancel.
- `open_worker.py` - reads an archive's index (and builds the tree) when it is
  opened; a spinner (`busy_overlay.py`) shows meanwhile.
- `save_worker.py` - saves a 7z (a full streaming rewrite) with progress +
  cancel.

If you add a slow operation, follow this pattern (worker + request id + queued
signal) rather than doing it inline.

## Memory policy (`core/memory.py`)

Every in-RAM budget (folder buffer, read cache, thumbnail cache, video preview,
video thumbnails from solid archives) comes from one live policy: a RESERVE that
always stays free for the OS (default max(2 GB, 20% of RAM)), a CAP on Archy's
own use (default 75% of RAM, measured as process RSS), and "spare" = the smaller
headroom, measured at the moment of use. Callers that hold several copies of the
same bytes pass `copies=`. Each buffer can be Automatic or fixed via Settings >
Advanced (`memory.Limits`, applied live by `_apply_settings`). This exists
because independent "share of free RAM" budgets once added up past 100% and
could take Windows down (black screen).

## Temp-files modes (Settings > Temporary files)

- `memory` (default): nothing is written; too-large previews are refused.
- `overflow` ("To disk when memory is tight"): RAM first; what does not fit (folder files
  beyond the budget, a video/PDF too big for RAM) goes to the encrypted store.
- `disk` ("Always use temporary files"): the folder pass keeps its files in the
  encrypted store instead of RAM, and videos/PDFs always play from it.

Everything on disk goes through `EncryptedTempStore` (`core/secure_temp.py`):
AES-256-CTR, a random key that only exists in process memory, a random nonce and
random file name per blob; CTR gives random access for seeking. Large entries
are streamed in with `ArchiveBackend.stream_entry` (never whole in RAM). Qt
reads them back through `EncryptedBlobDevice` (`services/encrypted_device.py`),
a seekable QIODevice that decrypts on demand (video player, PDF viewer).
`TempManager` (`services/temp_files.py`) owns the per-session store, the mode,
and cleanup (on exit if enabled, "Nu opruimen", and leftovers of earlier
sessions, which are unreadable anyway because their key is gone).

## Theme and the runtime accent

The accent colours are user-configurable at runtime. `theme.set_accent(primary,
secondary)` recomputes the accent tokens (hover/pressed/on-text/selection tint
are derived from the primary) and `theme.refresh_app()` re-applies the palette +
stylesheet so the whole app re-colours live. For this to work, colour tokens are
read **live**: `theme/__init__.py` serves them through `__getattr__` from
`palette`, so `theme.ACCENT` always reflects the latest `set_accent`. Do not
`from archy.ui.theme import ACCENT` - that copies a stale value and breaks
live re-theming; always read `theme.ACCENT` (or import the `palette` module and
read `palette.ACCENT`).

## Encryption / password flow

The session password is held in memory only (never written to disk). When a
read hits an encrypted entry, the window prompts for a password, stores it on
the backend for the session, and retries.

Encrypted archives open **locked** (read-only). `ui/main_window/locking.py`
shows a lock next to the archive name whenever an archive is read-only, with a
tooltip that explains why:

- not writable at all (rar, tar, ...): a fixed lock;
- an encrypted zip: a lock the user can open (the dialog explains that new and
  changed files are encrypted with AES-256, which the zip tools built into
  Windows and macOS cannot open). A zip save is always cheap, see below;
- an encrypted, compressed 7z: a lock the user can open when its content is
  at most `locking.RECOMPRESS_LIMIT` (1 GB; py7zr compresses at about 2 MB/s,
  the dialog gives the estimate), a fixed lock above that (a rewrite would
  recompress everything, hours on a big archive);
- an encrypted 7z made with Store: a lock the user can open on purpose. A dialog
  explains what saving will do (new version next to the original, same
  password and encrypted names, free space needed); after that `_edit_unlocked`
  allows editing for this session. Reopening the archive locks it again.

`_is_locked()` gates all editing while an encrypted archive is locked. What
kind of 7z it is comes from `SevenZipBackend.edit_profile()` (Store or
compressed, encrypted names, solid), probed once on the worker thread that
opens the archive.

## Saving a zip (`core/backends/zip_backend.py`)

`ZipBackend.save()` writes a new archive next to the original (`.zxtmp`),
checks it and swaps it in with `os.replace`, on the save worker with progress
and cancel (`save_plan()` makes the window use it). Entries that did not
change are copied raw by `_copy_raw`: local header (with the new name for a
rename), data and data descriptor, byte for byte, then appended to the
writer's central directory as a copied ZipInfo. So they stay compressed and,
in an encrypted zip, encrypted with their own method; a save costs a copy and
never needs the password for them. Renames are a name map (`_moved`), so a
renamed file is never read. New and changed files are written with pyzipper:
Deflate, and with the session password AES-256 (folder entries are left
unencrypted). The check reads the new central directory, opens every copied
entry (which verifies its local header, and with AES the password check
value) and reads back every written one.

## Saving a 7z (`core/backends/sevenzip_writer.py`)

7z has no in-place edit, so a save writes a complete new archive next to the
original (a hidden `.name.*.archy-save` file in the same folder) and replaces the
original with `os.replace` only after `verify()` re-opened it and checked the
names, sizes, method, header encryption and a sample of CRCs. Cancel or any
error removes the new file; the original is never touched before the replace.

- The kept files stream from the original in one sequential decode on a
  producer thread, through small bounded pipes, into the writer: memory stays
  at a few MB of data plus py7zr's index structures (about 10 KB per file).
  Nothing is ever written unencrypted.
- The new archive keeps the original's character: password, encrypted file
  names, Store (Copy + 7zAES) or compression, one block per file when the
  original was non-solid (py7zr itself always writes one solid block; `_Writer`
  starts a new block per file), and file times and attributes.
- Renames are staged as a name map (`_moved`: new name -> name in the archive),
  not as bytes, so renaming a folder of large files reads nothing; the data is
  copied under the new name during the save.
- `save_plan()` reports bytes, files and the free space needed; the UI checks
  the free space first and runs the save on `ui/save_worker.py` with a progress
  window (`SaveProgressDialog`) that only appears when the save takes longer
  than a moment.

## Translations (`archy/i18n.py`)

Every text the user sees goes through `tr("key", name=...)`. The texts live in
JSON catalogs: `resources/i18n/en.json` (reference and fallback) and `nl.json`,
plus any file a user drops in their language folder (`i18n.user_language_dir()`).
A text with plural forms is an object (`{"one": ..., "other": ...}`, or
`one/few/many` for Polish and Russian; `plural_category()` has the CLDR rules).
Integers passed to `tr()` are shown with the language's digit grouping, and
`format_size` uses its decimal separator. The language is set once in `app.py`
before any widget is built (Settings: Automatic = the system language when
there is a catalog for it, else English); changing it applies after a restart,
so widgets never need to re-translate themselves. `i18n` is Qt-free, so core
error messages can use it too. `tests/test_i18n.py` fails on a key that the code
uses but `en.json` lacks, and on placeholders that differ between languages. How
to add a language: `TRANSLATING.md`.

## Settings

`Settings` (`ui/settings.py`) is a typed wrapper over `QSettings` (persisted by
Qt in the per-user native location; org/app names are set in `app.py`). Every
key has a default, so a fresh install behaves sensibly. The settings screen
(`ui/settings_dialog.py`) reads and writes it and the window re-applies changes
live.

## Tests and checks

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src python -m pytest -q   # all tests
ruff check .            # lint
ruff format --check .   # formatting
```

`tests/` is mostly `core/` + `services/` (headless). `tests/test_ui_smoke.py`
boots the window offscreen and checks a live accent change - it is the safety net
for UI-layer refactors, so run it after any change under `ui/`.

## The MainWindow package (how the window is organised)

`MainWindow` is deliberately small: `main_window/__init__.py` holds only the
class declaration and `__init__`, which wires everything together. All of its
methods live in topic **mixins**, one file per concern, that `MainWindow`
inherits from. This keeps each area easy to find and read without a
2000-line file:

- `header.py` (`HeaderMixin`) - the custom title bar / header row.
- `sidebar.py` (`SidebarMixin`) - the category sidebar and its summary.
- `file_panel.py` (`FilePanelMixin`) - the file list (table / compact / grid)
  and the bottom status strip.
- `preview.py` (`PreviewMixin`) - the preview pane: media chrome, loading
  states, displaying each file kind, and the full-screen slideshow (a timer
  that steps to the next photo, wrapping; Space in `FullscreenWindow` toggles
  it).
- `actions.py` (`ActionsMixin`) - the `QAction`s and keyboard shortcuts.
- `opening.py` (`OpeningMixin`) - opening/closing archives and the password flow.
  The index is read by `ui/open_worker.py` on a worker thread while
  `ui/busy_overlay.py` shows a spinner over the workspace.
- `navigation.py` (`NavigationMixin`) - folder navigation and the breadcrumb:
  back/forward history (folder paths by name, so a reload keeps it; mouse
  buttons through `MouseNavFilter` on the views' viewports), "/" separators
  that drop down the sibling folders, and the crumbs of outer archives.
- `nested.py` (`NestedMixin`) - archives inside an archive: the outer
  archive's state is kept as a `_Level` while the inner one is shown; going
  back restores it and closes the inner backend.
- `selection.py` (`SelectionMixin`) - selection state and double-click handling.
- `search.py` (`SearchMixin`) - in-folder and whole-archive search.
- `editing.py` (`EditingMixin`) - add / delete / rename and drag-drop.
- `locking.py` (`LockMixin`) - the edit lock (tooltip, unlock dialog) and saving
  (a 7z in the background with progress, a zip directly).
- `extract.py` (`ExtractMixin`) - extraction and prefetch progress.
- `apply_settings.py` (`SettingsMixin`) - opening the settings screen and
  applying changes live.
- `window_frame.py` (`WindowFrameMixin`) - frameless-window chrome: resize grips,
  responsive layout, and the `resizeEvent` / `closeEvent` overrides.
- `chrome.py` and `constants.py` - the frameless-window helper widgets
  (`DragBar`, `ResizeGrip`, `FullscreenWindow`) and the module constants.

A mixin is a plain class that only defines methods; it never runs on its own.
It relies on the attributes `MainWindow.__init__` sets (`self._backend`,
`self._model`, ...) and on methods from the other mixins, all resolved through
`self` on the combined class. The base order in the class declaration lists the
mixins first and `QMainWindow` last, so the mixins' methods take precedence and
each Qt override (`eventFilter` in preview, `dragEnterEvent`/`dropEvent` in
editing, `resizeEvent`/`closeEvent` in window frame) overrides `QMainWindow`.

When adding a feature, put its methods in the mixin for that concern (or add a
new mixin and list it in the class bases), wire any new signals in
`MainWindow.__init__`, and keep each Qt override defined in exactly one mixin.
Run `tests/test_ui_smoke.py` afterwards - it constructs the whole window, so it
catches a mis-wired mixin immediately.
