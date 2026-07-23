"""The soap TUI application — a refman-style library browser.

Layout mirrors the mockup: a top bar (logo + search), a conditional inbox bar,
a three-pane body (sidebar / list / detail), and a footer of keybindings. The
app owns the single ``DocumentService`` connection for the session; widgets are
dumb views it feeds. Mutations (review→file) go through the library layer so the
on-disk ``info.yaml`` stays authoritative.
"""

import os
import subprocess
import sys
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Middle
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, ListView, Static

from soap.db.documents import DocumentService
from soap.library import Library
from soap.tui.review import ReviewScreen
from soap.tui.themes import DEFAULT_THEME, THEMES
from soap.tui.widgets import DetailPane, DocumentList, Sidebar, SidebarRow


class SearchInput(Input):
    """The header search box; Escape clears it and returns focus to the list."""

    BINDINGS = [Binding("escape", "cancel", "Clear", show=False)]

    def action_cancel(self) -> None:
        self.value = ""  # fires Changed -> app clears the search filter
        self.app.query_one(DocumentList).focus()


class HelpScreen(ModalScreen[None]):
    """A dismissible cheat-sheet of keybindings."""

    BINDINGS = [Binding("escape,q,question_mark", "dismiss", "Close", show=True)]

    HELP = """[b]soap — keys[/b]

[$accent]move[/]     j/k  up/down     g/G  top/bottom     ctrl+d/u  half page
[$accent]panes[/]    h/l  focus left/right     tab  cycle
[$accent]open[/]     enter or o   open the selected file
[$accent]search[/]   /    search title/author/tag     esc  clear
[$accent]review[/]   r    walk the inbox (a file · e edit · s skip)
[$accent]theme[/]    ctrl+t cycle     ctrl+p command palette
[$accent]other[/]    ctrl+r refresh     ? help     q quit

[$text-muted]a add · b export bibtex — not yet wired[/]"""

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                yield Static(self.HELP, id="help-card")


