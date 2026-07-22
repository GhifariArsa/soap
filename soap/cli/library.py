"""The ``soap add`` command — a thin shim over :func:`soap.library.add`.

All ingest logic lives in the library layer so the v3 inbox agent can call the
same code. This module only parses flags, iterates sources, prints the report,
and sets the exit code.
"""

from pathlib import Path

import httpx
import typer

from soap.db.documents import DocumentService
from soap.ingest.merge import Overrides
from soap.library import (
    AddOutcome,
    Library,
    collect_files,
    resolve_soap_dir,
)
from soap.library import add as add_document
from soap.models.document import ReviewStatus

app = typer.Typer()


@app.command()
def add(
    sources: list[str] = typer.Argument(
        ..., metavar="SOURCE...", help="Local file, directory, or URL to add."
    ),
    title: str | None = typer.Option(None, "--title", help="Override title."),
    author: list[str] = typer.Option(
        None, "--author", help="Override author (repeatable, ordered)."
    ),
    year: int | None = typer.Option(None, "--year", help="Override year."),
    type_: str | None = typer.Option(
        None, "--type", help="BibTeX type (article, inproceedings, book, ...)."
    ),
    doi: str | None = typer.Option(None, "--doi", help="Supply DOI, skips detection."),
    arxiv: str | None = typer.Option(
        None, "--arxiv", help="Supply arXiv ID, skips detection."
    ),
    isbn: str | None = typer.Option(
        None, "--isbn", help="Supply ISBN; fetches book metadata from Open Library."
    ),
    tag: list[str] = typer.Option(None, "--tag", help="Attach tag (repeatable)."),
    collection: list[str] = typer.Option(
        None, "--collection", help="Attach collection (repeatable)."
    ),
    no_fetch: bool = typer.Option(
        False, "--no-fetch", help="Skip all network lookups (Crossref/arXiv)."
    ),
    recursive: bool = typer.Option(
        False, "--recursive", help="Recurse into subfolders for directory sources."
    ),
    edit: bool = typer.Option(
        False, "--edit", "-e", help="Open the generated info.yaml in $EDITOR before saving."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be added; write nothing."
    ),
    force: bool = typer.Option(
        False, "--force", help="Add even if a duplicate is detected."
    ),
    path: str | None = typer.Option(
        None, "--path", help="Library location. Overrides $SOAP_DIR and the default."
    ),
):
    """Add a document (or many) to the library from a file, folder, or URL."""
    library = Library(resolve_soap_dir(path))
    if not library.is_initialized:
        typer.echo(
            typer.style("✗", fg=typer.colors.RED)
            + " library is not initialized; run `soap init` first"
        )
        raise typer.Exit(code=1)

    overrides = Overrides(
        title=title,
        authors=list(author or []),
        year=year,
        type=type_,
        doi=doi,
        arxiv_id=arxiv,
        isbn=isbn,
        tags=list(tag or []),
        collections=list(collection or []),
    )

    if dry_run:
        typer.echo(
            typer.style("DRY RUN", fg=typer.colors.CYAN, bold=True)
            + " — nothing will be written\n"
        )

    outcomes: list[AddOutcome] = []
    # Share a DB connection and HTTP client across the whole batch.
    with DocumentService.open(library.db_path) as docs, httpx.Client() as client:
        for source in sources:
            for item in _expand(source, recursive):
                outcome = add_document(
                    library,
                    item,
                    overrides=overrides,
                    fetch=not no_fetch,
                    force=force,
                    dry_run=dry_run,
                    edit=edit,
                    docs=docs,
                    client=client,
                )
                outcomes.append(outcome)
                _print_row(outcome, item)

    _print_summary(outcomes)
    if any(o.status == "error" for o in outcomes):
        raise typer.Exit(code=1)


def _expand(source: str, recursive: bool) -> list[str]:
    """Expand a filesystem directory into its files; pass URLs/files through."""
    from soap.ingest.url import is_url

    if is_url(source):
        return [source]
    p = Path(source).expanduser()
    if p.is_dir():
        files = collect_files(p, recursive)
        return [str(f) for f in files] or [source]  # empty dir -> one error row
    return [source]


def _truncate(text: str, width: int = 36) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def _print_row(outcome: AddOutcome, source: str) -> None:
    for warning in outcome.warnings:
        typer.echo(typer.style("  ! ", fg=typer.colors.YELLOW) + warning, err=True)

    if outcome.status == "error":
        typer.echo(
            typer.style("✗", fg=typer.colors.RED)
            + f" {'error':<24} {_truncate(source, 40)}  {outcome.message}"
        )
        return

    if outcome.status == "skipped":
        typer.echo(
            typer.style("-", fg=typer.colors.BRIGHT_BLACK)
            + f" {'skipped':<24} {outcome.message}"
        )
        return

    doc = outcome.document
    if outcome.status == "upgraded":
        typer.echo(
            typer.style("↑", fg=typer.colors.GREEN)
            + f" {outcome.matched_id:<24} {outcome.message}"
        )
        return

    # added
    key = outcome.citekey or (doc.id if doc else "")
    year = f" ({doc.year})" if doc and doc.year else ""
    middle = _truncate(f"{doc.title}{year}" if doc else source)
    if doc and doc.review_status == ReviewStatus.NEEDS_REVIEW.value:
        label = f"{doc.source}, needs review"
        marker = typer.style("!", fg=typer.colors.YELLOW)
    else:
        label = doc.source if doc else ""
        marker = typer.style("✓", fg=typer.colors.GREEN)
    typer.echo(f"{marker} {key:<24} {middle:<38} {label}")


def _print_summary(outcomes: list[AddOutcome]) -> None:
    added = sum(1 for o in outcomes if o.status == "added")
    upgraded = sum(1 for o in outcomes if o.status == "upgraded")
    skipped = sum(1 for o in outcomes if o.status == "skipped")
    failed = sum(1 for o in outcomes if o.status == "error")
    needs_review = sum(1 for o in outcomes if o.needs_review)

    parts = [f"{added} added"]
    if upgraded:
        parts.append(f"{upgraded} upgraded")
    if skipped:
        parts.append(f"{skipped} skipped")
    if needs_review:
        parts.append(f"{needs_review} needs review")
    if failed:
        parts.append(f"{failed} failed")

    typer.echo()
    typer.echo(", ".join(parts))
