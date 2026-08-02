"""A small keyboard-first confirmation modal for destructive actions.

The browser's ``d`` delete pushes this before calling
:func:`soap.library.delete_document` — mirroring the CLI review's
``confirm_delete`` gate (``soap/cli/inbox.py``), but as a Textual modal rather
than a raw stdin prompt. It states exactly what is about to be removed (title,
citekey, attached-file count — deletion takes the folder and its files with it)
so the choice is informed, and dismisses with a ``bool``: ``True`` to proceed,
``False`` to cancel. Styling is theme-token only (reuses the shared card slots in
``app.tcss``); every label/value gap goes through :func:`soap.tui._markup.sep`.
"""

from rich.markup import escape

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Middle, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from soap.models.document import Document
from soap.tui._markup import key, sep

_LEGEND = sep(2).join(
    [
        key("enter / y", "delete", "$error"),
        key("esc / n", "cancel", "$text-muted"),
    ]
)


class ConfirmDeleteScreen(ModalScreen[bool]):
    """Confirm deleting one document and its files. Dismisses ``True``/``False``."""

    # Nothing to type into — start in command mode so y/n/enter/esc act at once.
    AUTO_FOCUS = None

    BINDINGS = [
        Binding("y", "confirm", "delete", show=True),
        Binding("enter", "confirm", "delete", show=False),
        Binding("n", "cancel", "cancel", show=True),
        Binding("escape", "cancel", "cancel", show=False),
    ]

    def __init__(self, document: Document) -> None:
        super().__init__()
        self.doc = document

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                with Vertical(id="confirm-card"):
                    yield Static("", id="confirm-body")
                    yield Static(_LEGEND, id="confirm-hint")

    def on_mount(self) -> None:
        self.query_one("#confirm-card").border_title = "⚠  DELETE DOCUMENT"
        title = self.doc.title or self.doc.id
        n = len(self.doc.files)
        files = "no attached files" if n == 0 else (
            f"{n} attached file{'s' if n != 1 else ''}"
        )
        self.query_one("#confirm-body", Static).update(
            "[$foreground]Delete[/]"
            + sep(1)
            + f"[b $foreground]{escape(title)}[/]"
            + "[$foreground]?[/]\n\n"
            + "[$text-muted]citekey[/]"
            + sep(1)
            + f"[$foreground]{escape(self.doc.id)}[/]"
            + sep(2)
            + "[$text-muted]·[/]"
            + sep(2)
            + f"[$error]{files}[/]"
            + "\n\n"
            + "[$text-muted]This removes the document folder and its files "
            + "from disk — it cannot be undone.[/]"
        )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ConfirmBulkDeleteScreen(ModalScreen[bool]):
    """Confirm deleting several marked documents at once. Dismisses ``True``/``False``.

    The bulk counterpart to :class:`ConfirmDeleteScreen`: one explicit,
    count-aware gate for the whole selection rather than a prompt per document. It
    states how many documents and attached files are about to be removed so the
    choice is informed; cancelling makes no change.
    """

    AUTO_FOCUS = None

    BINDINGS = [
        Binding("y", "confirm", "delete", show=True),
        Binding("enter", "confirm", "delete", show=False),
        Binding("n", "cancel", "cancel", show=True),
        Binding("escape", "cancel", "cancel", show=False),
    ]

    def __init__(self, documents: list[Document]) -> None:
        super().__init__()
        self.documents = documents

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                with Vertical(id="confirm-card"):
                    yield Static("", id="confirm-body")
                    yield Static(_LEGEND, id="confirm-hint")

    def on_mount(self) -> None:
        n = len(self.documents)
        files = sum(len(d.files) for d in self.documents)
        self.query_one("#confirm-card").border_title = "⚠  DELETE DOCUMENTS"
        titles = ", ".join(escape(d.title or d.id) for d in self.documents[:3])
        if n > 3:
            titles += f", … (+{n - 3} more)"
        self.query_one("#confirm-body", Static).update(
            "[$foreground]Delete[/]"
            + sep(1)
            + f"[b $foreground]{n}[/]"
            + sep(1)
            + f"[$foreground]selected document{'s' if n != 1 else ''}?[/]\n\n"
            + f"[$text-muted]{titles}[/]\n\n"
            + f"[$error]{files} attached file{'s' if files != 1 else ''}[/]"
            + sep(2)
            + "[$text-muted]·[/]"
            + sep(2)
            + "[$text-muted]removes each document folder and its files from disk "
            + "— it cannot be undone.[/]"
        )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