class SoapApp(App):
    CSS_PATH = "app.tcss"
    ENABLE_COMMAND_PALETTE = True

    # Panes cycled by h/l and tab.
    PANES = ("#sidebar", "#doclist", "#detail")

    BINDINGS = [
        Binding("o", "open", "open", show=True),
        Binding("enter", "open", "open", show=False),
        Binding("a", "add", "add", show=True),
        Binding("slash", "search", "search", show=True, key_display="/"),
        Binding("f", "filter", "filter", show=True),
        Binding("r", "review", "review inbox", show=True),
        Binding("b", "export", "export bibtex", show=True),
        Binding("question_mark", "help", "help", show=True, key_display="?"),
        Binding("l", "focus_pane(1)", "Focus right", show=False),
        Binding("h", "focus_pane(-1)", "Focus left", show=False),
        Binding("ctrl+r", "refresh_data", "refresh", show=False),
        Binding("ctrl+t", "cycle_theme", "theme", show=False),
        Binding("q", "quit", "quit", show=False),
    ]

    def __init__(self, library: Library) -> None:
        super().__init__()
        self.library = library
        self.docs: DocumentService | None = None
        self.filter_kind = "all"
        self.filter_value: str | None = None
        self.search_term = ""
        self._initialized = library.is_initialized

    # -- construction ------------------------------------------------------

    def compose(self) -> ComposeResult:
        if not self._initialized:
            with Middle():
                with Center():
                    yield Static(
                        "[b]No soap library found.[/b]\n\n"
                        "[$text-muted]Run [/][$accent]soap init[/][$text-muted] to create one, "
                        "then launch [/][$accent]soap[/][$text-muted] again.[/]",
                        id="empty-card",
                    )
            yield Footer()
            return

        with Horizontal(id="topbar"):
            yield Static("\U0001f4da  soap", id="logo")
            yield SearchInput(
                placeholder="/  search title, author, tag…", id="search"
            )
        yield Static("", id="inboxbar")
        with Horizontal(id="body"):
            yield Sidebar(id="sidebar")
            yield DocumentList(id="doclist")
            yield DetailPane(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        for theme in THEMES:
            self.register_theme(theme)
        self.theme = DEFAULT_THEME
        if not self._initialized:
            return
        self.docs = DocumentService.open(self.library.db_path)
        self.refresh_data()
        # Focus the list, not the search box (which is first in DOM order), so
        # j/k browse immediately and the footer shows the full command set.
        self.query_one(DocumentList).focus()

    def on_unmount(self) -> None:
        if self.docs is not None:
            self.docs.close()

    # -- data flow ---------------------------------------------------------

    def refresh_data(self) -> None:
        """Rebuild sidebar + inbox bar + list from the database."""
        if self.docs is None:
            return
        counts = self.docs.library_counts()
        self.query_one(Sidebar).build(
            counts, self.docs.tag_counts(), self.docs.collection_counts()
        )
        self._update_inbox_bar(counts["inbox"])
        self._populate_list()

    def _update_inbox_bar(self, inbox: int) -> None:
        bar = self.query_one("#inboxbar", Static)
        if inbox:
            bar.display = True
            plural = "s" if inbox != 1 else ""
            bar.update(f"⚑  INBOX · {inbox} document{plural} need{'' if plural else 's'} review")
        else:
            bar.display = False

    def _populate_list(self) -> None:
        if self.docs is None:
            return
        rows = self.docs.list_documents(
            filter_kind=self.filter_kind,
            filter_value=self.filter_value,
            search=self.search_term or None,
        )
        doclist = self.query_one(DocumentList)
        doclist.populate(rows)
        if not rows:
            self.query_one(DetailPane).show(None)

    def _show_detail(self, doc_id: str | None) -> None:
        if self.docs is None or doc_id is None:
            self.query_one(DetailPane).show(None)
            return
        self.query_one(DetailPane).show(self.docs.get_document(doc_id))

    # -- events ------------------------------------------------------------

    @on(ListView.Highlighted, "#sidebar")
    def _sidebar_moved(self, event: ListView.Highlighted) -> None:
        item = event.item
        if isinstance(item, SidebarRow):
            self.filter_kind = item.kind
            self.filter_value = item.value
            self._populate_list()

    @on(ListView.Highlighted, "#doclist")
    def _doc_moved(self, event: ListView.Highlighted) -> None:
        row_id = getattr(event.item, "row", None)
        self._show_detail(row_id.id if row_id is not None else None)

    @on(Input.Changed, "#search")
    def _search_changed(self, event: Input.Changed) -> None:
        self.search_term = event.value.strip()
        self._populate_list()

    # -- actions -----------------------------------------------------------

    def action_focus_pane(self, delta: int) -> None:
        widgets = [self.query_one(sel) for sel in self.PANES]
        focused = self.focused
        current = 0
        for i, w in enumerate(widgets):
            if focused is w or (focused is not None and focused in w.walk_children()):
                current = i
                break
        widgets[(current + delta) % len(widgets)].focus()

    def action_search(self) -> None:
        self.query_one("#search", SearchInput).focus()

    def action_open(self) -> None:
        doc_id = self.query_one(DocumentList).current_id
        if self.docs is None or doc_id is None:
            return
        doc = self.docs.get_document(doc_id)
        if doc is None or not doc.files:
            self.notify("no file attached to this document", severity="warning")
            return
        target = self.library.path / doc.files[0].path
        if not target.exists():
            self.notify(f"file missing on disk: {target}", severity="error")
            return
        self._launch(target)

    def action_review(self) -> None:
        if self.docs is None:
            return
        ids = [r.id for r in self.docs.list_documents(filter_kind="inbox")]
        if not ids:
            self.notify("inbox is empty — nothing to review")
            return
        self.push_screen(ReviewScreen(self.library, self.docs, ids), self._after_review)

    def _after_review(self, result: tuple[int, int] | None) -> None:
        if result:
            filed, skipped = result
            parts = [f"{filed} filed"]
            if skipped:
                parts.append(f"{skipped} skipped")
            self.notify(", ".join(parts))
        self.refresh_data()

    def action_add(self) -> None:
        self.notify("add isn't wired into the TUI yet — use `soap add …`")

    def action_export(self) -> None:
        self.notify("bibtex export isn't wired up yet")

    def action_filter(self) -> None:
        self.notify("filter menu is coming; use the sidebar or / search for now")

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_cycle_theme(self) -> None:
        names = [t.name for t in THEMES] + [
            n for n in self.available_themes if n not in {t.name for t in THEMES}
        ]
        try:
            i = names.index(self.theme)
        except ValueError:
            i = -1
        self.theme = names[(i + 1) % len(names)]
        self.notify(f"theme: {self.theme}")

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _launch(path: Path) -> None:
        """Open a file with the OS default handler, non-blocking."""
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])


def run(library: Library) -> None:
    SoapApp(library).run()
