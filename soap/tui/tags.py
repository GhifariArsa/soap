"""The `t edit tags` flow: a keyboard-first modal for tagging a document.

The reviewer opens it on the highlighted document and builds a tag set live:
type a tag and press ``enter`` (or ``,``/space) to add it as a chip, ``tab`` to
accept the top suggestion, ``backspace`` on an empty field to drop the last
chip, ``ctrl+s`` to persist, ``esc`` to cancel. Suggestions come from the tags
already in the library (:meth:`DocumentService.tag_counts`), prefix-first then
substring, so tagging stays consistent and typo-free.

Persisting goes through :func:`soap.library.save_document` — the whole document
is rewritten to ``info.yaml`` (the source of truth) and re-indexed, so the chips
and the sidebar tag counts refresh from disk. Tag normalization (lowercase,
strip, sort, dedupe) is the model's job (``Document.normalize_tags``); this
screen mirrors it for live display so what you see is what gets saved.

Look follows Aqua Slate: chips reuse the detail pane's ``on $primary-muted``
slot, the tab-target suggestion inverts to ``on $primary``, and every label/value
gap goes through :func:`soap.tui._markup.sep` — no hardcoded hex.
"""

import re

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Middle, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from rich.markup import escape

from soap.db.documents import DocumentService
from soap.library import Library, save_document
from soap.models.document import Document
from soap.tui._markup import key, sep

# How many suggestions to surface at once — enough to scan, not a wall of text.
_MAX_SUGGEST = 8

_LEGEND = sep(2).join(
    [
        key("enter", "add", "$success"),
        key("tab", "complete"),
        key("⌫", "drop last"),
        key("^s", "save", "$success"),
        key("esc", "cancel", "$text-muted"),
    ]
)


def _norm(tag: str) -> str:
    """Normalize one tag the same way ``Document.normalize_tags`` does."""
    return tag.lower().strip()


def _split(raw: str) -> list[str]:
    """Split raw input into candidate tags on commas/whitespace."""
    return [t for t in re.split(r"[,\s]+", raw) if t.strip()]


class TagInput(Input):
    """The tag entry box: ``tab`` completes, empty ``backspace`` drops a chip."""

    class Complete(Message):
        """Bubbled when the user presses ``tab`` to accept the top suggestion."""

    class DropLast(Message):
        """Bubbled when ``backspace`` is pressed on an already-empty field."""

    BINDINGS = [Binding("tab", "complete", "complete", show=False)]

    def action_complete(self) -> None:
        self.post_message(self.Complete())

    def action_delete_left(self) -> None:
        # Empty field + backspace removes the last chip (nothing to delete in the
        # input itself); otherwise fall back to normal character deletion.
        if self.value == "":
            self.post_message(self.DropLast())
        else:
            super().action_delete_left()


