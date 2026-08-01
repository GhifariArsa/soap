"""The browser's ``E`` in-app edit form: correct the core fields without $EDITOR.

This is the main-screen counterpart to the review modal's inline correction. It
renders the same :data:`soap.library.CORE_REVIEW_FIELDS` as prefilled, editable
``Input`` widgets and, on save, folds them back through the shared correction
core :func:`soap.library.prompt_fields` — so the citekey is pinned (the folder is
never renamed, matching the review-edit contract in ``CLAUDE.md``) and an
emptied field keeps its detected value exactly as the CLI ``[c]orrect`` walk
does. Persisting goes through :func:`soap.library.save_document`, which rewrites
``info.yaml`` (the source of truth) then re-indexes; the DB is never touched
directly.

For the long tail (abstract, tags, arbitrary keys) the browser's ``e`` →
``$EDITOR`` path stays available; this form is the quick, keyboard-first core
edit. Styling reuses the shared ``field-row``/``field-input`` slots in
``app.tcss`` — theme tokens only, no hardcoded hex.

Keyboard model mirrors the review form's edit mode: fields are ``Input`` widgets,
so ``tab``/``shift+tab`` move between them, ``enter`` saves, ``esc`` cancels.
The screen dismisses with the (unchanged) document id on save, or ``None`` on
cancel, so the app can refresh and re-select the row.
"""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Middle, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from rich.markup import escape

from soap.db.documents import DocumentService
from soap.library import CORE_REVIEW_FIELDS, Library, prompt_fields, save_document
from soap.models.document import Document
from soap.tui._markup import key, sep

_LEGEND = sep(2).join(
    [
        key("enter / ^s", "save", "$success"),
        key("tab", "next field"),
        key("esc", "cancel", "$text-muted"),
    ]
)


class EditScreen(ModalScreen[str | None]):
    """Edit one filed document's core fields in place. Dismisses id / ``None``."""

    # Focus the first field immediately — this screen is purely an edit form
    # (unlike the review modal, there is no command mode to preserve).
    AUTO_FOCUS = "#f-title"

    BINDINGS = [
        Binding("ctrl+s", "save", "save", show=True),
        Binding("escape", "cancel", "cancel", show=False),
    ]

    def __init__(
        self, library: Library, docs: DocumentService, document: Document
    ) -> None:
        super().__init__()
        self.library = library
        self.docs = docs
        self.doc = document

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                with Vertical(id="edit-card"):
                    for field in CORE_REVIEW_FIELDS:
                        with Horizontal(classes="field-row"):
                            yield Static(field, classes="field-label")
                            placeholder = (
                                "Last, First; Last, First"
                                if field == "authors"
                                else ""
                            )
                            yield Input(
                                id=f"f-{field}",
                                classes="field-input",
                                placeholder=placeholder,
                            )
                    yield Static("", id="edit-facts")
                    yield Static(_LEGEND, id="edit-hint")

    def on_mount(self) -> None:
        title = self.doc.title or self.doc.id
        self.query_one("#edit-card").border_title = f"✎  EDIT · {escape(title[:44])}"
        # Prefill each field with the current value; prompt_fields keeps any field
        # left unchanged (and keeps an emptied field's detected value — the same
        # correction semantics as the CLI [c]orrect walk).
        self.query_one("#f-title", Input).value = self.doc.title or ""
        self.query_one("#f-authors", Input).value = "; ".join(self.doc.authors)
        self.query_one("#f-year", Input).value = (
            str(self.doc.year) if self.doc.year else ""
        )
        self.query_one("#f-type", Input).value = self.doc.type or ""
        self.query_one("#f-venue", Input).value = self.doc.venue or ""
        self.query_one("#edit-facts", Static).update(
            "[$text-muted]citekey[/]"
            + sep(1)
            + f"[$foreground]{escape(self.doc.id)}[/]"
            + sep(2)
            + "[$text-muted]·[/]"
            + sep(2)
            + "[$text-muted]pinned — editing never renames the folder[/]"
        )

    def _prompt_fn(self, field: str, _current: str) -> str:
        """Feed prompt_fields the on-screen value for a field.

        ``_current`` (the detected value prompt_fields passes in) is ignored: the
        form already prefilled the widget with it, so the widget value is the
        single source of truth for what the user wants saved.
        """
        return self.query_one(f"#f-{field}", Input).value

    @on(Input.Submitted)
    def _on_submit(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_save()

    def action_save(self) -> None:
        try:
            # The shared correction core pins the id, so save never renames the
            # folder — the review-edit contract in CLAUDE.md.
            updated = prompt_fields(self.doc, self._prompt_fn)
        except Exception as exc:  # noqa: BLE001 - surface, never crash the modal
            self.app.notify(f"invalid metadata: {exc}", severity="error")
            return
        if not updated.title:
            self.app.notify("title is required", severity="error")
            return
        try:
            save_document(self.library, updated, self.docs)
        except Exception as exc:  # noqa: BLE001
            self.app.notify(f"could not save {self.doc.id}: {exc}", severity="error")
            return
        self.dismiss(updated.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
