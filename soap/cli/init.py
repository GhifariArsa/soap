import os
import sqlite3
from enum import Enum
from pathlib import Path

import typer

from soap.db.sqlite import SCHEMA_VERSION
from soap.library import Library, resolve_soap_dir
from soap.shell import detect_shell, source_command, write_shell_export

app = typer.Typer()


class ShellChoice(str, Enum):
    auto = "auto"
    zsh = "zsh"
    bash = "bash"
    fish = "fish"


def _display(path: Path) -> str:
    """Contract the home directory to ``~`` for readable output."""
    home = Path(os.path.expanduser("~"))
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def _ok(label: str, message: str) -> None:
    typer.echo(f"{typer.style('✓', fg=typer.colors.GREEN)} {label:<12} {message}")


def _warn(message: str) -> None:
    typer.echo(f"{typer.style('!', fg=typer.colors.YELLOW)} {message}")


@app.command()
def init(
    path: str | None = typer.Option(
        None, "--path", help="Library location. Overrides $SOAP_DIR and the default."
    ),
    shell: ShellChoice = typer.Option(
        ShellChoice.auto, "--shell", help="Which shell config to write."
    ),
    force: bool = typer.Option(
        False, "--force", help="Reinitialize even if a library already exists (destructive)."
    ),
):
    """Prepare this machine to use soap: create the library, database, and shell export."""
    try:
        soap_dir = resolve_soap_dir(path)
    except ValueError as exc:
        typer.echo(typer.style("✗", fg=typer.colors.RED) + f" {exc}")
        raise typer.Exit(code=2)
    library = Library(soap_dir)
    already_initialized = library.is_initialized and not force

    # 1 & 2. Create the directory structure. Failure here is fatal.
    try:
        library.create_directories()
    except OSError as exc:
        typer.echo(
            typer.style("✗", fg=typer.colors.RED)
            + f" could not create library at {_display(soap_dir)}: {exc}"
        )
        raise typer.Exit(code=1)

    # 3. Create and initialize the database (unless already present w/o --force).
    backup_path = None
    if already_initialized:
        typer.echo(f"soap is already initialized at {_display(soap_dir)}")
        typer.echo()
        _ok("library", _display(soap_dir))
    else:
        try:
            backup_path = library.initialize_database(force=force)
        except (OSError, sqlite3.Error) as exc:
            typer.echo(
                typer.style("✗", fg=typer.colors.RED)
                + f" could not create database at {_display(library.db_path)}: {exc}"
            )
            raise typer.Exit(code=1)
        _ok("library", _display(soap_dir))
        _ok("created", "inbox/ documents/")
        if backup_path is not None:
            _warn(
                f"--force: backed up existing database to {_display(backup_path)} "
                "before recreating it"
            )
        _ok("database", f"soap.db (schema v{SCHEMA_VERSION})")

    # 4. Persist SOAP_DIR to the shell config. A failure here must not abort.
    _write_shell(shell, soap_dir)


def _write_shell(shell: ShellChoice, soap_dir: Path) -> None:
    if shell is ShellChoice.auto:
        target = detect_shell()
        if target is None:
            _warn(
                "could not detect your shell from $SHELL; add SOAP_DIR yourself:"
            )
            typer.echo(f'    export SOAP_DIR="{soap_dir}"')
            return
    else:
        target = shell.value

    result = write_shell_export(target, soap_dir)
    if result.status == "failed":
        _warn(
            f"could not write {_display(result.config_path)} ({result.error}); "
            "add SOAP_DIR yourself:"
        )
        typer.echo(f"    {result.export_line}")
        return

    verb = {
        "added": "added SOAP_DIR to",
        "updated": "updated SOAP_DIR in",
        "unchanged": "SOAP_DIR already set in",
    }[result.status]
    _ok("shell", f"{verb} {_display(result.config_path)}")

    typer.echo()
    typer.echo(
        f"Run `{source_command(result.config_path)}` or open a new terminal "
        "to load SOAP_DIR."
    )
