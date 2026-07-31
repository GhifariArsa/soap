import typer

from soap.cli import init, inbox, library, selfupdate

app = typer.Typer()
app.add_typer(init.app)
app.add_typer(library.app)
app.add_typer(inbox.app, name="inbox")
app.add_typer(selfupdate.app, name="self")


def _version_callback(value: bool) -> None:
    if value:
        # Shares one resolver with `soap self update` so they never disagree.
        typer.echo(selfupdate.current_version())
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
        # A once-per-24h, offline-safe hint that a newer soap exists. Skipped
        # for `self` so it never runs mid-update; never blocks the subcommand.
        if ctx.invoked_subcommand != "self":
            from soap.cli.selfupdate import maybe_nudge
            from soap.library import resolve_soap_dir

            maybe_nudge(resolve_soap_dir())
        return
    # No subcommand: launch the terminal UI over the resolved library.
    from soap.library import Library, resolve_soap_dir
    from soap.tui import run

    run(Library(resolve_soap_dir()))


if __name__ == "__main__":
    app()
