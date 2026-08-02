"""Pilot tests for the browser's BibTeX export flow.

Drives the real ``SoapApp`` through Textual's test pilot: marking rows with
``space``, exporting the marked selection, exporting the current filtered set and
all records when nothing is marked, cancelling at each stage, and the result
summary / skip path. Async runs via ``asyncio.run`` — no pytest-asyncio plugin,
matching ``tests/test_tui_browser_edit_delete.py``.
"""

import asyncio

from pathlib import Path

from soap.library import add
from soap.tui.app import SoapApp
from soap.tui.export import (
    ExportDestinationScreen,
    ExportScopeScreen,
    resolve_export_path,
)
from soap.tui.widgets import DocumentList

from textual.widgets import Input, Static


def _seed(library, make_pdf, name: str) -> str:
    outcome = add(library, str(make_pdf(name)), fetch=False)
    assert outcome.status == "added"
    assert outcome.citekey is not None
    return outcome.citekey


def _drive(library, coro_factory):
    async def main():
        app = SoapApp(library)
        async with app.run_test() as pilot:
            await pilot.pause()
            await coro_factory(pilot, app)

    asyncio.run(main())


def _select(app, doc_id: str) -> None:
    doclist = app.query_one(DocumentList)
    ids = getattr(doclist, "_ids", [])
    assert doc_id in ids, f"{doc_id} not in list {ids}"
    doclist.move_cursor(row=ids.index(doc_id))


def test_space_marks_and_unmarks_row(library, make_pdf):
    a = _seed(library, make_pdf, "alpha.pdf")

    async def check(pilot, app):
        doclist = app.query_one(DocumentList)
        _select(app, a)
        await pilot.press("space")
        await pilot.pause()
        assert a in doclist.marked
        _select(app, a)
        await pilot.press("space")
        await pilot.pause()
        assert a not in doclist.marked

    _drive(library, check)


def test_export_selected_writes_only_marked(library, make_pdf, tmp_path):
    a = _seed(library, make_pdf, "alpha.pdf")
    b = _seed(library, make_pdf, "beta.pdf")
    _seed(library, make_pdf, "gamma.pdf")
    dest = tmp_path / "out.bib"

    async def check(pilot, app):
        doclist = app.query_one(DocumentList)
        _select(app, a)
        await pilot.press("space")
        _select(app, b)
        await pilot.press("space")
        await pilot.pause()
        assert doclist.marked == {a, b}

        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, ExportScopeScreen)
        await pilot.press("s")  # selected
        await pilot.pause()
        assert isinstance(app.screen, ExportDestinationScreen)
        app.screen.query_one("#export-dest", Input).value = str(dest)
        await pilot.press("ctrl+s")
        await pilot.pause()

        text = dest.read_text()
        # Both marked entries present, the third absent.
        assert f"{{{a}," in text
        assert f"{{{b}," in text
        assert "gamma" not in text

    _drive(library, check)


def test_export_filtered_when_nothing_marked(library, make_pdf, tmp_path):
    _seed(library, make_pdf, "alpha.pdf")
    _seed(library, make_pdf, "beta.pdf")
    dest = tmp_path / "filtered.bib"

    async def check(pilot, app):
        # Narrow the list to just "alpha" via search.
        app.search_term = "alpha"
        app._populate_list()
        await pilot.pause()
        assert app.query_one(DocumentList)._ids == ["alpha"] or app.query_one(
            DocumentList
        )._ids  # tolerate citekey form
        filtered_ids = list(app.query_one(DocumentList)._ids)

        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, ExportScopeScreen)
        # No selection → the "s" gesture must be inert; choose filtered.
        await pilot.press("f")
        await pilot.pause()
        assert isinstance(app.screen, ExportDestinationScreen)
        app.screen.query_one("#export-dest", Input).value = str(dest)
        await pilot.press("ctrl+s")
        await pilot.pause()

        text = dest.read_text()
        for i in filtered_ids:
            assert f"{{{i}," in text
        assert "beta" not in text

    _drive(library, check)


def test_export_all_when_nothing_marked(library, make_pdf, tmp_path):
    a = _seed(library, make_pdf, "alpha.pdf")
    b = _seed(library, make_pdf, "beta.pdf")
    dest = tmp_path / "all.bib"

    async def check(pilot, app):
        # Filter to a subset, but "all" must export the whole library.
        app.search_term = "alpha"
        app._populate_list()
        await pilot.pause()

        await pilot.press("x")
        await pilot.pause()
        await pilot.press("a")  # all records
        await pilot.pause()
        assert isinstance(app.screen, ExportDestinationScreen)
        app.screen.query_one("#export-dest", Input).value = str(dest)
        await pilot.press("ctrl+s")
        await pilot.pause()

        text = dest.read_text()
        assert f"{{{a}," in text
        assert f"{{{b}," in text

    _drive(library, check)


