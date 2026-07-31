"""Regression tests for shell generation and private library permissions."""

import shutil
import subprocess
from pathlib import Path

import pytest

from soap.cli.init import _write_starter_config
from soap.config import save_theme
from soap.ingest.merge import Overrides
from soap.library import Library, add
from soap.permissions import PRIVATE_DIRECTORY_MODE, PRIVATE_FILE_MODE
from soap.shell import BLOCK_END, BLOCK_START, export_line, source_command, write_shell_export


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _source_with_bash(config: Path) -> subprocess.CompletedProcess[str]:
    """Source a generated file without interpolating its path into a command."""
    return subprocess.run(
        ["bash", "-c", 'source "$1"; printf \'%s\' "$SOAP_DIR"', "soap-test", str(config)],
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_shell_block_quotes_hostile_paths_without_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell: str
):
    """Separators, substitutions, quotes, and newlines remain path data."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    marker = tmp_path / "command-executed"
    hostile = tmp_path / (
        "library; touch "
        + str(marker)
        + "\n$(touch "
        + str(marker)
        + ") `touch "
        + str(marker)
        + "` 'quoted'"
    )

    result = write_shell_export(shell, hostile)

    assert result.status == "added"
    assert not marker.exists()
    assert result.config_path.read_text().count(BLOCK_START) == 1
    assert result.config_path.read_text().count(BLOCK_END) == 1

    if shell == "fish":
        # Fish is not installed in every test environment.  When it is, source
        # the actual fish block as well as checking its generated syntax.
        if shutil.which("fish"):
            sourced = subprocess.run(
                [
                    "fish",
                    "-c",
                    'source $argv[1]; printf "%s" $SOAP_DIR',
                    "soap-test",
                    str(result.config_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            assert sourced.returncode == 0, sourced.stderr
            assert sourced.stdout == str(hostile)
        else:
            assert "set -gx SOAP_DIR='" in result.export_line
    else:
        shell_bin = shutil.which(shell)
        if shell_bin:
            sourced = subprocess.run(
                [
                    shell_bin,
                    "-c",
                    'source "$1"; printf \'%s\' "$SOAP_DIR"',
                    "soap-test",
                    str(result.config_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            assert sourced.returncode == 0, sourced.stderr
            assert sourced.stdout == str(hostile)
        else:
            pytest.skip(f"{shell} is not installed")
    assert not marker.exists()


def test_unknown_shell_fallback_is_safe_to_source(tmp_path: Path):
    marker = tmp_path / "command-executed"
    hostile = tmp_path / f"lib; touch {marker}\n$(touch {marker})"
    config = tmp_path / "unknown-shell-config"
    config.write_text(export_line("unknown", hostile) + "\n")

    sourced = _source_with_bash(config)

    assert sourced.returncode == 0, sourced.stderr
    assert sourced.stdout == str(hostile)
    assert not marker.exists()


def test_shell_block_marker_is_rejected_without_writing_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    marker = tmp_path / "command-executed"
    hostile = tmp_path / f"lib\n{BLOCK_END}\ntouch {marker}"

    result = write_shell_export("bash", hostile)

    assert result.status == "failed"
    assert result.error and "reserved soap shell-block marker" in result.error
    assert not result.config_path.exists()
    assert not marker.exists()


def test_source_command_quotes_a_hostile_config_path(tmp_path: Path):
    marker = tmp_path / "command-executed"
    config = tmp_path / "shell config; touch command-executed\n"
    config.write_text("export SOAP_DIR=ok\n")

    command = source_command(config)
    sourced = subprocess.run(
        ["bash", "-c", command + '; printf \'%s\' "$SOAP_DIR"'],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert sourced.returncode == 0, sourced.stderr
    assert sourced.stdout == "ok"
    assert not marker.exists()


def test_library_directories_and_metadata_are_private(library: Library, tmp_path: Path):
    assert _mode(library.path) == PRIVATE_DIRECTORY_MODE
    assert _mode(library.inbox) == PRIVATE_DIRECTORY_MODE
    assert _mode(library.documents) == PRIVATE_DIRECTORY_MODE
    assert _mode(library.db_path) == PRIVATE_FILE_MODE

    config = library.path / "config.yaml"
    config.write_text("always_review: true\n")
    config.chmod(0o644)
    _write_starter_config(library.path)
    assert _mode(config) == PRIVATE_FILE_MODE

    save_theme(library.path, "aqua-slate")
    assert _mode(config) == PRIVATE_FILE_MODE

    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4 private test")
    source.chmod(0o777)
    outcome = add(library, str(source), fetch=False, overrides=Overrides(title="Private"))

    assert outcome.status == "added"
    folder = library.documents / (outcome.citekey or "")
    assert _mode(folder) == PRIVATE_DIRECTORY_MODE
    assert _mode(folder / "info.yaml") == PRIVATE_FILE_MODE
    assert _mode(folder / "source.pdf") == PRIVATE_FILE_MODE


def test_existing_library_database_is_hardened(library: Library):
    library.db_path.chmod(0o666)

    Library(library.path).initialize_database()

    assert _mode(library.db_path) == PRIVATE_FILE_MODE


def test_shell_normal_path_keeps_export_ux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    result = write_shell_export("bash", tmp_path / "library")

    assert result.status == "added"
    assert result.export_line == f"export SOAP_DIR={tmp_path / 'library'}"
    assert "source " in source_command(result.config_path)
