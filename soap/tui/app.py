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

from soap.bibtex import serialize_documents

from textual import on
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Center, Horizontal, Middle
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, ListView, Static

from soap.config import load_config, save_theme
from soap.db.documents import DocumentService
from soap.library import (
    Library,
    delete_document,
    edit_document,
    resolve_file_ref_path,
    save_document,
    set_read_status,
)
from soap.models.document import Document, ReadStatus
from soap.tui._markup import key, sep
from soap.tui.confirm import ConfirmBulkDeleteScreen, ConfirmDeleteScreen
from soap.tui.edit import EditScreen
from soap.tui.export import ExportDestinationScreen, ExportScopeScreen
from soap.tui.review import ReviewScreen
from soap.tui.tags import BulkTagScreen, TagEditScreen
from soap.tui.themes import BUNDLED_THEMES, DEFAULT_THEME, load_user_themes
from soap.tui.widgets import DetailPane, DocumentList, Sidebar, SidebarRow

# Human labels for the list pane's border title, per sidebar filter kind.
_FILTER_TITLES = {
    "all": "All documents",
    "inbox": "Inbox",
    "toread": "To read",
    "reading": "Reading",
}


def _cheatbar() -> str:
    """The persistent footer: a compact set of the core bulk-first concepts.

    Deliberately reduced to seven concepts — the full key reference (``?``) and
    the command palette (``^p``) stay discoverable but off the bar. Export appears
    exactly once. ``space`` selects rows so ``E``/``t``/``m``/``x`` then act on the
    selection (or the single row under the cursor when nothing is marked).
    """
    return sep(3).join(
        [
            key("space", "select"),
            key("E", "edit"),
            key("t", "tag"),
            key("m", "read"),
            key("x", "export"),
            key("tab", "pane"),
            key("?", "keys"),
        ]
    )


