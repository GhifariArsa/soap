"""The soap TUI application — a refman-style library browser.

Layout mirrors the mockup: a top bar (brand + search + amber inbox pill), a
three-pane body (sidebar / list / detail) in titled ``round``-bordered panes
whose focused pane border turns teal, and a persistent cheat-bar footer of the
wired verbs. The app owns the single ``DocumentService`` connection for the
session; widgets are dumb views it feeds. Mutations (review→file) go through the
library layer so the on-disk ``info.yaml`` stays authoritative.

Themes are a first-class subsystem (:mod:`soap.tui.themes`): the app registers
the bundled themes plus any the user drops in ``$SOAP_DIR/themes/``, honors the
``theme:`` key from ``config.yaml`` at startup, and persists the choice back
whenever it changes (``ctrl+t`` cycle or the ``ctrl+p`` palette picker).
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
from textual.widgets import DataTable, Input, ListView, Static

from soap.config import load_config, save_theme
from soap.db.documents import DocumentService
from soap.library import Library, edit_document
from soap.tui._markup import key, sep
from soap.tui.review import ReviewScreen
from soap.tui.tags import TagEditScreen
from soap.tui.themes import BUNDLED_THEMES, DEFAULT_THEME, load_user_themes
from soap.tui.widgets import DetailPane, DocumentList, Sidebar, SidebarRow

# Human labels for the list pane's border title, per sidebar filter kind.
_FILTER_TITLES = {
    "all": "All documents",
    "inbox": "Inbox",
    "toread": "To read",
    "reading": "Reading",
}


def _cheatbar(inbox: int) -> str:
    """The persistent footer: wired verbs only, keys teal, review count amber."""
    review = (
        key("r", "review", "$accent")
        + (sep(1) + f"[b $accent]{inbox}[/]" if inbox else "")
    )
    return sep(3).join(
        [
            key("j/k", "move"),
            key("enter", "open"),
            key("/", "find"),
            key("t", "tags"),
            review,
            key("tab", "pane"),
            key("?", "keys"),
            key("^p", "palette"),
            key("q", "quit"),
        ]
    )


class SearchInput(Input):
    """The header search box; Escape clears it and returns focus to the list."""

    BINDINGS = [Binding("escape", "cancel", "Clear", show=False)]

    def action_cancel(self) -> None:
        self.value = ""  # fires Changed -> app clears the search filter
        self.app.query_one(DocumentList).focus()


class HelpScreen(ModalScreen[None]):
    """A dismissible three-column cheat sheet (MOVE / ACT / APP)."""

    BINDINGS = [Binding("escape,q,question_mark", "dismiss", "Close", show=True)]

    # Only wired commands appear here (no `a add` / `b export` stubs).
    _COLS = [
        (
            "MOVE",
            [
                ("j / k", "down / up"),
                ("g / G", "top / bottom"),
                ("^d / ^u", "half page"),
                ("tab", "cycle panes"),
                ("h / l", "focus left / right"),
            ],
        ),
        (
            "ACT",
            [
                ("enter / o", "open file"),
                ("/", "search"),
                ("t", "edit tags"),
                ("r", "review inbox"),
            ],
        ),
        (
            "APP",
            [
                ("?", "this help"),
                ("^p", "command palette"),
                ("^t", "cycle theme"),
                ("^r", "refresh"),
                ("q", "quit"),
            ],
        ),
    ]

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        with Middle():
            with Center():
                with Vertical(id="help-card"):
                    with Horizontal(id="help-cols"):
                        for head, rows in self._COLS:
                            body = f"[b $text-muted]{head}[/]\n\n" + "\n".join(
                                self._row(k, d) for k, d in rows
                            )
                            yield Static(body, classes="help-col")
                    yield Static(
                        "[$text-muted]press[/]"
                        + sep(1)
                        + "[b $primary]?[/]"
                        + sep(1)
                        + "[$text-muted]or[/]"
                        + sep(1)
                        + "[b $primary]esc[/]"
                        + sep(1)
                        + "[$text-muted]to close   ·   type[/]"
                        + sep(1)
                        + "[b $primary]^p[/]"
                        + sep(1)
                        + "[$text-muted]to search every command by name[/]",
                        id="help-hint",
                    )

    def on_mount(self) -> None:
        self.query_one("#help-card").border_title = "◆  soap — keyboard reference"

    @staticmethod
    def _row(k: str, desc: str) -> str:
        return f"[b $primary]{k}[/]" + sep(max(1, 12 - len(k))) + f"[$foreground]{desc}[/]"


class SoapApp(App):
    CSS_PATH = "app.tcss"
    ENABLE_COMMAND_PALETTE = True

    # Panes cycled by h/l and tab.
    PANES = ("#sidebar", "#doclist", "#detail")

    BINDINGS = [
        Binding("o", "open", "open", show=False),
        Binding("enter", "open", "open", show=False),
        Binding("slash", "search", "search", show=False, key_display="/"),
        Binding("r", "review", "review inbox", show=False),
        Binding("t", "edit_tags", "edit tags", show=False),
        Binding("e", "edit_metadata", "edit metadata", show=False),
        Binding("question_mark", "help", "help", show=False, key_display="?"),
        Binding("tab", "focus_pane(1)", "Next pane", show=False),
        Binding("shift+tab", "focus_pane(-1)", "Prev pane", show=False),
        Binding("l", "focus_pane(1)", "Focus right", show=False),
        Binding("h", "focus_pane(-1)", "Focus left", show=False),
        Binding("ctrl+r", "refresh_data", "refresh", show=False),
        Binding("ctrl+t", "cycle_theme", "theme", show=False),
        Binding("q", "quit", "quit", show=False),
    ]

    def __init__(self, library: Library, editor_runner=None) -> None:
        super().__init__()
        self.library = library
        # Injectable for tests; the default is the shared VISUAL/EDITOR/vi
        # runner used by the library and inbox review surfaces.
        self.editor_runner = editor_runner
        self.config = load_config(library.path)
        self.docs: DocumentService | None = None
        self.filter_kind = "all"
        self.filter_value: str | None = None
        self.search_term = ""
        self._inbox = 0
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
            yield Static("", id="cheatbar")
            return

        with Horizontal(id="topbar"):
            yield Static("◆  soap", id="logo")
            yield SearchInput(
                placeholder="/  search title, author, tag, doi…", id="search"
            )
            yield Static("", id="inboxpill")
        with Horizontal(id="body"):
            yield Sidebar(id="sidebar")
            yield DocumentList(id="doclist")
            yield DetailPane(id="detail")
        yield Static("", id="cheatbar")

    def on_mount(self) -> None:
        self._register_themes()
        if not self._initialized:
            return
        self.query_one("#sidebar").border_title = "BROWSE"
        self.query_one("#detail").border_title = "DETAIL"
        self.docs = DocumentService.open(self.library.db_path)
        self.refresh_data()
        # Focus the list, not the search box (which is first in DOM order), so
        # j/k browse immediately and the footer shows the full command set.
        self.query_one(DocumentList).focus()

    def _register_themes(self) -> None:
        """Register bundled + user themes and select the startup theme."""
        for theme in BUNDLED_THEMES:
            self.register_theme(theme)
        user_themes, warnings = load_user_themes(self.library.path)
        for theme in user_themes:
            self.register_theme(theme)

        wanted = self.config.theme
        self.theme = wanted if wanted in self.available_themes else DEFAULT_THEME
        # Persist any later change (ctrl+t or the ctrl+p palette picker).
        self.watch(self, "theme", self._persist_theme, init=False)

        for warning in warnings:
            self.notify(f"theme: {warning}", severity="warning", timeout=8)

    def _persist_theme(self, theme_name: str) -> None:
        save_theme(self.library.path, theme_name)

    def on_unmount(self) -> None:
        if self.docs is not None:
            self.docs.close()

    # -- data flow ---------------------------------------------------------

    def refresh_data(self) -> None:
        """Rebuild sidebar + inbox pill + footer + list from the database."""
        if self.docs is None:
            return
        counts = self.docs.library_counts()
        self.query_one(Sidebar).build(
            counts, self.docs.tag_counts(), self.docs.collection_counts()
        )
        self._inbox = counts["inbox"]
        self._update_inbox()
        self._populate_list()

    def _update_inbox(self) -> None:
        pill = self.query_one("#inboxpill", Static)
        if self._inbox:
            pill.display = True
            pill.update(
                f"[b $accent]⚑[/]"
                + sep(2)
                + f"[b $accent]{self._inbox} in inbox — press r[/]"
            )
        else:
            pill.display = False
        self.query_one("#cheatbar", Static).update(_cheatbar(self._inbox))

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
        if self.filter_kind == "tag" and self.filter_value:
            title = f"# {self.filter_value}"
        elif self.filter_kind == "collection" and self.filter_value:
            title = self.filter_value
        else:
            title = _FILTER_TITLES.get(self.filter_kind, "Documents")
        if self.search_term:
            title += f' · /{self.search_term}'
        doclist.border_title = f"{title} · {len(rows)}"
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

    @on(DataTable.RowHighlighted, "#doclist")
    def _doc_moved(self, event: DataTable.RowHighlighted) -> None:
        self._show_detail(event.row_key.value if event.row_key else None)

    @on(DataTable.RowSelected, "#doclist")
    def _doc_selected(self, event: DataTable.RowSelected) -> None:
        self.action_open()

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
        if doc is None:
            return
        if doc.files:
            target = self.library.path / doc.files[0].path
            if not target.exists():
                self.notify(f"file missing on disk: {target}", severity="error")
                return
            self._launch(target)
        elif doc.url:
            self._launch(doc.url)
        else:
            self.notify("no file attached to this document", severity="warning")
            return

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

    def action_edit_metadata(self) -> None:
        """Edit the selected filed document's on-disk metadata in an editor."""
        doc_id = self.query_one(DocumentList).current_id
        if self.docs is None or doc_id is None:
            return
        if self.docs.get_document(doc_id) is None:
            return
        try:
            # Textual must release the terminal before a full-screen editor can
            # take it over. Validation happens before edit_document writes or
            # re-indexes, so an invalid saved file remains available to the user.
            with self.suspend():
                edit_document(
                    self.library, doc_id, self.docs,
                    editor_runner=self.editor_runner,
                )
        except Exception as exc:  # noqa: BLE001 - editor/YAML errors are user input
            self.notify(
                f"metadata edit not applied ({exc})",
                severity="error",
                timeout=8,
            )
            return
        self.refresh_data()
        # refresh_data rebuilds the table; restore the edited row and detail pane
        # rather than leaving the cursor on the first document.
        doclist = self.query_one(DocumentList)
        ids = getattr(doclist, "_ids", [])
        if doc_id in ids:
            doclist.move_cursor(row=ids.index(doc_id))
        self._show_detail(doc_id)
        self.notify("metadata saved")

    def action_edit_tags(self) -> None:
        doc_id = self.query_one(DocumentList).current_id
        if self.docs is None or doc_id is None:
            return
        doc = self.docs.get_document(doc_id)
        if doc is None:
            return
        self.push_screen(
            TagEditScreen(self.library, self.docs, doc),
            lambda saved: self._after_tag_edit(doc_id, saved),
        )

    def _after_tag_edit(self, doc_id: str, saved: list[str] | None) -> None:
        # None = cancelled / no change; a list = the persisted tag set.
        if saved is None:
            return
        n = len(saved)
        self.notify(f"tags saved — {n} tag{'s' if n != 1 else ''}")
        # Rebuild the sidebar tag counts + list, then re-show this document so its
        # chips reflect the new set (the cursor position is preserved).
        self.refresh_data()
        self._show_detail(doc_id)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_cycle_theme(self) -> None:
        names = [t.name for t in BUNDLED_THEMES] + [
            n
            for n in self.available_themes
            if n not in {t.name for t in BUNDLED_THEMES}
        ]
        try:
            i = names.index(self.theme)
        except ValueError:
            i = -1
        self.theme = names[(i + 1) % len(names)]
        self.notify(f"theme: {self.theme}")

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _launch(target: Path | str) -> None:
        """Open a file or URL with the OS default handler, non-blocking."""
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        elif os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(target)])


def run(library: Library) -> None:
    SoapApp(library).run()
