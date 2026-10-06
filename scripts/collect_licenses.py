"""Copy the license files of Archy's Python dependencies into
src/archy/resources/licenses/packages/, for the About tab in Settings and the
installers. Run it after changing or upgrading dependencies:

    python scripts/collect_licenses.py

The standard texts next to them (LGPL, GPL, the font license) are kept as they
are; the list of components shown to users is in src/archy/about.py.
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "archy" / "resources" / "licenses" / "packages"

# Distribution names as pip knows them (see src/archy/about.py).
PACKAGES = (
    "py7zr",
    "pyppmd",
    "pybcj",
    "inflate64",
    "multivolumefile",
    "pyzipper",
    "charset-normalizer",
    "pycryptodomex",
    "psutil",
    "pillow",
    "pi-heif",
    "libarchive-c",
    "av",
    "brotli",
    "texttable",
    "backports.zstd",
)


def license_text(name: str) -> str | None:
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None
    parts = []
    for file in dist.files or ():
        lower = file.name.lower()
        words = ("license", "licence", "copying", "notice")
        if any(word in lower for word in words) and ".dist-info" in str(file):
            text = Path(dist.locate_file(file)).read_text(encoding="utf-8", errors="replace")
            parts.append(f"----- {file.name} -----\n\n{text.strip()}\n")
    return "\n\n".join(parts) if parts else None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in PACKAGES:
        text = license_text(name)
        if text is None:
            print(f"skip    {name} (not installed or no license file)")
            continue
        version = metadata.version(name)
        target = OUT / f"{name}.txt"
        target.write_text(f"{name} {version}\n\n{text}", encoding="utf-8")
        print(f"wrote   {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
