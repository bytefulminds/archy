#!/usr/bin/env python3
"""Diagnostic: is libarchive available, and can it read RAR?

Run this after installing dependencies to confirm the native libarchive
library is reachable. libarchive-c does NOT bundle the C library; it must be
present on the system:

  Debian/Ubuntu : sudo apt install libarchive13
  Fedora        : sudo dnf install libarchive
  macOS (brew)  : brew install libarchive
  Windows       : ship libarchive.dll next to the app (handled in packaging)
"""

from __future__ import annotations


def main() -> int:
    from archy.core.native import ensure_libarchive_env

    ensure_libarchive_env()
    try:
        import libarchive  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print("libarchive-c NOT usable:", exc)
        print("Install the native library (see the module docstring).")
        return 1

    try:
        from libarchive.ffi import version_number

        number = int(version_number())  # e.g. 3007004 for 3.7.4
    except Exception as exc:  # noqa: BLE001
        print("libarchive binding loaded but version lookup failed:", exc)
        return 1
    major, minor, patch = number // 1_000_000, number // 1_000 % 1_000, number % 1_000
    print(f"libarchive OK - version: {major}.{minor}.{patch}")

    # RAR read support depends on how libarchive was built; only opening a
    # real .rar tells for sure.
    print("Note: RAR read support depends on the libarchive build.")
    print("Open a .rar in Archy to confirm it works.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