class SearchInput(Input):
    """The header search box and its explicit handoff-to-list interactions."""

    BINDINGS = [
        Binding("enter", "accept", "Accept search", show=False),
        Binding("down", "handoff", "Focus list", show=False),
        Binding("tab", "handoff", "Focus list", show=False),
        Binding("escape", "cancel", "Clear", show=False),
    ]

    def _focus_list(self) -> None:
        self.app.query_one(DocumentList).focus()

    def action_accept(self) -> None:
        """Accept the query without changing the live filter."""
        self._focus_list()

    def action_handoff(self) -> None:
        self._focus_list()

    def action_cancel(self) -> None:
        self.value = ""  # fires Changed -> app clears the search filter
        self._focus_list()


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
                ("E", "edit fields"),
                ("e", "edit YAML ($EDITOR)"),
                ("d", "delete document"),
                ("m", "cycle read status"),
                ("space", "mark / unmark"),
                ("u", "clear selection"),
                ("x", "export BibTeX"),
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
        Binding("E", "edit_fields", "edit fields", show=False),
        Binding("d", "delete", "delete", show=False),
        Binding("m", "cycle_read_status", "cycle read status", show=False),
        Binding("space", "toggle_mark", "mark", show=False),
        Binding("u", "clear_selection", "clear selection", show=False),
        Binding("x", "export", "export BibTeX", show=False),
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
        # ``ansi_color=True`` keeps Textual's ANSI→truecolor filter off so the
        # ``ansi_default`` root background in app.tcss survives to the terminal
        # as SGR 49 (terminal-default background) instead of being flattened to
        # an opaque RGB fill — that is what lets a transparent terminal window
        # show through the app. Truecolor theme colors (panes/borders/text) are
        # unaffected; only ANSI-named colors pass through verbatim.
        super().__init__(ansi_color=True)
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

    def refresh_data(self, selected_id: str | None = None) -> None:
        """Rebuild sidebar + inbox pill + footer + list from the database."""
        if self.docs is None:
            return
        counts = self.docs.library_counts()
        sidebar = self.query_one(Sidebar)
        sidebar.build(counts, self.docs.tag_counts(), self.docs.collection_counts())
        # Rebuilding counts must not silently reset the active filter to All.
        for index, item in enumerate(sidebar.children):
            if (
                isinstance(item, SidebarRow)
                and item.kind == self.filter_kind
                and item.value == self.filter_value
            ):
                sidebar.index = index
                break
        self._inbox = counts["inbox"]
        self._update_inbox()
        self._populate_list(selected_id=selected_id)

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
        self.query_one("#cheatbar", Static).update(_cheatbar())

    def _populate_list(self, selected_id: str | None = None) -> None:
        if self.docs is None:
            return
        rows = self.docs.list_documents(
            filter_kind=self.filter_kind,
            filter_value=self.filter_value,
            search=self.search_term or None,
        )
        doclist = self.query_one(DocumentList)
        if selected_id is None:
            selected_id = doclist.current_id
        doclist.populate(rows, selected_id=selected_id)
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
            try:
                target = resolve_file_ref_path(self.library, doc.id, doc.files[0])
            except (OSError, ValueError) as exc:
                self.notify(f"unsafe file reference: {exc}", severity="error")
                return
            if not target.exists():
                self.notify(f"file missing on disk: {target}", severity="error")
                return
            self._launch(target)
        elif doc.url:
            self._launch(doc.url)
        else:
            self.notify("no file attached to this document", severity="warning")
            return

    def action_cycle_read_status(self) -> None:
        """Cycle the selected document unread -> reading -> read."""
        doclist = self.query_one(DocumentList)
        doc_id = doclist.current_id
        if self.docs is None or doc_id is None:
            return
        document = self.docs.get_document(doc_id)
        if document is None:
            return
        statuses = (
            ReadStatus.UNREAD.value,
            ReadStatus.READING.value,
            ReadStatus.READ.value,
        )
        next_status = statuses[(statuses.index(document.read_status) + 1) % len(statuses)]
        set_read_status(self.library, doc_id, next_status, self.docs)
        self.refresh_data(selected_id=doc_id)
        self._show_detail(doc_id)
        self.notify(f"marked {next_status}")

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

    def action_edit_fields(self) -> None:
        """Edit the selected document's core fields in an in-app form.

        The quick, keyboard-first counterpart to ``e`` → ``$EDITOR``: reuses the
        review flow's inline correction core (``prompt_fields``) so the citekey is
        pinned (never a folder rename) and persistence goes through
        ``save_document``. ``e`` remains the full-YAML power option.
        """
        doc_id = self.query_one(DocumentList).current_id
        if self.docs is None or doc_id is None:
            return
        doc = self.docs.get_document(doc_id)
        if doc is None:
            return
        self.push_screen(
            EditScreen(self.library, self.docs, doc),
            lambda saved: self._after_edit_fields(doc_id, saved),
        )

    def _after_edit_fields(self, doc_id: str, saved: str | None) -> None:
        # None = cancelled / not saved; a str = the (pinned) document id.
        if saved is None:
            return
        self.refresh_data(selected_id=doc_id)
        # refresh_data rebuilds the table; keep the cursor on the edited row.
        doclist = self.query_one(DocumentList)
        ids = getattr(doclist, "_ids", [])
        if doc_id in ids:
            doclist.move_cursor(row=ids.index(doc_id))
        self._show_detail(doc_id)
        self.notify("metadata saved")

    def action_delete(self) -> None:
        """Delete the marked documents (bulk) or the single cursor document.

        With rows marked, one count-aware confirmation gates deleting the whole
        selection; with nothing marked, the existing single-document delete is
        unchanged. Cancelling makes no change in either case.
        """
        if self.docs is None:
            return
        doclist = self.query_one(DocumentList)
        if doclist.marked:
            ids = [i for i in doclist._ids if i in doclist.marked]
            docs = [self.docs.get_document(i) for i in ids]
            documents = [d for d in docs if d is not None]
            if not documents:
                return
            self.push_screen(
                ConfirmBulkDeleteScreen(documents),
                lambda confirmed: self._after_confirm_bulk_delete(
                    [d.id for d in documents], confirmed
                ),
            )
            return
        doc_id = doclist.current_id
        if doc_id is None:
            return
        doc = self.docs.get_document(doc_id)
        if doc is None:
            return
        self.push_screen(
            ConfirmDeleteScreen(doc),
            lambda confirmed: self._after_confirm_delete(doc_id, confirmed),
        )

    def _after_confirm_bulk_delete(
        self, ids: list[str], confirmed: bool | None
    ) -> None:
        if not confirmed or self.docs is None:
            return
        deleted = 0
        failed: list[str] = []
        for doc_id in ids:
            try:
                delete_document(self.library, doc_id, self.docs)
                deleted += 1
            except Exception:  # noqa: BLE001 - collect, report, keep going
                failed.append(doc_id)
        # The selection is consumed by the bulk action; deleted ids are gone and
        # any that failed stay in the library but the marks are cleared so the
        # next action starts from a clean selection.
        self.query_one(DocumentList).clear_marks()
        self.refresh_data()
        doclist = self.query_one(DocumentList)
        self._show_detail(doclist.current_id)
        if failed:
            self.notify(
                f"deleted {deleted}, {len(failed)} failed: {', '.join(failed)}",
                severity="error",
                timeout=8,
            )
        else:
            self.notify(f"deleted {deleted} document{'s' if deleted != 1 else ''}")

    def _after_confirm_delete(self, doc_id: str, confirmed: bool | None) -> None:
        if not confirmed or self.docs is None:
            return
        try:
            delete_document(self.library, doc_id, self.docs)
        except Exception as exc:  # noqa: BLE001 - surface, never crash the app
            self.notify(f"could not delete {doc_id}: {exc}", severity="error")
            return
        # Rebuild the list + sidebar tag/status counts; the deleted row is gone,
        # so let the list settle on whatever now sits under the cursor.
        self.refresh_data()
        doclist = self.query_one(DocumentList)
        self._show_detail(doclist.current_id)
        self.notify("document deleted")

    def action_edit_tags(self) -> None:
        """Edit tags: additive bulk-tag for the selection, or the single editor.

        With rows marked, ``t`` opens the additive bulk-tag flow (entered tags are
        unioned onto each marked document, existing tags kept). With nothing
        marked, the single-document tag editor is unchanged.
        """
        if self.docs is None:
            return
        doclist = self.query_one(DocumentList)
        if doclist.marked:
            ids = [i for i in doclist._ids if i in doclist.marked]
            self.push_screen(
                BulkTagScreen(self.docs, len(ids)),
                lambda tags: self._after_bulk_tag(ids, tags),
            )
            return
        doc_id = doclist.current_id
        if doc_id is None:
            return
        doc = self.docs.get_document(doc_id)
        if doc is None:
            return
        self.push_screen(
            TagEditScreen(self.library, self.docs, doc),
            lambda saved: self._after_tag_edit(doc_id, saved),
        )

    def _after_bulk_tag(self, ids: list[str], tags: list[str] | None) -> None:
        # None / empty = cancelled or nothing entered → no change.
        if not tags or self.docs is None:
            return
        updated = 0
        failed: list[str] = []
        for doc_id in ids:
            doc = self.docs.get_document(doc_id)
            if doc is None:
                failed.append(doc_id)
                continue
            # Additive union: keep existing tags, add the new ones. Model
            # normalization (lowercase/sort/dedupe) collapses any overlap.
            merged = sorted({*doc.tags, *tags})
            if merged == sorted(doc.tags):
                continue  # nothing new for this document
            data = doc.model_dump(mode="json")
            data["tags"] = merged
            try:
                save_document(self.library, Document(**data), self.docs)
                updated += 1
            except Exception:  # noqa: BLE001 - collect, report, keep going
                failed.append(doc_id)
        # The bulk action consumes the selection; refresh sidebar tag counts + list.
        self.query_one(DocumentList).clear_marks()
        self.refresh_data()
        self._show_detail(self.query_one(DocumentList).current_id)
        n = len(tags)
        base = f"tagged {updated} document{'s' if updated != 1 else ''}"
        if failed:
            self.notify(
                f"{base}, {len(failed)} failed: {', '.join(failed)}",
                severity="error",
                timeout=8,
            )
        else:
            self.notify(f"{base} · +{n} tag{'s' if n != 1 else ''}")

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

    def action_toggle_mark(self) -> None:
        """Mark/unmark the current row for a bulk action (e.g. export)."""
        doclist = self.query_one(DocumentList)
        doc_id = doclist.current_id
        if doc_id is None:
            return
        state = doclist.toggle_mark(doc_id)
        if state is None:
            return
        # Re-render the row's marker and keep the cursor where it was, then step
        # down so space-space-space marks a run — matching mc/ranger muscle memory.
        # Marking stays quiet: the row markers are feedback enough, so a run of
        # selections never spams a toast.
        row = doclist.cursor_row
        self._populate_list(selected_id=doc_id)
        if row + 1 < doclist.row_count:
            doclist.move_cursor(row=row + 1)

    def action_clear_selection(self) -> None:
        """Unselect all marked rows at once. Quiet, and a no-op when nothing is marked."""
        doclist = self.query_one(DocumentList)
        if not doclist.marked:
            return  # harmless no-op
        current = doclist.current_id
        doclist.clear_marks()
        # Re-render so the markers disappear — that vanishing is the only feedback;
        # clearing stays quiet, exactly like marking (no selection-count toast).
        self._populate_list(selected_id=current)

    def action_export(self) -> None:
        """Export library records to a BibTeX file, after a scope + destination choice."""
        if self.docs is None:
            return
        doclist = self.query_one(DocumentList)
        selected = len(doclist.marked)
        filtered = len(doclist._ids)
        total = self.docs.library_counts()["all"]
        self.push_screen(
            ExportScopeScreen(
                selected_count=selected,
                filtered_count=filtered,
                all_count=total,
            ),
            self._after_export_scope,
        )

    def _after_export_scope(self, scope: str | None) -> None:
        if scope is None or self.docs is None:
            return
        ids = self._ids_for_scope(scope)
        if not ids:
            self.notify("nothing to export", severity="warning")
            return
        default = "soap-selected.bib" if scope == "selected" else "soap-library.bib"
        self.push_screen(
            ExportDestinationScreen(
                count=len(ids), default_path=default, cwd=Path.cwd()
            ),
            lambda path: self._after_export_destination(ids, path),
        )

    def _ids_for_scope(self, scope: str) -> list[str]:
        """Resolve a scope choice to the concrete document ids to export."""
        doclist = self.query_one(DocumentList)
        if scope == "selected":
            # Preserve the current list order for the marked subset.
            return [i for i in doclist._ids if i in doclist.marked]
        if scope == "filtered":
            return list(doclist._ids)
        # "all" — every library record, independent of the active filter.
        if self.docs is None:
            return []
        return [r.id for r in self.docs.list_documents(filter_kind="all")]

    def _after_export_destination(self, ids: list[str], path: str | None) -> None:
        if path is None or self.docs is None:
            return
        # Hydrate the chosen documents read-only through the service layer; export
        # never mutates the library or touches the network.
        docs = [self.docs.get_document(i) for i in ids]
        documents = [d for d in docs if d is not None]
        result = serialize_documents(documents)
        # ``path`` is already the fully resolved absolute destination (the modal
        # resolved cwd/~/suffix), so what we write matches the preview exactly.
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(result.text, encoding="utf-8")
        except OSError as exc:
            self.notify(f"could not write {target}: {exc}", severity="error", timeout=8)
            return
        n = result.count
        msg = f"exported {n} record{'s' if n != 1 else ''} → {target}"
        skipped = len(result.skipped_ids)
        if skipped:
            self.notify(
                f"{msg} · {skipped} skipped (incomplete metadata)",
                severity="warning",
                timeout=8,
            )
        else:
            self.notify(msg)

    def get_system_commands(self, screen):
        """Surface the export action in the ``ctrl+p`` command palette too."""
        yield from super().get_system_commands(screen)
        if self._initialized:
            yield SystemCommand(
                "Export BibTeX",
                "Export selected, filtered, or all records to a .bib file",
                self.action_export,
            )
            yield SystemCommand(
                "Clear selection",
                "Unselect all marked documents",
                self.action_clear_selection,
            )

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
