"""What Settings > About shows: who makes Archy, and the third-party software
it is built on with the licenses that require a mention. Qt-free.

The license texts live in resources/licenses/: the standard texts (LGPL, GPL,
the font license) and, in packages/, the license files of the Python
packages (refreshed with scripts/collect_licenses.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from archy import __version__

MAKER = "Bytefulminds"
WEBSITE = "https://www.bytefulminds.com"
WEBSITE_LABEL = "www.bytefulminds.com"
SOURCE = "https://gitlab.com/bytefulminds/archy"

LICENSES = Path(__file__).resolve().parent / "resources" / "licenses"


@dataclass(frozen=True)
class Component:
    name: str
    license: str
    url: str
    files: tuple[str, ...] = ()  # in resources/licenses/
    note: str = ""


COMPONENTS: tuple[Component, ...] = (
    Component(
        "Qt 6 and Qt for Python (PySide6)",
        "LGPL-3.0",
        "https://www.qt.io",
        ("LGPL-3.0.txt", "GPL-3.0.txt"),
        "Used as unmodified, dynamically linked libraries; you may replace them with "
        "your own build of the same version. Source code: https://download.qt.io and "
        "https://code.qt.io. Qt WebEngine includes Chromium and Qt Multimedia includes "
        "FFmpeg, each with their own licenses: "
        "https://doc.qt.io/qt-6/licenses-used-in-qt.html",
    ),
    Component(
        "py7zr, pyppmd, pybcj, inflate64, multivolumefile",
        "LGPL-2.1-or-later",
        "https://github.com/miurahr/py7zr",
        (
            "packages/py7zr.txt",
            "packages/pyppmd.txt",
            "packages/pybcj.txt",
            "packages/inflate64.txt",
            "packages/multivolumefile.txt",
            "LGPL-2.1.txt",
        ),
        "Used unmodified; source code at the address above.",
    ),
    Component("pyzipper", "MIT", "https://github.com/danifus/pyzipper", ("packages/pyzipper.txt",)),
    Component(
        "pycryptodomex",
        "BSD-2-Clause, public domain",
        "https://www.pycryptodome.org",
        ("packages/pycryptodomex.txt",),
    ),
    Component(
        "libarchive-c",
        "CC0-1.0",
        "https://github.com/Changaco/python-libarchive-c",
        ("packages/libarchive-c.txt",),
    ),
    Component(
        "libarchive",
        "BSD-2-Clause",
        "https://www.libarchive.org",
        ("libarchive-BSD-2.txt",),
        "Included in the installers for rar and other formats.",
    ),
    Component(
        "Pillow",
        "MIT-CMU",
        "https://python-pillow.org",
        ("packages/pillow.txt",),
    ),
    Component(
        "pi-heif",
        "BSD-3-Clause; libheif and libde265 under LGPL-3.0",
        "https://github.com/bigcat88/pillow_heif",
        ("packages/pi-heif.txt", "LGPL-3.0.txt"),
    ),
    Component(
        "PyAV",
        "BSD-3-Clause; FFmpeg under LGPL-2.1-or-later",
        "https://pyav.basswood-io.com",
        ("packages/av.txt", "LGPL-2.1.txt"),
    ),
    Component(
        "charset-normalizer",
        "MIT",
        "https://github.com/jawah/charset_normalizer",
        ("packages/charset-normalizer.txt",),
    ),
    Component(
        "psutil", "BSD-3-Clause", "https://github.com/giampaolo/psutil", ("packages/psutil.txt",)
    ),
    Component("Brotli", "MIT", "https://github.com/google/brotli", ("packages/brotli.txt",)),
    Component(
        "texttable", "MIT", "https://github.com/foutaise/texttable", ("packages/texttable.txt",)
    ),
    Component(
        "backports.zstd",
        "PSF-2.0; Zstandard under BSD-3-Clause",
        "https://github.com/Rogdham/backports.zstd",
        ("packages/backports.zstd.txt",),
    ),
    Component(
        "mammoth, cobble",
        "BSD-2-Clause",
        "https://github.com/mwilliamson/python-mammoth",
        ("packages/mammoth.txt", "packages/cobble.txt"),
    ),
    Component(
        "openpyxl, et-xmlfile",
        "MIT",
        "https://foss.heptapod.net/openpyxl/openpyxl",
        ("packages/openpyxl.txt", "packages/et-xmlfile.txt"),
    ),
    Component(
        "python-pptx, XlsxWriter",
        "MIT; BSD-2-Clause",
        "https://github.com/scanny/python-pptx",
        ("packages/python-pptx.txt", "packages/xlsxwriter.txt"),
    ),
    Component(
        "lxml (with libxml2 and libxslt)",
        "BSD-3-Clause; MIT",
        "https://lxml.de",
        ("packages/lxml.txt",),
    ),
    Component(
        "typing-extensions",
        "PSF-2.0",
        "https://github.com/python/typing_extensions",
        ("packages/typing-extensions.txt",),
    ),
    Component(
        "Inter, JetBrains Mono and Sora fonts",
        "OFL-1.1",
        "https://openfontlicense.org",
        ("OFL-1.1-fonts.txt",),
    ),
)


def version() -> str:
    return __version__


def notices_text() -> str:
    """All third-party notices and license texts as one plain text, each
    license text once."""
    parts = [
        f"Archy {__version__}, made by {MAKER} ({WEBSITE_LABEL}).",
        f"Open source under the MIT license: {SOURCE}",
        "",
        "Archy is built on the following software:",
        "",
    ]
    for component in COMPONENTS:
        line = f"- {component.name}: {component.license}, {component.url}"
        parts.append(line)
        if component.note:
            parts.append(f"  {component.note}")
    own = LICENSES / "Archy-MIT.txt"
    if own.is_file():
        text = own.read_text(encoding="utf-8").strip()
        parts += ["", "=" * 72, "Archy (MIT)", "=" * 72, "", text]
    seen: set[str] = set()
    for component in COMPONENTS:
        for name in component.files:
            if name in seen:
                continue
            seen.add(name)
            path = LICENSES / name
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                parts += ["", "=" * 72, name, "=" * 72, "", text]
    return "\n".join(parts) + "\n"
