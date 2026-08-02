"""The browser's BibTeX export flow: pick a scope, then a destination file.

Two small keyboard-first modals drive the export the app wires to the ``x`` key
(and the command palette). Neither touches the library — they only gather the
user's choices; the app hydrates the chosen documents through the normal service
layer and hands them to :func:`soap.bibtex.serialize_documents`, then writes the
returned text.

:class:`ExportScopeScreen` makes the scope explicit rather than silently
exporting the whole library: it offers the marked selection (when any rows are
marked), the current filtered results, and all library records. It dismisses with
the chosen scope string (``"selected"`` / ``"filtered"`` / ``"all"``) or ``None``
on cancel.

:class:`ExportDestinationScreen` collects the ``.bib`` path (prefilled with a
sensible default) and dismisses with the entered path or ``None`` on cancel.

Styling reuses the shared card / field slots in ``app.tcss`` — theme tokens only,
no hardcoded hex — and every label/value gap goes through
:func:`soap.tui._markup.sep`.
"""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Middle, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from pathlib import Path

from rich.markup import escape

from soap.tui._markup import key, sep


def resolve_export_path(raw: str, cwd: Path) -> Path:
    """Resolve a user-entered destination to the absolute path soap will write.

    The single source of truth for destination resolution, shared by the modal's
    live preview and the app's actual write so the two never drift. A relative
    name is anchored to ``cwd`` — the directory soap was launched from, not the
    library — ``~`` is expanded, and a missing extension gains ``.bib``.
    """
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = cwd / p
    if p.suffix == "":
        p = p.with_suffix(".bib")
    return p


_SCOPE_HINT = sep(2).join(
    [
        key("s", "selected", "$accent"),
        key("f", "filtered"),
        key("a", "all"),
        key("esc", "cancel", "$text-muted"),
    ]
)

_DEST_HINT = sep(2).join(
    [
        key("enter / ^s", "export", "$success"),
        key("esc", "cancel", "$text-muted"),
    ]
)


class ExportScopeScreen(ModalScreen[str | None]):
    """Choose which records to export. Dismisses a scope string or ``None``.

    ``selected_count`` is the number of marked rows (the ``selected`` option is
    only offered, and pre-selected, when it is non-zero); ``filtered_count`` and
    ``all_count`` size the other two scopes.
    """

    # No text entry here — start in command mode so the letter keys act at once.
    AUTO_FOCUS = None

    BINDINGS = [
        Binding("s", "pick_selected", "selected", show=True),
        Binding("f", "pick_filtered", "filtered", show=True),
        Binding("a", "pick_all", "all", show=True),
        Binding("escape", "cancel", "cancel", show=False),
    ]

    def __init__(
        self, *, selected_count: int, filtered_count: int, all_count: int
    ) -> None:
        super().__init__()
        self.selected_count = selected_count
        self.filtered_count = filtered_count
        self.all_count = all_count

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                with Vertical(id="export-card"):
                    yield Static("", id="export-body")
                    yield Static(self._hint(), id="export-hint")

    def _hint(self) -> str:
        # Drop the ``selected`` verb from the legend when nothing is marked.
        if self.selected_count:
            return _SCOPE_HINT
        return sep(2).join(
            [key("f", "filtered"), key("a", "all"), key("esc", "cancel", "$text-muted")]
        )

    def on_mount(self) -> None:
        self.query_one("#export-card").border_title = "⇩  EXPORT BibTeX"
        rows = []
        if self.selected_count:
            rows.append(
                "[b $accent]s[/]"
                + sep(2)
                + f"[$foreground]selected records[/]"
                + sep(1)
                + f"[$text-muted]({self.selected_count})[/]"
            )
        rows.append(
            "[b $primary]f[/]"
            + sep(2)
            + "[$foreground]current filtered results[/]"
            + sep(1)
            + f"[$text-muted]({self.filtered_count})[/]"
        )
        rows.append(
            "[b $primary]a[/]"
            + sep(2)
            + "[$foreground]all library records[/]"
            + sep(1)
            + f"[$text-muted]({self.all_count})[/]"
        )
        self.query_one("#export-body", Static).update(
            "[$text-muted]Choose what to export:[/]\n\n" + "\n".join(rows)
        )

    def action_pick_selected(self) -> None:
        # Ignore the gesture when there is nothing marked to export.
        if self.selected_count:
            self.dismiss("selected")

    def action_pick_filtered(self) -> None:
        self.dismiss("filtered")

    def action_pick_all(self) -> None:
        self.dismiss("all")

    def action_cancel(self) -> None:
        self.dismiss(None)


class ExportDestinationScreen(ModalScreen[str | None]):
    """Enter the ``.bib`` destination path. Dismisses the path or ``None``."""

    AUTO_FOCUS = "#export-dest"

    BINDINGS = [
        Binding("ctrl+s", "confirm", "export", show=True),
        Binding("escape", "cancel", "cancel", show=False),
    ]

    def __init__(self, *, count: int, default_path: str, cwd: Path) -> None:
        super().__init__()
        self.count = count
        self.default_path = default_path
        # The directory soap was launched from — relative names resolve against it
        # (injected so the preview is testable). The screen dismisses the fully
        # resolved absolute path, so what the preview shows is exactly what the
        # app writes.
        self.cwd = cwd

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                with Vertical(id="export-dest-card"):
                    yield Static("", id="export-dest-body")
                    with Horizontal(classes="field-row"):
                        yield Static("path", classes="field-label")
                        yield Input(
                            id="export-dest",
                            classes="field-input",
                            placeholder="library.bib",
                        )
                    yield Static("", id="export-dest-preview")
                    yield Static(_DEST_HINT, id="export-dest-hint")

    def on_mount(self) -> None:
        self.query_one("#export-dest-card").border_title = "⇩  EXPORT BibTeX"
        n = self.count
        self.query_one("#export-dest-body", Static).update(
            "[$foreground]Export[/]"
            + sep(1)
            + f"[b $foreground]{n}[/]"
            + sep(1)
            + f"[$foreground]record{'s' if n != 1 else ''} to a .bib file.[/]\n"
            + "[$text-muted]A relative name saves under the directory soap was "
            + "launched from:[/]\n"
            + f"[$text-muted]{escape(str(self.cwd))}[/]"
        )
        dest = self.query_one("#export-dest", Input)
        dest.value = self.default_path
        self._render_preview(self.default_path)

    def _render_preview(self, raw: str) -> None:
        preview = self.query_one("#export-dest-preview", Static)
        raw = raw.strip()
        if not raw:
            preview.update("[$text-muted]enter a filename above[/]")
            return
        resolved = resolve_export_path(raw, self.cwd)
        preview.update(
            "[$text-muted]saves to[/]"
            + sep(1)
            + f"[$accent]{escape(str(resolved))}[/]"
        )

    @on(Input.Changed, "#export-dest")
    def _dest_changed(self, event: Input.Changed) -> None:
        # Live preview: recompute the resolved absolute destination as the user types.
        self._render_preview(event.value)

    @on(Input.Submitted)
    def _on_submit(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_confirm()

    def action_confirm(self) -> None:
        raw = self.query_one("#export-dest", Input).value.strip()
        if not raw:
            self.app.notify("enter a destination path", severity="error")
            return
        # Dismiss the fully resolved absolute path so the app writes exactly what
        # the preview showed — no second, drift-prone resolution downstream.
        self.dismiss(str(resolve_export_path(raw, self.cwd)))

    def action_cancel(self) -> None:
        self.dismiss(None)
