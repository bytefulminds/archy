<div align="center">

<img src="docs/archy-logo.png" alt="Archy logo" width="128">

# Archy

**Look inside your archives like a normal folder: browse, preview and take out
what you need, without unpacking anything.**

**Version 0.1.0** · installers for Windows, macOS and Linux are on their way

[![pipeline](https://gitlab.com/bytefulminds/archy/badges/main/pipeline.svg)](https://gitlab.com/bytefulminds/archy/-/pipelines)
![license: MIT](https://img.shields.io/badge/license-MIT-45C0CD)
![platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux-CD7E45)

</div>

<p align="center">
  <img src="docs/screenshots/browse-thumbnails.png" alt="Archy showing a folder of photos inside a 7z archive as thumbnails, with one photo open in the preview pane" width="900">
</p>

Archy opens zip, 7z, tar and other archives and shows what is inside as if it
were a folder on your computer. See every photo as a thumbnail, play a video,
read a document, and copy out only the files you need. Nothing you look at is
unpacked to your disk.

It was made for big, password-protected backups, such as a 50 GB archive with a
hundred thousand photos, where "unpack everything first" is not an option.

## What you can do with Archy

### Photos and videos, without unpacking

Every photo in a folder becomes a thumbnail, including iPhone HEIC, and always
upright, and camera RAW and Photoshop files get previews too. Click one to see
its size in pixels, the camera and its settings, or start a slideshow.
Videos play right away, with a seek bar you can drag and frame-by-frame
stepping. Search the whole archive by name and jump straight to what you find.

<p align="center">
  <img src="docs/screenshots/preview-video.png" alt="A video from the archive playing in the preview pane, with play controls and a seek bar" width="49%">
  <img src="docs/screenshots/search-archive.png" alt="Search results for a folder name across the whole archive, with a photo in the preview" width="49%">
</p>

### Documents the way they are meant to look

Web pages look the way a browser shows them, with all their CSS and the
images and fonts next to them in the archive. Markdown is shown formatted, and
one click shows the text behind either. PDFs open in the preview too, with
their page count and author.

<p align="center">
  <img src="docs/screenshots/preview-markdown.png" alt="A Markdown travel plan shown formatted, with a table and a list" width="49%">
  <img src="docs/screenshots/preview-html.png" alt="A web page from the archive rendered with its own stylesheet and banner image" width="49%">
</p>

### Data at a glance

CSV files open as a table with every column in its own colour. JSON, YAML, TOML
and XML are shown in the colours you know from code editors.

<p align="center">
  <img src="docs/screenshots/preview-csv.png" alt="A CSV file of travel expenses shown as a table with coloured columns" width="49%">
  <img src="docs/screenshots/preview-json.png" alt="A JSON file with keys, text, numbers and true/false in different colours" width="49%">
</p>

### Find a word, take out what you need

Press Ctrl+F in any text file to find a word in it. Extract a few files, a
folder or everything, with a clear progress window, or simply drag files out.

<p align="center">
  <img src="docs/screenshots/find-in-file.png" alt="Find in a Python file, with every match highlighted and a counter" width="49%">
  <img src="docs/screenshots/extract-dialog.png" alt="The extraction window showing the current file, a progress bar, speed and time left" width="49%">
</p>

### Safe changes, private by design

Add, rename and delete files in zip and 7z archives; your original is only
replaced after the new version has been written and checked. Encrypted
archives start locked so nothing changes by accident. Previews are made in
memory, passwords are never stored, and nothing is loaded from the internet.

<p align="center">
  <img src="docs/screenshots/unlock-dialog.png" alt="The dialog that explains what unlocking an encrypted backup for editing means" width="49%">
  <img src="docs/screenshots/settings-temp-files.png" alt="The temporary files settings, with the work mode and cleanup options" width="49%">
</p>

And more: music plays in the preview, you go back and forward through folders,
archives inside an archive open too (experimental), archives with more than
100,000 files open without freezing, a 7z backup that is only partly stored
without compression is pointed out (so you know why some files open slower),
zip, 7z, tar and (with an extra library) rar are supported, and Archy speaks
English and Dutch, with room for more languages.

## Getting started

Installers for Windows, macOS and Linux are on their way. Until then you run
Archy with Python 3.11 or newer:

```bash
git clone https://gitlab.com/bytefulminds/archy.git
cd archy
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
archy                              # or: python -m archy
```

Open an archive from the window (**Ctrl+O**, or drop it on the window), or
straight from the command line: `archy ~/Backups/photos.7z`.

zip, 7z and tar work right away on every platform. For rar and a few other
formats, see [Rar and other formats](MANUAL.md#rar-and-other-formats) in the
manual.

## Learn more

- **[MANUAL.md](MANUAL.md):** everything Archy can do, keyboard shortcuts,
  privacy and temporary files, settings, and tips for large 7z backups.
- **[CHANGELOG.md](CHANGELOG.md):** what changed in each version.
- **[TRANSLATING.md](TRANSLATING.md):** add a language.
- **[ARCHITECTURE.md](ARCHITECTURE.md):** how the code works, for developers
  (see also [For developers](MANUAL.md#for-developers)).

## License

MIT. See [LICENSE](LICENSE).
