"""The panes that make up the browser: sidebar, document table, detail.

Each is a thin view over data the app hands it. The sidebar is a ``ListView``
with vim motions; the document list is a columned ``DataTable`` (status glyph ·
Title · Authors · Venue · Year) with a full-row teal cursor. None of these
widgets touch the database — the app owns the ``DocumentService`` and pushes
rows/documents in. Colors are resolved from the active theme at render time, so
switching themes reskins every cell.
"""

from rich.text import Text

from textual.binding import Binding
from textual.widgets import DataTable, Label, ListItem, ListView

from soap.db.documents import DocumentRow
from soap.models.document import ReadStatus, ReviewStatus
from soap.tui.widgets_detail import DetailPane  # noqa: F401  (re-export)

# Status glyphs: "where my cursor is" (the teal row cursor) never shares a color
# with "what state a row is in" (these glyphs) — the fzf fg+/state split.
_GLYPH = {
    "needs_review": ("●", "accent"),  # amber — attention
    "read": ("●", "success"),  # green — done
    "reading": ("◐", "secondary"),  # blue — in progress
    "unread": ("○", "text-muted"),  # hollow — to read
}


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


# --- document list (DataTable) --------------------------------------------


def _authors_cell(row: DocumentRow) -> str:
    """Render an author summary: surname / "A, B" / "Lead +N"."""
    if not row.author_count or not row.lead_authors:
        return "—"
    names = [n.strip() for n in row.lead_authors.split(";") if n.strip()]
    surnames = [n.split(",")[0].strip() for n in names if n]
    if not surnames:
        return "—"
    if row.author_count == 1:
        return surnames[0]
    if row.author_count == 2:
        return ", ".join(surnames[:2])
    return f"{surnames[0]} +{row.author_count - 1}"


