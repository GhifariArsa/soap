"""The `r review inbox` flow: a modal that walks the needs_review queue.

Per item the reviewer sees the core metadata as **prefilled, editable fields**
(title / authors / year / type / venue) and can file it (saving any inline
edits), drop the whole ``info.yaml`` into ``$EDITOR`` for the long tail, or skip.
Filing goes through :func:`soap.library.save_document`, which rewrites
``info.yaml`` (the source of truth) before touching the index. The screen
dismisses with ``(filed, skipped)`` counts so the app can report and refresh.

Keyboard model (fields are Textual ``Input`` widgets, so single-letter actions
would be typed as text while a field is focused). The modal therefore has two
implicit modes, distinguished by focus:

- **command mode** (no field focused, the initial state): ``a``/⏎ file, ``c``/tab
  start correcting, ``e`` opens ``$EDITOR``, ``s`` skips, ``q``/esc done.
- **edit mode** (a field focused): type freely, ``tab``/``shift+tab`` move between
  fields, ⏎ files, ``esc`` returns to command mode.

Non-priority screen bindings give this for free: a focused ``Input`` consumes the
letter keys as text, so the action bindings only fire in command mode.
"""

from rich.markup import escape

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Middle, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from soap.db.documents import DocumentService
from soap.library import (
    CORE_REVIEW_FIELDS,
    Library,
    edit_document,
    save_document,
)
from soap.models.document import Document, ReviewStatus


class ReviewScreen(ModalScreen[tuple[int, int]]):
    """Walk the inbox queue one document at a time, editing fields in place."""

    # Don't auto-focus a field on mount — start in command mode so the reviewer's
    # first a/e/s/q acts on the item instead of being typed into the title.
    AUTO_FOCUS = None

    BINDINGS = [
        Binding("a", "file", "file", show=True),
        Binding("enter", "file", "file", show=False),
        Binding("c", "correct", "correct", show=True),
        Binding("e", "editor", "$EDITOR", show=True),
        Binding("s", "skip", "skip", show=True),
        Binding("q", "close", "done", show=True),
        Binding("escape", "cancel", "done", show=False),
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
                    for field in CORE_REVIEW_FIELDS:
                        with Horizontal(classes="field-row"):
                            yield Static(field, classes="field-label")
                            yield Input(id=f"f-{field}", classes="field-input")
                    yield Static("", id="review-facts")
                    yield Static(
                        "[$text-muted]⏎/a file · c/tab correct · "
                        "e $EDITOR · s skip · q done[/]",
                        id="review-hint",
                    )

    def on_mount(self) -> None:
        self._render_current()
        # Start in command mode (nothing focused) so a/e/s/q act immediately; the
        # reviewer taps c or tab to edit a field. Deferred past the refresh so it
        # wins against Textual's post-mount auto-focus of the first Input.
        self.call_after_refresh(self.set_focus, None)

    # -- queue rendering ---------------------------------------------------

    def _current_doc(self) -> Document | None:
        if self.pos >= len(self.queue):
            return None
        return self.docs.get_document(self.queue[self.pos])

    def _render_current(self) -> None:
        if self.pos >= len(self.queue):
            self.dismiss((self.filed, self.skipped))
            return
        doc = self._current_doc()
        if doc is None:  # vanished from under us; skip it
            self.pos += 1
            self._render_current()
            return

        self.query_one("#review-progress", Static).update(
            f"[$accent]REVIEW[/]  [$text-muted]{self.pos + 1}/{len(self.queue)}[/]"
        )

        # Prefill each editable field with the detected value.
        self.query_one("#f-title", Input).value = doc.title or ""
        self.query_one("#f-authors", Input).value = "; ".join(doc.authors)
        self.query_one("#f-year", Input).value = str(doc.year) if doc.year else ""
        self.query_one("#f-type", Input).value = doc.type or ""
        self.query_one("#f-venue", Input).value = doc.venue or ""

        conf = f" · confidence {doc.confidence:.2f}" if doc.confidence is not None else ""
        attach = escape(doc.files[0].path.rsplit("/", 1)[-1]) if doc.files else "none"
        self.query_one("#review-facts", Static).update(
            f"[$text-muted]source[/] {escape(doc.source)}{conf}   "
            f"[$text-muted]file[/] {attach}"
        )

    # -- form <-> model ----------------------------------------------------

    def _read_form(self, doc: Document) -> Document:
        """Build an updated document from the current field values (id pinned).

        Reads the fields literally — an emptied field clears its value (inline
        Zotero-style edit) rather than restoring the detected one. The id is
        pinned so filing never renames the folder (decision 2). May raise
        ``ValueError`` (bad year) or a pydantic ``ValidationError``.
        """
        def val(field: str) -> str:
            return self.query_one(f"#f-{field}", Input).value.strip()

        data = doc.model_dump(mode="json")
        data["title"] = val("title")
        data["authors"] = [a.strip() for a in val("authors").split(";") if a.strip()]
        year = val("year")
        data["year"] = int(year) if year else None
        data["type"] = val("type") or "article"
        data["venue"] = val("venue") or None
        updated = Document(**data)
        updated.id = doc.id
        return updated

    # -- actions -----------------------------------------------------------

    def action_correct(self) -> None:
        """Enter edit mode: focus the first field."""
        self.query_one("#f-title", Input).focus()

    @on(Input.Submitted)
    def _on_submit(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_file()

    def action_file(self) -> None:
        doc = self._current_doc()
        if doc is None:
            return
        try:
            updated = self._read_form(doc)
        except Exception as exc:  # noqa: BLE001 - surface, don't crash the modal
            self.app.notify(f"invalid metadata: {exc}", severity="error")
            return
        if not updated.title:
            self.app.notify("title is required to file", severity="error")
            return
        updated.review_status = ReviewStatus.FILED.value
        try:
            save_document(self.library, updated, self.docs)
        except Exception as exc:  # noqa: BLE001
            self.app.notify(f"could not file {doc.id}: {exc}", severity="error")
            return
        self.filed += 1
        self.pos += 1
        self.set_focus(None)  # back to command mode for the next item
        self._render_current()

    def action_skip(self) -> None:
        self.skipped += 1
        self.pos += 1
        self.set_focus(None)
        self._render_current()

    def action_editor(self) -> None:
        doc = self._current_doc()
        if doc is None:
            return
        doc_id = self.queue[self.pos]
        # Best-effort: persist inline edits first so $EDITOR opens what the
        # reviewer sees on screen (a blank/invalid title is left for the editor).
        try:
            updated = self._read_form(doc)
            if updated.title:
                save_document(self.library, updated, self.docs)
        except Exception:  # noqa: BLE001 - fall through to edit the on-disk file
            pass
        with self.app.suspend():
            try:
                edit_document(self.library, doc_id, self.docs)
            except Exception as exc:  # noqa: BLE001 - invalid edit, keep the item
                self.app.notify(
                    f"edit not applied ({exc})", severity="warning", timeout=6
                )
        # Stay on the same item, reloading whatever the editor saved.
        self.set_focus(None)
        self.refresh()
        self._render_current()

    def action_cancel(self) -> None:
        # esc leaves a focused field (back to command mode); from command mode it
        # closes the modal.
        if isinstance(self.focused, Input):
            self.set_focus(None)
        else:
            self.dismiss((self.filed, self.skipped))

    def action_close(self) -> None:
        self.dismiss((self.filed, self.skipped))
