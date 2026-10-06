"""Settings > About: version, maker and the third-party notices."""

from __future__ import annotations

import archy
from archy import about


def test_about_names_the_version_maker_and_website() -> None:
    assert about.version() == archy.__version__
    assert about.MAKER == "Bytefulminds"
    assert about.WEBSITE_LABEL == "www.bytefulminds.com"


def test_every_listed_license_file_exists_and_the_notices_include_them() -> None:
    for component in about.COMPONENTS:
        for name in component.files:
            assert (about.LICENSES / name).is_file(), name
    text = about.notices_text()
    assert f"Archy {archy.__version__}" in text
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in text  # Qt, py7zr
    assert "SIL OPEN FONT LICENSE" in text  # the fonts
    assert "Copyright 2016 The Inter Project Authors" in text
    assert "MIT License" in text  # Archy itself
