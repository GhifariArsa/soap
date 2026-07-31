#!/usr/bin/env python3
"""Validate the application files and console script in a release wheel."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


REQUIRED_FILES = {"soap/__init__.py", "soap/main.py"}
EXPECTED_ENTRY_POINT = "soap = soap.main:app"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <wheel>", file=sys.stderr)
        return 2

    wheel = Path(argv[1])
    if not wheel.is_file():
        print(f"wheel not found: {wheel}", file=sys.stderr)
        return 2

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        missing = REQUIRED_FILES - names
        if missing:
            print(
                f"wheel is missing application files: {', '.join(sorted(missing))}",
                file=sys.stderr,
            )
            return 1

        entry_points = [
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        ]
        if len(entry_points) != 1:
            print("wheel must contain exactly one entry_points.txt", file=sys.stderr)
            return 1
        entries = archive.read(entry_points[0]).decode().splitlines()
        if EXPECTED_ENTRY_POINT not in entries:
            print(
                f"wheel is missing the installed command: {EXPECTED_ENTRY_POINT}",
                file=sys.stderr,
            )
            return 1

    print(f"wheel contents OK: {wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
