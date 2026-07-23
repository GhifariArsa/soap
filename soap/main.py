import typer

from soap.cli import init, library

app = typer.Typer()
app.add_typer(init.app)
app.add_typer(library.app)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """soap — a papis-like reference manager. Run with no command to open the TUI."""
    if ctx.invoked_subcommand is not None:
        return
    # No subcommand: launch the terminal UI over the resolved library.
    from soap.library import Library, resolve_soap_dir
    from soap.tui import run

    run(Library(resolve_soap_dir()))


if __name__ == "__main__":
    app()
