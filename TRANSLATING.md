# Translating Archy

Archy's interface text lives in JSON files, one per language. Adding a language
is adding one file: no code changes and no rebuild.

## Where the files are

- **Bundled with Archy:** `src/archy/resources/i18n/<code>.json`. English
  (`en.json`) is the reference and the fallback; Dutch (`nl.json`) is complete.
- **Your own, without rebuilding Archy:** drop the file in the user language
  folder. Settings > General shows the exact path; it is
  - Windows: `%APPDATA%\Archy\languages\`
  - macOS: `~/Library/Application Support/Archy/languages/`
  - Linux: `~/.config/archy/languages/`

  A file there with the code of a bundled language (for example `nl.json` with a
  few texts) overrides just those texts.

Restart Archy and pick the language under **Settings > General > Language**.
*Automatic* takes the first language from your system's preferred-languages
list that Archy has (macOS: System Settings > General > Language & Region),
and English otherwise.

## Making a new language

1. Copy `src/archy/resources/i18n/en.json` to `<code>.json`, using the
   two-letter ISO 639-1 code: `de.json`, `pl.json`, `ru.json`, `fr.json`, ...
2. Fill in `_meta`:

   ```json
   "_meta": {
     "code": "de",
     "name": "Deutsch",
     "english_name": "German",
     "decimal": ",",
     "group": ".",
     "status": "draft"
   }
   ```

   `name` is shown in the language list, so write it in the language itself.
   `decimal` and `group` are the decimal and thousands separators (`1.234,5`).
   Use `"status": "draft"` until a native speaker has checked it.
3. Translate the values under `strings`. Leave the keys alone.
4. For a language bundled with Archy, also add its code to
   `CFBundleLocalizations` in `packaging/archy.spec`, so the macOS app is marked
   as available in it.

Anything you leave out falls back to English, so a half-finished file works.

## Rules for the texts

- **Keep the placeholders.** `{name}`, `{count}`, `{size}`, ... are filled in by
  Archy; keep them (you may move them). `{s:02d}` means "two digits".
- **Plural forms.** A text that depends on a number is an object with one entry
  per form. English and Dutch use `one` and `other`:

  ```json
  "count.items": {"one": "{count} item", "other": "{count} items"}
  ```

  Other languages need other forms:

  | Language | Forms | Example |
  | --- | --- | --- |
  | German, French, Italian, Spanish, Swedish, ... | `one`, `other` | 1 Datei, 2 Dateien |
  | Polish | `one`, `few`, `many` | 1 plik, 2 pliki, 5 plików |
  | Russian, Ukrainian | `one`, `few`, `many` | 1 файл, 2 файла, 5 файлов |
  | Czech, Slovak | `one`, `few`, `other` | 1 soubor, 2 soubory, 5 souborů |
  | Japanese, Chinese, Korean | `other` | 5 ファイル |

  The rules are in `plural_category()` in `src/archy/i18n.py` (CLDR). A missing
  form falls back to `other`.
- **A few texts contain HTML** (`lock.dialog_html`): keep the tags, translate
  the words between them.
- **Line breaks** are written as `\n`.
- Texts are shown in a desktop window: keep labels and buttons short.

## Checking a translation

```bash
pytest tests/test_i18n.py
```

checks that every bundled language has the same keys and placeholders as
English. For a new bundled language, add its code to the list in
`test_bundled_translation_is_complete_and_consistent`.

## For developers

Every text the user sees goes through `tr()`:

```python
from archy.i18n import tr

label.setText(tr("status.selected_one", name=node.name))
self._set_status(tr("edit.files_added", count=added))
```

Add the key to `en.json` and `nl.json` (the test fails on a key that is used
but missing). Integers passed to `tr()` get the language's digit grouping.