class DocumentList(DataTable):
    """The middle pane: the filtered documents as a columned table.

    Kept named ``DocumentList`` (was a ``ListView``) so the app's references and
    the pane id are stable; it is now a row-cursor ``DataTable``.
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "go_top", "Top", show=False),
        Binding("G", "go_bottom", "Bottom", show=False),
        Binding("ctrl+d", "half_down", "Half page down", show=False),
        Binding("ctrl+u", "half_up", "Half page up", show=False),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(
            *args, cursor_type="row", zebra_stripes=True, cell_padding=0, **kwargs
        )
        self._ids: list[str] = []
        # Ids the user has marked (space) for a bulk action such as export. Kept
        # by id so a marked row survives a refresh/re-sort; ids that leave the
        # library are pruned in ``populate``.
        self.marked: set[str] = set()

    def toggle_mark(self, doc_id: str | None) -> bool | None:
        """Toggle ``doc_id``'s marked state; return the new state (or ``None``)."""
        if not doc_id:
            return None
        if doc_id in self.marked:
            self.marked.discard(doc_id)
            return False
        self.marked.add(doc_id)
        return True

    def clear_marks(self) -> None:
        self.marked.clear()

    # Fixed non-title column widths; Title flexes to fill the rest. cell_padding
    # is 0 (see __init__) so a 1-col gutter is baked into each cell's text; that
    # buys back the ~10 columns default padding would cost and lets all five
    # columns fit the list pane at typical widths.
    _OTHER_COLS = 1 + 14 + 9 + 6

    def on_mount(self) -> None:
        self.add_column(" ", key="status", width=1)
        self.add_column(" Title", key="title", width=34)
        self.add_column(" Authors", key="authors", width=14)
        self.add_column(" Venue", key="venue", width=9)
        self.add_column(Text("Year ", justify="right"), key="year", width=6)

    def on_resize(self) -> None:
        self._fit_title()

    def _fit_title(self) -> None:
        """Grow/shrink the Title column to fill the laid-out pane width."""
        if "title" not in self.columns:
            return
        content_w = self.content_size.width or self.size.width
        if content_w <= 0:
            return
        # Reserve the other columns plus DataTable's per-column cell padding
        # (1 each side) and a little slack so the right-most Year column always
        # lands inside the pane rather than scrolling off the edge.
        reserve = self._OTHER_COLS + 3
        self.columns["title"].width = max(18, content_w - reserve)
        self._require_update_dimensions = True
        self.refresh()

    # -- theme-aware colors -------------------------------------------------

    def _color(self, token: str) -> str:
        """Resolve a theme variable (e.g. 'accent') to a concrete color."""
        try:
            return self.app.theme_variables.get(token, "#ffffff")
        except Exception:  # noqa: BLE001 - defensive; never break a row render
            return "#ffffff"

    # -- population ---------------------------------------------------------

    def populate(self, rows: list[DocumentRow], selected_id: str | None = None) -> None:
        self.clear()
        self._ids = []
        # Drop marks for documents no longer present so a mark never lingers on a
        # deleted/filtered-away id.
        present = {row.id for row in rows}
        self.marked &= present
        muted = self._color("text-muted")
        fg = self._color("foreground")
        year_c = self._color("secondary")
        accent = self._color("accent")
        for row in rows:
            marked = row.id in self.marked
            # A marked row swaps its status glyph for a filled accent marker so
            # the selection is legible without stealing a column.
            if marked:
                gtxt = Text("▣", style=accent)
            else:
                glyph, gtoken = _GLYPH.get(
                    row.review_status
                    if row.review_status == ReviewStatus.NEEDS_REVIEW.value
                    else row.read_status,
                    _GLYPH["unread"],
                )
                gtxt = Text(glyph, style=self._color(gtoken))
            attention = (
                row.review_status == ReviewStatus.NEEDS_REVIEW.value
                or row.read_status == ReadStatus.UNREAD.value
            )
            ttxt = Text(
                " " + (row.title or "—"),
                style=(accent if marked else (f"bold {fg}" if attention else muted)),
                no_wrap=True,
                overflow="ellipsis",
            )
            atxt = Text(
                " " + _authors_cell(row), style=muted, no_wrap=True, overflow="ellipsis"
            )
            vtxt = Text(
                " " + (row.venue or "—"), style=muted, no_wrap=True, overflow="ellipsis"
            )
            ytxt = Text(
                (str(row.year) if row.year else "—") + " ",
                style=year_c,
                justify="right",
            )
            self.add_row(gtxt, ttxt, atxt, vtxt, ytxt, key=row.id, height=1)
            self._ids.append(row.id)
        if self._ids:
            row = self._ids.index(selected_id) if selected_id in self._ids else 0
            self.move_cursor(row=row)
        self.call_after_refresh(self._fit_title)

    @property
    def current_id(self) -> str | None:
        idx = self.cursor_row
        if 0 <= idx < len(self._ids):
            return self._ids[idx]
        return None

    # -- vim motions --------------------------------------------------------

    def action_go_top(self) -> None:
        if self.row_count:
            self.move_cursor(row=0)

    def action_go_bottom(self) -> None:
        if self.row_count:
            self.move_cursor(row=self.row_count - 1)

    def _half(self) -> int:
        return max(1, self.size.height // 2)

    def action_half_down(self) -> None:
        self.move_cursor(row=min(self.row_count - 1, self.cursor_row + self._half()))

    def action_half_up(self) -> None:
        self.move_cursor(row=max(0, self.cursor_row - self._half()))


# --- sidebar --------------------------------------------------------------


class SidebarHeader(ListItem):
    """A non-selectable section label (LIBRARY / TAGS / COLLECTIONS)."""

    def __init__(self, text: str) -> None:
        super().__init__(Label(text, classes="side-header"), classes="side-header-item")
        self.disabled = True


class SidebarRow(ListItem):
    """A selectable filter: a library view, a tag, or a collection."""

    def __init__(
        self,
        kind: str,
        value: str | None,
        label: str,
        count: int | None = None,
        *,
        attention: bool = False,
    ) -> None:
        classes = "side-row attention" if attention else "side-row"
        super().__init__(classes=classes)
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
            SidebarRow("all", None, "All", counts["all"]),
            SidebarRow(
                "inbox", None, "⚑ Inbox", counts["inbox"], attention=counts["inbox"] > 0
            ),
            SidebarRow("toread", None, "To read", counts["toread"]),
            SidebarRow("reading", None, "Reading", counts.get("reading", 0)),
        ]
        if tags:
            items.append(SidebarHeader("TAGS"))
            items.extend(
                SidebarRow("tag", name, f"# {name}", n) for name, n in tags
            )
        if collections:
            items.append(SidebarHeader("COLLECTIONS"))
            items.extend(
                SidebarRow("collection", name, name, n) for name, n in collections
            )
        self.extend(items)
        # Highlight "All" (index 1; 0 is the disabled header).
        self.index = 1
