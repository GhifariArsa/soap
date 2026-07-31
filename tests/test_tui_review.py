"""Pilot tests for the TUI ReviewScreen editable form.

Drives the modal through Textual's test pilot to confirm the headline behaviour:
the core fields render prefilled, the modal starts in command mode (so a/e/s/q
act instead of typing), correcting a field and filing persists through
``save_document`` with the citekey pinned. Async is run via ``asyncio.run`` so no
pytest-asyncio plugin is needed.
"""

import asyncio

from soap.db.documents import DocumentService
from soap.library import add, load_document
from soap.tui.app import SoapApp
from soap.tui.review import ReviewScreen

from textual.widgets import Input


def _seed(library, make_pdf, name: str) -> str:
    outcome = add(library, str(make_pdf(name)), fetch=False)
    assert outcome.status == "added"
    return outcome.citekey


def _drive(library, coro_factory):
    """Run a pilot coroutine against a fresh ReviewScreen over the inbox."""

    async def main():
        app = SoapApp(library)
        async with app.run_test() as pilot:
            app.docs = DocumentService.open(library.db_path)
            ids = [r.id for r in app.docs.list_documents(filter_kind="inbox")]
            screen = ReviewScreen(library, app.docs, ids)
            await app.push_screen(screen)
            await pilot.pause()
            try:
                await coro_factory(pilot, app, screen, ids)
            finally:
                app.docs.close()

    asyncio.run(main())


def test_form_prefills_and_starts_in_command_mode(library, make_pdf):
    _seed(library, make_pdf, "attention_is_all_you_need.pdf")

    async def check(pilot, app, screen, ids):
        assert screen.query_one("#f-title", Input).value == "attention is all you need"
        assert screen.query_one("#f-year", Input).value == ""
        # command mode: nothing focused, so a single-letter action fires
        assert app.focused is None

    _drive(library, check)


def test_command_mode_accept_files_without_typing(library, make_pdf):
    doc_id = _seed(library, make_pdf, "a.pdf")

    async def check(pilot, app, screen, ids):
        await pilot.press("a")
        await pilot.pause()
        assert screen.filed == 1
        doc = load_document(library, doc_id)
        assert doc.review_status == "filed"
        # title untouched (the 'a' filed, it was not typed into the field)
        assert doc.title == "a"

    _drive(library, check)


def test_correct_then_enter_files_edit_pinned(library, make_pdf):
    doc_id = _seed(library, make_pdf, "draft.pdf")

    async def check(pilot, app, screen, ids):
        await pilot.press("c")  # enter edit mode
        await pilot.pause()
        assert app.focused is not None and app.focused.id == "f-title"
        screen.query_one("#f-title", Input).value = "Edited In TUI"
        screen.query_one("#f-year", Input).value = "2021"
        await pilot.press("enter")  # Input.Submitted -> file
        await pilot.pause()
        assert screen.filed == 1
        doc = load_document(library, doc_id)
        assert doc.title == "Edited In TUI"
        assert doc.year == 2021
        assert doc.review_status == "filed"
        assert doc.id == doc_id  # citekey pinned, folder not moved

    _drive(library, check)


def test_skip_leaves_item_queued(library, make_pdf):
    doc_id = _seed(library, make_pdf, "a.pdf")

    async def check(pilot, app, screen, ids):
        await pilot.press("s")
        await pilot.pause()
        assert screen.skipped == 1
        assert load_document(library, doc_id).review_status == "needs_review"

    _drive(library, check)
