"""The `r review inbox` flow: a modal that walks the needs_review queue.

Per item the reviewer can accept-and-file, edit the metadata in ``$EDITOR``, or
skip. Filing goes through :func:`soap.library.set_review_status`, which rewrites
``info.yaml`` (the source of truth) before touching the index. The screen
dismisses with ``(filed, skipped)`` counts so the app can report and refresh.
"""

from rich.markup import escape

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Middle, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from soap.db.documents import DocumentService
from soap.library import Library, edit_document, set_review_status
from soap.models.document import ReviewStatus


class ReviewScreen(ModalScreen[tuple[int, int]]):
    """Walk the inbox queue one document at a time."""

    BINDINGS = [
        Binding("a", "accept", "file", show=True),
        Binding("enter", "accept", "file", show=False),
        Binding("e", "edit", "edit", show=True),
        Binding("s", "skip", "skip", show=True),
        Binding("q", "close", "done", show=True),
        Binding("escape", "close", "done", show=False),
    ]

    def __init__(self, library: Library, docs: DocumentService, ids: list[str]) -> None:
        super().__init__()
        self.library = library
        self.docs = docs
        self.queue = list(ids)
        self.pos = 0
        self.filed = 0
        self.skipped = 0

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                with Vertical(id="review-card"):
                    yield Static("", id="review-progress")
                    yield Static("", id="review-body")
                    yield Static(
                        "[$text-muted]a file · e edit · s skip · q done[/]",
                        id="review-hint",
                    )

    def on_mount(self) -> None:
        self._render_current()

    # -- queue rendering ---------------------------------------------------

    def _render_current(self) -> None:
        if self.pos >= len(self.queue):
            self.dismiss((self.filed, self.skipped))
            return
        doc = self.docs.get_document(self.queue[self.pos])
        if doc is None:  # vanished from under us; skip it
            self.pos += 1
            self._render_current()
            return

        self.query_one("#review-progress", Static).update(
            f"[$accent]REVIEW[/]  [$text-muted]{self.pos + 1}/{len(self.queue)}[/]"
        )

        lines = [f"[b]{escape(doc.title)}[/b]"]
        if doc.authors:
            lines.append(f"[$text-muted]{escape(', '.join(doc.authors))}[/]")
        facts = [b for b in (doc.venue, str(doc.year) if doc.year else None) if b]
        if facts:
            lines.append(f"[$text-muted]{escape(' · '.join(facts))}[/]")
        ident = doc.doi or doc.arxiv_id or doc.isbn
        if ident:
            lines.append(f"[$text-muted]{escape(ident)}[/]")
        lines.append("")
        lines.append(
            f"[$text-muted]source[/] {escape(doc.source)}   "
            f"[$text-muted]file[/] {'yes' if doc.files else 'none'}"
        )
        if doc.abstract:
            lines.append("")
            lines.append(escape(doc.abstract[:400]))
        self.query_one("#review-body", Static).update("\n".join(lines))

    # -- actions -----------------------------------------------------------

    def action_accept(self) -> None:
        doc_id = self.queue[self.pos]
        try:
            set_review_status(self.library, doc_id, ReviewStatus.FILED.value, self.docs)
        except Exception as exc:  # noqa: BLE001 - surface, don't crash the modal
            self.app.notify(f"could not file {doc_id}: {exc}", severity="error")
            return
        self.filed += 1
        self.pos += 1
        self._render_current()

    def action_skip(self) -> None:
        self.skipped += 1
        self.pos += 1
        self._render_current()

    def action_edit(self) -> None:
        doc_id = self.queue[self.pos]
        with self.app.suspend():
            try:
                edit_document(self.library, doc_id, self.docs)
            except Exception as exc:  # noqa: BLE001 - invalid edit, keep the item
                self.app.notify(
                    f"edit not applied ({exc})", severity="warning", timeout=6
                )
        # Stay on the same item so the reviewer sees the result and files it.
        self.refresh()
        self._render_current()

    def action_close(self) -> None:
        self.dismiss((self.filed, self.skipped))
