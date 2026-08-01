"""Pilot tests for the main browser's in-app edit form and delete confirm.

Drives the real ``SoapApp`` (not a hand-pushed modal) through Textual's test
pilot so the full wiring is exercised: ``E`` opens the in-app core-field form and
saving persists through ``save_document`` with the citekey pinned; ``d`` asks for
confirmation before ``delete_document`` removes the folder and its files and
refreshes the sidebar counts; cancelling the confirm leaves the document intact.
Async runs via ``asyncio.run`` — no pytest-asyncio plugin, matching
``tests/test_tui_review.py`` / ``tests/test_themes.py``.
"""

import asyncio

from soap.library import add, load_document
from soap.tui.app import SoapApp
from soap.tui.confirm import ConfirmDeleteScreen
from soap.tui.edit import EditScreen
from soap.tui.widgets import DocumentList

from textual.widgets import Input


def _seed(library, make_pdf, name: str) -> str:
    outcome = add(library, str(make_pdf(name)), fetch=False)
    assert outcome.status == "added"
    return outcome.citekey


def _drive(library, coro_factory):
    """Run a pilot coroutine against the real browser app over the library."""

    async def main():
        app = SoapApp(library)
        async with app.run_test() as pilot:
            await pilot.pause()
            await coro_factory(pilot, app)

    asyncio.run(main())


def _select(app, doc_id: str) -> None:
    """Put the list cursor on ``doc_id`` so a keystroke acts on it."""
    doclist = app.query_one(DocumentList)
    ids = getattr(doclist, "_ids", [])
    assert doc_id in ids, f"{doc_id} not in list {ids}"
    doclist.move_cursor(row=ids.index(doc_id))


# --- in-app edit form ---------------------------------------------------------


def test_edit_fields_form_persists_and_pins_citekey(library, make_pdf):
    doc_id = _seed(library, make_pdf, "draft.pdf")

    async def check(pilot, app):
        _select(app, doc_id)
        await pilot.press("E")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditScreen)
        # Prefilled from the current metadata.
        assert screen.query_one("#f-title", Input).value == "draft"
        # Edit the core fields, then save with ctrl+s.
        screen.query_one("#f-title", Input).value = "Edited On Browser"
        screen.query_one("#f-year", Input).value = "2021"
        screen.query_one("#f-venue", Input).value = "NeurIPS"
        await pilot.press("ctrl+s")
        await pilot.pause()
        # Persisted to info.yaml (source of truth) and reindexed, id pinned.
        doc = load_document(library, doc_id)
        assert doc.title == "Edited On Browser"
        assert doc.year == 2021
        assert doc.venue == "NeurIPS"
        assert doc.id == doc_id  # citekey pinned, folder not renamed
        # The index reflects the new title too.
        assert app.docs.get_document(doc_id).title == "Edited On Browser"

    _drive(library, check)


def test_edit_fields_cancel_leaves_metadata_unchanged(library, make_pdf):
    doc_id = _seed(library, make_pdf, "keep.pdf")

    async def check(pilot, app):
        _select(app, doc_id)
        await pilot.press("E")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditScreen)
        screen.query_one("#f-title", Input).value = "Should Not Save"
        await pilot.press("escape")
        await pilot.pause()
        assert load_document(library, doc_id).title == "keep"

    _drive(library, check)


# --- delete + confirm ---------------------------------------------------------


def test_delete_confirm_removes_document_files_and_counts(library, make_pdf):
    doc_id = _seed(library, make_pdf, "trash.pdf")
    other = _seed(library, make_pdf, "keeper.pdf")

    async def check(pilot, app):
        folder = library.documents / doc_id
        assert folder.exists()
        before = app.docs.library_counts()["all"]

        _select(app, doc_id)
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDeleteScreen)
        await pilot.press("y")  # confirm
        await pilot.pause()

        # Removed from disk (folder + files) and from the index.
        assert not folder.exists()
        assert app.docs.get_document(doc_id) is None
        assert app.docs.library_counts()["all"] == before - 1
        # The other document is untouched.
        assert (library.documents / other).exists()
        # The list refreshed to just the survivor.
        assert app.query_one(DocumentList)._ids == [other]

    _drive(library, check)


def test_delete_cancel_leaves_document_intact(library, make_pdf):
    doc_id = _seed(library, make_pdf, "safe.pdf")

    async def check(pilot, app):
        folder = library.documents / doc_id
        _select(app, doc_id)
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDeleteScreen)
        await pilot.press("n")  # cancel
        await pilot.pause()
        assert folder.exists()
        assert app.docs.get_document(doc_id) is not None

    _drive(library, check)