def test_export_scope_cancel_writes_nothing(library, make_pdf, tmp_path):
    _seed(library, make_pdf, "alpha.pdf")
    dest = tmp_path / "nope.bib"

    async def check(pilot, app):
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, ExportScopeScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, (ExportScopeScreen, ExportDestinationScreen))
        assert not dest.exists()

    _drive(library, check)


def test_export_destination_cancel_writes_nothing(library, make_pdf, tmp_path):
    _seed(library, make_pdf, "alpha.pdf")
    dest = tmp_path / "nope.bib"

    async def check(pilot, app):
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, ExportDestinationScreen)
        app.screen.query_one("#export-dest", Input).value = str(dest)
        await pilot.press("escape")
        await pilot.pause()
        assert not dest.exists()

    _drive(library, check)


def test_export_default_extension_and_summary(library, make_pdf, tmp_path):
    _seed(library, make_pdf, "alpha.pdf")

    async def check(pilot, app):
        dest_noext = tmp_path / "libout"
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#export-dest", Input).value = str(dest_noext)
        await pilot.press("ctrl+s")
        await pilot.pause()
        # A missing extension gets ".bib" appended.
        assert (tmp_path / "libout.bib").exists()

    _drive(library, check)


# --- destination resolution + preview -----------------------------------------


def test_resolve_export_path_relative_is_anchored_to_cwd():
    cwd = Path("/work/dir")
    assert resolve_export_path("out.bib", cwd) == Path("/work/dir/out.bib")
    # A subdirectory relative path is anchored too.
    assert resolve_export_path("sub/out.bib", cwd) == Path("/work/dir/sub/out.bib")


def test_resolve_export_path_absolute_is_unchanged():
    assert resolve_export_path("/tmp/x.bib", Path("/work/dir")) == Path("/tmp/x.bib")


def test_resolve_export_path_expands_tilde():
    resolved = resolve_export_path("~/x.bib", Path("/work/dir"))
    assert resolved == Path.home() / "x.bib"
    assert resolved.is_absolute()


def test_resolve_export_path_adds_bib_suffix_only_when_missing():
    cwd = Path("/work/dir")
    assert resolve_export_path("libout", cwd) == Path("/work/dir/libout.bib")
    # An existing (any) extension is preserved, not clobbered.
    assert resolve_export_path("data.json", cwd) == Path("/work/dir/data.json")
    assert resolve_export_path("refs.bib", cwd) == Path("/work/dir/refs.bib")


def test_destination_modal_shows_cwd_and_live_resolved_preview(library, make_pdf):
    _seed(library, make_pdf, "alpha.pdf")

    async def check(pilot, app):
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, ExportDestinationScreen)
        cwd = str(Path.cwd())
        # The modal states the actual current directory.
        body = app.screen.query_one("#export-dest-body", Static)
        assert cwd in body.render().plain
        # The default filename resolves to a preview under cwd.
        preview = app.screen.query_one("#export-dest-preview", Static)
        assert str(Path.cwd() / "soap-library.bib") in preview.render().plain
        # Editing the path updates the preview live, including the .bib suffix.
        app.screen.query_one("#export-dest", Input).value = "nested/refs"
        await pilot.pause()
        expected = str(Path.cwd() / "nested" / "refs.bib")
        assert expected in app.screen.query_one("#export-dest-preview", Static).render().plain

    _drive(library, check)


def test_destination_modal_preview_handles_absolute_and_tilde(library, make_pdf):
    _seed(library, make_pdf, "alpha.pdf")

    async def check(pilot, app):
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ExportDestinationScreen)
        preview = screen.query_one("#export-dest-preview", Static)
        # Absolute path shown verbatim.
        screen.query_one("#export-dest", Input).value = "/tmp/mine.bib"
        await pilot.pause()
        assert "/tmp/mine.bib" in preview.render().plain
        # Tilde expands in the preview.
        screen.query_one("#export-dest", Input).value = "~/refs.bib"
        await pilot.pause()
        assert str(Path.home() / "refs.bib") in preview.render().plain

    _drive(library, check)