class TagEditScreen(ModalScreen[list[str] | None]):
    """Add/remove tags on one document, with library-wide autocomplete.

    Dismisses with the saved tag list on ``ctrl+s`` (``None`` if cancelled or
    unchanged) so the app can refresh and notify.
    """

    AUTO_FOCUS = "#tag-input"

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
        # Working set — normalized + order-preserving; seeded from the document.
        self.tags: list[str] = []
        for t in document.tags:
            n = _norm(t)
            if n and n not in self.tags:
                self.tags.append(n)
        # Library-wide vocabulary, most-used first, minus what this doc already has.
        self.vocab: list[str] = [name for name, _ in docs.tag_counts()]

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                with Vertical(id="tag-card"):
                    yield Static("", id="tag-chips")
                    yield TagInput(
                        id="tag-input",
                        placeholder="type a tag — enter to add, tab to complete",
                    )
                    yield Static("", id="tag-suggest")
                    yield Static(_LEGEND, id="tag-hint")

    def on_mount(self) -> None:
        title = self.doc.title or self.doc.id
        self.query_one("#tag-card").border_title = f"#  TAGS · {escape(title[:48])}"
        self._render_chips()
        self._render_suggestions()

    # -- rendering ---------------------------------------------------------

    def _render_chips(self) -> None:
        chips = self.query_one("#tag-chips", Static)
        if self.tags:
            chips.update(
                sep(2).join(
                    f"[$text-primary on $primary-muted] #{escape(t)} [/]"
                    for t in self.tags
                )
            )
        else:
            chips.update("[$text-muted]no tags yet — type one below[/]")

    def _partial(self) -> str:
        """The token currently being typed (text after the last separator)."""
        raw = self.query_one("#tag-input", Input).value
        parts = re.split(r"[,\s]+", raw)
        return _norm(parts[-1]) if parts else ""

    def _matches(self) -> list[str]:
        """Vocabulary matches for the current partial, prefix-first, deduped."""
        partial = self._partial()
        pool = [t for t in self.vocab if t not in self.tags]
        if not partial:
            return pool[:_MAX_SUGGEST]
        prefix = [t for t in pool if t.startswith(partial)]
        substr = [t for t in pool if partial in t and not t.startswith(partial)]
        return (prefix + substr)[:_MAX_SUGGEST]

    def _render_suggestions(self) -> None:
        suggest = self.query_one("#tag-suggest", Static)
        matches = self._matches()
        if not matches:
            suggest.update("[$text-muted]no matching tags[/]")
            return
        # The first match is the tab-completion target — invert it so the target
        # is obvious; the rest are muted chips.
        parts = [f"[$background on $primary] {escape(matches[0])} [/]"]
        parts += [f"[$text-muted on $surface] {escape(m)} [/]" for m in matches[1:]]
        suggest.update(sep(1).join(parts))

    # -- mutation ----------------------------------------------------------

    def _add(self, tag: str) -> None:
        n = _norm(tag)
        if n and n not in self.tags:
            self.tags.append(n)

    def _commit_input(self) -> None:
        """Fold any typed text into the tag set and clear the field."""
        box = self.query_one("#tag-input", Input)
        for token in _split(box.value):
            self._add(token)
        box.value = ""

    # -- events ------------------------------------------------------------

    @on(Input.Changed, "#tag-input")
    def _input_changed(self, event: Input.Changed) -> None:
        # A comma or trailing space means "commit the token before it" so tags
        # can be rattled off without pressing enter each time.
        if event.value and event.value[-1] in ", ":
            head = event.value[:-1]
            if head.strip():
                for token in _split(head):
                    self._add(token)
                self.query_one("#tag-input", Input).value = ""
                self._render_chips()
        self._render_suggestions()

    @on(Input.Submitted, "#tag-input")
    def _input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._commit_input()
        self._render_chips()
        self._render_suggestions()

    @on(TagInput.Complete)
    def _complete(self, event: TagInput.Complete) -> None:
        matches = self._matches()
        if not matches:
            return
        self._add(matches[0])
        self.query_one("#tag-input", Input).value = ""
        self._render_chips()
        self._render_suggestions()

    @on(TagInput.DropLast)
    def _drop_last(self, event: TagInput.DropLast) -> None:
        if self.tags:
            self.tags.pop()
            self._render_chips()
            self._render_suggestions()

    # -- actions -----------------------------------------------------------

    def action_save(self) -> None:
        self._commit_input()
        # Model normalization (sorted set) is the real comparison basis.
        if sorted(set(self.tags)) == sorted(set(_norm(t) for t in self.doc.tags)):
            self.dismiss(None)
            return
        data = self.doc.model_dump(mode="json")
        data["tags"] = self.tags
        try:
            updated = Document(**data)
            save_document(self.library, updated, self.docs)
        except Exception as exc:  # noqa: BLE001 - surface, never crash the modal
            self.app.notify(f"could not save tags: {exc}", severity="error")
            return
        self.dismiss(updated.tags)

    def action_cancel(self) -> None:
        self.dismiss(None)
