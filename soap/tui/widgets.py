"""The panes that make up the browser: sidebar, document list, detail.

Each is a thin view over data the app hands it. The two lists subclass a common
``VimList`` so ``j``/``k``/``g``/``G``/half-page motions work identically. None
of these widgets touch the database — the app owns the ``DocumentService`` and
pushes rows/documents in.
"""

from rich.markup import escape

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Label, ListItem, ListView, Static

from soap.db.documents import DocumentRow
from soap.models.document import Document, ReviewStatus


class VimList(ListView):
    """A ListView with vim motions layered on the default arrow-key bindings."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "go_top", "Top", show=False),
        Binding("G", "go_bottom", "Bottom", show=False),
        Binding("ctrl+d", "half_down", "Half page down", show=False),
        Binding("ctrl+u", "half_up", "Half page up", show=False),
    ]

    def action_go_top(self) -> None:
        if len(self.children):
            self.index = 0

    def action_go_bottom(self) -> None:
        count = len(self.children)
        if count:
            self.index = count - 1

    def _half(self) -> int:
        return max(1, self.size.height // 2)

    def action_half_down(self) -> None:
        for _ in range(self._half()):
            self.action_cursor_down()

    def action_half_up(self) -> None:
        for _ in range(self._half()):
            self.action_cursor_up()


# --- document list --------------------------------------------------------


class DocRow(ListItem):
    """One row in the document list: title (left) + year (right)."""

    def __init__(self, row: DocumentRow) -> None:
        super().__init__(classes="doc-row")
        self.row = row

    def compose(self):
        marker = "◆ " if self.row.review_status == ReviewStatus.NEEDS_REVIEW.value else ""
        yield Label(f"{marker}{self.row.title}", classes="doc-title")
        yield Label(str(self.row.year or "—"), classes="doc-year")


class DocumentList(VimList):
    """The middle pane: the filtered list of documents."""

    def populate(self, rows: list[DocumentRow]) -> None:
        self.clear()
        self.extend(DocRow(r) for r in rows)
        if rows:
            self.index = 0

    @property
    def current_id(self) -> str | None:
        child = self.highlighted_child
        return child.row.id if isinstance(child, DocRow) else None


# --- sidebar --------------------------------------------------------------


class SidebarHeader(ListItem):
    """A non-selectable section label (LIBRARY / TAGS / COLLECTIONS)."""

    def __init__(self, text: str) -> None:
        super().__init__(Label(text, classes="side-header"), classes="side-header-item")
        self.disabled = True


class SidebarRow(ListItem):
    """A selectable filter: a library view, a tag, or a collection."""

    def __init__(
        self, kind: str, value: str | None, label: str, count: int | None = None
    ) -> None:
        super().__init__(classes="side-row")
        self.kind = kind
        self.value = value
        self._label = label
        self._count = count

    def compose(self):
        yield Label(self._label, classes="side-label")
        if self._count is not None:
            yield Label(str(self._count), classes="side-count")


class Sidebar(VimList):
    """The left pane: LIBRARY views + TAGS + COLLECTIONS, all as filters."""

    def build(
        self,
        counts: dict[str, int],
        tags: list[tuple[str, int]],
        collections: list[tuple[str, int]],
    ) -> None:
        self.clear()
        items: list[ListItem] = [
            SidebarHeader("LIBRARY"),
            SidebarRow("all", None, "▸ All", counts["all"]),
            SidebarRow("inbox", None, "▸ Inbox", counts["inbox"]),
            SidebarRow("toread", None, "▸ To read", counts["toread"]),
        ]
        if tags:
            items.append(SidebarHeader("TAGS"))
            items.extend(
                SidebarRow("tag", name, f"#{name}", n) for name, n in tags
            )
        if collections:
            items.append(SidebarHeader("COLLECTIONS"))
            items.extend(
                SidebarRow("collection", name, name, n) for name, n in collections
            )
        self.extend(items)
        # Highlight "All" (index 1; 0 is the disabled header).
        self.index = 1


# --- detail pane ----------------------------------------------------------


class DetailPane(VerticalScroll):
    """The right pane: full metadata for the selected document."""

    BINDINGS = [
        Binding("j", "scroll_down", "Down", show=False),
        Binding("k", "scroll_up", "Up", show=False),
    ]

    def compose(self):
        yield Static("", id="detail-body")

    def show(self, document: Document | None) -> None:
        body = self.query_one("#detail-body", Static)
        if document is None:
            body.update("[$text-muted]Nothing selected[/]")
            return
        body.update(self._to_markup(document))

    def _to_markup(self, doc: Document) -> str:
        lines: list[str] = [f"[b]{escape(doc.title)}[/b]"]

        if doc.authors:
            lines.append(f"[$text-muted]{escape(self._authors(doc.authors))}[/]")

        venue_bits = [b for b in (doc.venue, str(doc.year) if doc.year else None) if b]
        if venue_bits:
            lines.append(f"[$text-muted]{escape(' · '.join(venue_bits))}[/]")

        if doc.tags:
            chips = "  ".join(
                f"[#9cc6ef on #17324e] #{escape(t)} [/]" for t in doc.tags
            )
            lines.append("")
            lines.append(chips)

        if doc.abstract:
            lines.append("")
            lines.append("[$text-muted]ABSTRACT[/]")
            lines.append(escape(doc.abstract))

        lines.append("")
        if doc.files:
            for f in doc.files:
                name = f.path.rsplit("/", 1)[-1]
                lines.append(f"[$text-muted]\U0001f4ce {escape(name)}[/]")
        else:
            lines.append("[$text-muted]no file attached[/]")

        return "\n".join(lines)

    @staticmethod
    def _authors(authors: list[str]) -> str:
        if len(authors) == 1:
            return authors[0]
        if len(authors) == 2:
            return f"{authors[0]}, {authors[1]}"
        return f"{authors[0]} et al."
