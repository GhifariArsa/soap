"""Tests for the Homebrew tap formula bumper (scripts/bump_homebrew_formula.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "bump_homebrew_formula", ROOT / "scripts" / "bump_homebrew_formula.py"
)
assert _SPEC and _SPEC.loader
bump_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bump_mod)


FORMULA = """\
class SoapTui < Formula
  desc "Reference manager for research papers (CLI + TUI)"
  homepage "https://github.com/GhifariArsa/soap"
  license "MIT"

  on_macos do
    on_arm do
      url "https://github.com/GhifariArsa/soap/releases/download/v0.2.31/soap-macos-arm64"
      sha256 "78aae85dc73573a4c1563b1c6dd61d0830a6caabbd83f6fc2ca4aecdc476b1cd"
    end
    on_intel do
      odie "soap has no prebuilt Intel-macOS binary; Apple Silicon and Linux only"
    end
  end

  on_linux do
    on_arm do
      url "https://github.com/GhifariArsa/soap/releases/download/v0.2.31/soap-linux-arm64"
      sha256 "8ffc0ba6fcf06de86f9196c44fce9102c0cc15e5b134754c1c4f23d7ddd17628"
    end
    on_intel do
      url "https://github.com/GhifariArsa/soap/releases/download/v0.2.31/soap-linux-x86_64"
      sha256 "cd00a1f6b07d0209fd9b09b6f6aacecb6478a50c3242c2fb9a2626260eb59929"
    end
  end
end
"""

NEW = {
    "soap-macos-arm64": "a" * 64,
    "soap-linux-arm64": "b" * 64,
    "soap-linux-x86_64": "c" * 64,
}
CHECKSUMS = "".join(f"{sha}  {name}\n" for name, sha in NEW.items())


def test_parse_checksums_roundtrip() -> None:
    assert bump_mod.parse_checksums(CHECKSUMS) == NEW


def test_parse_checksums_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        bump_mod.parse_checksums("nothex  soap-macos-arm64\n")
    with pytest.raises(ValueError):
        bump_mod.parse_checksums("deadbeef soap-macos-arm64 extra\n")


def test_bump_updates_every_url_and_pairs_the_right_sha() -> None:
    out = bump_mod.bump(FORMULA, "0.3.0", NEW)

    # Every download URL moved to the new tag; none of the old tag remains.
    assert "/download/v0.2.31/" not in out
    assert out.count("/download/v0.3.0/") == 3

    # Each asset's sha256 sits with its own URL, not another platform's.
    assert 'download/v0.3.0/soap-macos-arm64"\n      sha256 "' + "a" * 64 in out
    assert 'download/v0.3.0/soap-linux-arm64"\n      sha256 "' + "b" * 64 in out
    assert 'download/v0.3.0/soap-linux-x86_64"\n      sha256 "' + "c" * 64 in out

    # The Intel-mac odie guard is untouched.
    assert "no prebuilt Intel-macOS binary" in out


def test_bump_requires_all_assets() -> None:
    partial = {k: v for k, v in NEW.items() if k != "soap-linux-arm64"}
    with pytest.raises(ValueError, match="soap-linux-arm64"):
        bump_mod.bump(FORMULA, "0.3.0", partial)


def test_bump_fails_when_block_missing() -> None:
    # A formula missing a platform block should error, not silently pass.
    trimmed = FORMULA.replace(
        '      url "https://github.com/GhifariArsa/soap/releases/download/'
        'v0.2.31/soap-linux-x86_64"\n'
        '      sha256 "cd00a1f6b07d0209fd9b09b6f6aacecb6478a50c3242c2fb9a2626260eb59929"\n',
        "",
    )
    with pytest.raises(ValueError, match="soap-linux-x86_64"):
        bump_mod.bump(trimmed, "0.3.0", NEW)


def test_main_writes_file(tmp_path: Path) -> None:
    formula = tmp_path / "soap-tui.rb"
    formula.write_text(FORMULA)
    checks = tmp_path / "checksums.txt"
    checks.write_text(CHECKSUMS)

    rc = bump_mod.main(["prog", str(formula), "v1.2.3", str(checks)])
    assert rc == 0
    assert "/download/v1.2.3/" in formula.read_text()


def test_main_rejects_bad_tag(tmp_path: Path) -> None:
    formula = tmp_path / "soap-tui.rb"
    formula.write_text(FORMULA)
    checks = tmp_path / "checksums.txt"
    checks.write_text(CHECKSUMS)

    assert bump_mod.main(["prog", str(formula), "not-a-version", str(checks)]) == 2
