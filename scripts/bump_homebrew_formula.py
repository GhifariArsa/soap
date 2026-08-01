#!/usr/bin/env python3
"""Rewrite the Homebrew tap formula for a new soap release.

Given the `homebrew-soap` formula, a release tag, and that release's
`checksums.txt`, this bumps the per-platform download URLs to the new tag and
swaps in the matching sha256 for each binary. It is deliberately a small,
deterministic rewrite instead of `brew bump-formula-pr`: the formula is a
multi-platform *binary* formula with three separate `url`/`sha256` pairs nested
in `on_macos`/`on_linux` blocks (plus an Intel-mac `odie` branch and no
`version` stanza — the version is scanned from the URL), which the standard
single-URL bumper does not model.

usage: bump_homebrew_formula.py <formula.rb> <tag> <checksums.txt>
  <tag> is the release tag, e.g. `v0.2.32` (a bare `0.2.32` is accepted too).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Release assets that map into the formula, one url/sha256 pair each. There is
# intentionally no macos-x86_64 asset (see the formula's Intel-mac `odie`).
ASSETS = ("soap-macos-arm64", "soap-linux-arm64", "soap-linux-x86_64")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def parse_checksums(text: str) -> dict[str, str]:
    """Parse `<sha256>  <asset>` lines into {asset: sha256}."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise ValueError(f"malformed checksums line: {line!r}")
        sha, name = parts
        if not _SHA256_RE.match(sha):
            raise ValueError(f"not a sha256: {sha!r}")
        sums[name] = sha
    return sums


def bump(formula: str, version: str, sums: dict[str, str]) -> str:
    """Return the formula text with each asset's URL version + sha256 updated."""
    for asset in ASSETS:
        if asset not in sums:
            raise ValueError(f"checksums.txt has no entry for {asset}")
        pattern = re.compile(
            r'(url "https://github\.com/GhifariArsa/soap/releases/download/)'
            r'v[^/"]+'
            r"(/" + re.escape(asset) + r'"\s*\n\s*sha256 ")'
            r"[0-9a-f]{64}"
            r'(")'
        )
        formula, n = pattern.subn(
            lambda m: f"{m.group(1)}v{version}{m.group(2)}{sums[asset]}{m.group(3)}",
            formula,
        )
        if n != 1:
            raise ValueError(f"expected exactly one url/sha256 block for {asset}, found {n}")
    return formula


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(f"usage: {argv[0]} <formula.rb> <tag> <checksums.txt>", file=sys.stderr)
        return 2

    formula_path = Path(argv[1])
    version = argv[2].lstrip("v")
    checksums_path = Path(argv[3])

    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(f"tag does not look like a version: {argv[2]!r}", file=sys.stderr)
        return 2
    if not formula_path.is_file():
        print(f"formula not found: {formula_path}", file=sys.stderr)
        return 2
    if not checksums_path.is_file():
        print(f"checksums not found: {checksums_path}", file=sys.stderr)
        return 2

    try:
        sums = parse_checksums(checksums_path.read_text())
        updated = bump(formula_path.read_text(), version, sums)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    formula_path.write_text(updated)
    print(f"bumped {formula_path} to v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
