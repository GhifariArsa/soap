"""The ``soap inbox review`` command — a thin shim over the library layer.

The interactive walk over the ``needs_review`` queue lives in
:func:`soap.library.review_inbox`; this module only wires terminal IO (printing
each document, reading a keystroke, confirming a delete) into it and reports the
final tally. All mutations go through the existing library helpers, so the CLI
and TUI review flows stay consistent.
"""

import sys

import typer

from soap.cli._prompt import prompt_field
from soap.db.documents import DocumentService
from soap.library import CORE_REVIEW_FIELDS, Library, resolve_soap_dir, review_inbox
from soap.models.document import Document

app = typer.Typer(help="Work the inbox: review documents awaiting a human.")

_MENU = "[a]ccept · [c]orrect · [e]$EDITOR · [s]kip · [d]elete · [q]uit"


def _render(doc: Document, position: int, total: int) -> None:
    """Print a readable summary of one document awaiting review."""
    typer.echo()
    typer.echo(
        typer.style(f"[{position + 1}/{total}] ", fg=typer.colors.CYAN, bold=True)
        + typer.style("needs review", fg=typer.colors.YELLOW)
    )
    authors = ", ".join(doc.authors) if doc.authors else "—"
    ident = doc.doi or doc.arxiv_id or doc.isbn or "—"
    conf = f"{doc.confidence:.2f}" if doc.confidence is not None else "—"
    rows = [
        ("citekey", doc.id),
        ("title", doc.title),
        ("authors", authors),
        ("year", str(doc.year) if doc.year else "—"),
        ("id", ident),
        ("source", f"{doc.source}   confidence {conf}   file {'yes' if doc.files else 'none'}"),
    ]
    for label, value in rows:
        typer.echo(f"  {typer.style(f'{label:<8}', fg=typer.colors.BRIGHT_BLACK)} {value}")


def _ask_action() -> str:
    """Read one action from stdin, raising ``EOFError`` on end-of-input.

    Reading a line directly (rather than via ``typer.prompt``) keeps a piped or
    empty stdin from hanging: EOF surfaces as ``EOFError``, which
    :func:`review_inbox` treats as "quit cleanly".
    """
    typer.echo(typer.style(f"  {_MENU}: ", fg=typer.colors.BRIGHT_BLACK), nl=False)
    line = sys.stdin.readline()
    if line == "":
        typer.echo()  # tidy the newline the user's Ctrl-D didn't emit
        raise EOFError
    return line


def _prompt_field(field: str, current: str) -> str:
    """Per-field prompt for the ``[c]orrect`` walk; heads the walk once."""
    if field == CORE_REVIEW_FIELDS[0]:
        typer.echo(
            typer.style(
                "  correcting — Enter keeps the value, type to override",
                fg=typer.colors.BRIGHT_BLACK,
            )
        )
    return prompt_field(field, current)


def _confirm_delete(doc: Document) -> bool:
    """Confirm a destructive delete; default No, EOF surfaces as ``EOFError``."""
    typer.echo(
        typer.style("  delete ", fg=typer.colors.RED)
        + f"{doc.id} and its file(s)? [y/N]: ",
        nl=False,
    )
    line = sys.stdin.readline()
    if line == "":
        typer.echo()
        raise EOFError
    return line.strip().lower() in ("y", "yes")


@app.command()
def review(
    path: str | None = typer.Option(
        None, "--path", help="Library location. Overrides $SOAP_DIR and the default."
    ),
):
    """Walk the needs_review queue, filing, editing, skipping, or deleting each."""
    library = Library(resolve_soap_dir(path))
    if not library.is_initialized:
        typer.echo(
            typer.style("✗", fg=typer.colors.RED)
            + " library is not initialized; run `soap init` first"
        )
        raise typer.Exit(code=1)

    def _report(message: str) -> None:
        typer.echo(typer.style("  ! ", fg=typer.colors.YELLOW) + message, err=True)

    with DocumentService.open(library.db_path) as docs:
        if docs.inbox_count() == 0:
            typer.echo("Nothing to review — the inbox is empty. ✓")
            return
        summary = review_inbox(
            library,
            docs,
            render=_render,
            ask_action=_ask_action,
            confirm_delete=_confirm_delete,
            report=_report,
            prompt_field=_prompt_field,
        )

    parts = [f"{summary.filed} filed"]
    if summary.corrected:
        parts.append(f"{summary.corrected} corrected")
    if summary.edited:
        parts.append(f"{summary.edited} edited")
    if summary.skipped:
        parts.append(f"{summary.skipped} skipped")
    if summary.deleted:
        parts.append(f"{summary.deleted} deleted")
    if summary.errors:
        parts.append(f"{summary.errors} errored")
    typer.echo()
    typer.echo(", ".join(parts))
