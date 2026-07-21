import typer
from soap.cli import init
from soap.cli import library

app = typer.Typer()
app.add_typer(init.app)
app.add_typer(library.app)

if __name__ == "__main__":
    app()
