"""The detail pane: full metadata for the selected document.

A rich, themed read-out — bold title, a needs-review line with a confidence
meter, an aligned metadata table, themed tag chips (no hardcoded hex — colors
come from the active theme), abstract, and the file path. All spacing goes
through :func:`soap.tui._markup.sep` so styled labels never mash into their
values.
"""

from typing import ClassVar

from rich.markup import escape
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.events import Blur, Focus
from textual.widgets import Static

from soap.models.document import Document, ReviewStatus
from soap.tui._markup import confidence_meter, sep

_LABEL_WIDTH = 6
_ABSTRACT_PREVIEW_CHARS = 480


def _abstract_preview(abstract: str) -> str:
    """Return a small, deterministic preview suitable for the unfocused pane."""
    if len(abstract) <= _ABSTRACT_PREVIEW_CHARS:
        return abstract
    # Prefer ending on a word boundary, while keeping the preview bounded even
    # when metadata contains one very long line.
    preview = abstract[:_ABSTRACT_PREVIEW_CHARS].rsplit(" ", 1)[0].rstrip()
    return preview + "…"


def _kv(label: str, value: str, value_color: str = "$foreground") -> str:
    """An aligned ``label   value`` row for the metadata table."""
    pad = sep(_LABEL_WIDTH - len(label) + 2)
    return f"[$text-muted]{label}[/]" + pad + f"[{value_color}]{escape(value)}[/]"


def _author_summary(authors: list[str]) -> str:
    """First three surnames, then ``+N`` for the rest (matches the list view)."""
    surnames = [a.split(",")[0].strip() for a in authors if a.strip()]
    if len(surnames) <= 3:
        return ", ".join(surnames)
    return ", ".join(surnames[:3]) + f" +{len(surnames) - 3}"


class DetailPane(VerticalScroll):
    """The right pane, compact until focused and scrollable when focused."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("j", "detail_scroll_down", "Scroll down", show=False),
        Binding("k", "detail_scroll_up", "Scroll up", show=False),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._document: Document | None = None

    def compose(self):
        yield Static("", id="detail-body")

    def show(self, document: Document | None) -> None:
        self._document = document
        body = self.query_one("#detail-body", Static)
        if document is None:
            body.update("[$text-muted]Nothing selected[/]")
        else:
            body.update(self._to_markup(document, focused=self.has_focus))
        # A new selection (and every focus-mode transition) starts at the top.
        self.scroll_home(animate=False)

    def on_focus(self, _event: Focus) -> None:
        self._rerender(focused=True)

    def on_blur(self, _event: Blur) -> None:
        self._rerender(focused=False)

    def _rerender(self, *, focused: bool) -> None:
        if self._document is None:
            return
        self.query_one("#detail-body", Static).update(
            self._to_markup(self._document, focused=focused)
        )
        self.scroll_home(animate=False)

    def action_detail_scroll_down(self) -> None:
        self.scroll_down(animate=False)

    def action_detail_scroll_up(self) -> None:
        self.scroll_up(animate=False)

    def _to_markup(self, doc: Document, *, focused: bool = False) -> str:
        lines: list[str] = [f"[b $primary]{escape(doc.title or '—')}[/]"]

        if doc.authors:
            lines.append(f"[$text-muted]{escape(_author_summary(doc.authors))}[/]")

        lines.append("")

        if doc.review_status == ReviewStatus.NEEDS_REVIEW.value:
            head = "[b $accent]⚑ needs review[/]"
            if doc.confidence is not None:
                _, color, _ = confidence_meter(doc.confidence)
                head += (
                    sep(3)
                    + "[$text-muted]·[/]"
                    + sep(3)
                    + "[$text-muted]conf[/]"
                    + sep(1)
                    + f"[b {color}]{doc.confidence:.2f}[/]"
                )
            lines.append(head)
            if doc.confidence is not None:
                _, _, meter = confidence_meter(doc.confidence)
                lines.append(meter)
            lines.append("")

        # Aligned metadata table.
        venue_bits = [b for b in (doc.venue, str(doc.year) if doc.year else None) if b]
        if venue_bits:
            lines.append(_kv("venue", " · ".join(venue_bits)))
        if doc.type:
            lines.append(_kv("type", doc.type))
        if doc.arxiv_id:
            lines.append(_kv("arxiv", doc.arxiv_id, "$secondary"))
        if doc.doi:
            lines.append(_kv("doi", doc.doi, "$secondary"))
        if doc.added_at:
            lines.append(_kv("added", str(doc.added_at)[:10], "$text-muted"))
        lines.append(_kv("read", str(doc.read_status), "$text-muted"))

        if doc.tags:
            lines.append("")
            chips = sep(2).join(
                f"[$text-primary on $primary-muted] #{escape(t)} [/]" for t in doc.tags
            )
            lines.append(chips)

        if doc.abstract:
            lines.append("")
            lines.append("[b $text-muted]ABSTRACT[/]")
            abstract = doc.abstract if focused else _abstract_preview(doc.abstract)
            lines.append(f"[$text-muted]{escape(abstract)}[/]")

        lines.append("")
        if doc.files:
            for f in doc.files:
                name = f.path.rsplit("/", 1)[-1]
                lines.append("[$secondary]▸[/]" + sep(1) + f"[$text-muted]{escape(name)}[/]")
            lines.append(f"[$text-muted]{escape(doc.files[0].path)}[/]")
        elif doc.url:
            lines.append("[$secondary]↗[/]" + sep(1) + f"[$secondary]{escape(doc.url)}[/]")
        else:
            lines.append("[$text-muted]no file attached[/]")

        return "\n".join(lines)
