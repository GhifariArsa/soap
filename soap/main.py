import typer

from soap.cli import init, inbox, library

app = typer.Typer()
app.add_typer(init.app)
app.add_typer(library.app)
app.add_typer(inbox.app, name="inbox")


def _resolve_version() -> str:
    """Best-effort version string for `soap --version`.

    Prefers installed distribution metadata; falls back to the generated
    ``soap/_version.py`` (written by hatch-vcs at build time) when running from
    an uninstalled source tree.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("soap-tui")
    except PackageNotFoundError:
        try:
            from soap._version import __version__

            return __version__
        except Exception:
            return "0.0.0+unknown"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(_resolve_version())
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the soap version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """soap — a reference manager. Run with no command to open the TUI."""
    if ctx.invoked_subcommand is not None:
        return
    # No subcommand: launch the terminal UI over the resolved library.
    from soap.library import Library, resolve_soap_dir
    from soap.tui import run

    run(Library(resolve_soap_dir()))


if __name__ == "__main__":
    app()
