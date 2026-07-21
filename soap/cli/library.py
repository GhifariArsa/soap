import typer

app = typer.Typer()


@app.command()
def add():
    """
    Add a new library.
    """
    typer.echo("Adding a new library...")
