import typer

app = typer.Typer()


@app.command()
def init():
    """
    Initialize the application.
    """
    typer.echo("Initializing the application...")
