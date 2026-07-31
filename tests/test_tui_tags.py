"""Pilot tests for TUI tag editing, autocomplete, and the sidebar tag filter.

Covers the three shipped capabilities: editing a document's tags from the
``TagEditScreen`` and persisting through ``save_document`` (survives a reload
from ``info.yaml``), autocomplete drawn from the existing tag vocabulary, and
filtering the document list by a sidebar tag — including its AND-composition with
the ``/`` search box. Async runs via ``asyncio.run`` so no plugin is needed.
"""

import asyncio

from soap.db.documents import DocumentService
from soap.ingest.merge import Overrides
from soap.library import add, load_document
from soap.tui.app import SoapApp
from soap.tui.tags import TagEditScreen
from soap.tui.widgets import DocumentList, Sidebar, SidebarRow

from textual.widgets import Input


def _seed(library, make_pdf, name: str, tags: list[str] | None = None) -> str:
    outcome = add(
        library,
        str(make_pdf(name)),
        fetch=False,
        overrides=Overrides(tags=tags or []),
    )
    assert outcome.status == "added"
    return outcome.citekey


def _drive_tag_screen(library, doc_id, coro_factory):
    """Open a fresh TagEditScreen over ``doc_id`` and run a pilot coroutine."""

    async def main():
        app = SoapApp(library)
        async with app.run_test() as pilot:
            app.docs = DocumentService.open(library.db_path)
            doc = app.docs.get_document(doc_id)
            screen = TagEditScreen(library, app.docs, doc)
            await app.push_screen(screen)
            await pilot.pause()
            try:
                await coro_factory(pilot, app, screen)
            finally:
                app.docs.close()

    asyncio.run(main())


def _drive_app(library, coro_factory):
    """Run a pilot coroutine against the full app (sidebar + list wired)."""

    async def main():
        app = SoapApp(library)
        async with app.run_test() as pilot:
            await pilot.pause()
            await coro_factory(pilot, app)

    asyncio.run(main())


# -- editing + persistence -------------------------------------------------


def test_add_tag_persists_to_disk(library, make_pdf):
    doc_id = _seed(library, make_pdf, "a.pdf", tags=["ml"])

    async def check(pilot, app, screen):
        screen.query_one("#tag-input", Input).value = "nlp"
        await pilot.press("enter")  # commit the typed token as a chip
        await pilot.pause()
        assert screen.tags == ["ml", "nlp"]
        await pilot.press("ctrl+s")  # save + dismiss
        await pilot.pause()

    _drive_tag_screen(library, doc_id, check)
    # Reload from info.yaml: the tag survived (model sorts + lowercases).
    assert load_document(library, doc_id).tags == ["ml", "nlp"]


def test_backspace_on_empty_drops_last_chip(library, make_pdf):
    doc_id = _seed(library, make_pdf, "a.pdf", tags=["ml", "nlp"])

    async def check(pilot, app, screen):
        assert screen.tags == ["ml", "nlp"]
        assert screen.query_one("#tag-input", Input).value == ""
        await pilot.press("backspace")  # empty field -> drop last chip
        await pilot.pause()
        assert screen.tags == ["ml"]
        await pilot.press("ctrl+s")
        await pilot.pause()

    _drive_tag_screen(library, doc_id, check)
    assert load_document(library, doc_id).tags == ["ml"]


def test_comma_commits_token_live(library, make_pdf):
    doc_id = _seed(library, make_pdf, "a.pdf")

    async def check(pilot, app, screen):
        box = screen.query_one("#tag-input", Input)
        box.value = "vision"
        box.value = "vision,"  # trailing comma commits the token
        await pilot.pause()
        assert screen.tags == ["vision"]
        assert box.value == ""
        await pilot.press("ctrl+s")
        await pilot.pause()

    _drive_tag_screen(library, doc_id, check)
    assert load_document(library, doc_id).tags == ["vision"]


def test_cancel_leaves_tags_untouched(library, make_pdf):
    doc_id = _seed(library, make_pdf, "a.pdf", tags=["ml"])

    async def check(pilot, app, screen):
        screen.query_one("#tag-input", Input).value = "throwaway"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("escape")  # cancel — nothing persisted
        await pilot.pause()

    _drive_tag_screen(library, doc_id, check)
    assert load_document(library, doc_id).tags == ["ml"]


# -- autocomplete ----------------------------------------------------------


def test_tab_completes_from_vocabulary(library, make_pdf):
    _seed(library, make_pdf, "a.pdf", tags=["machine-learning"])
    target = _seed(library, make_pdf, "b.pdf")

    async def check(pilot, app, screen):
        assert "machine-learning" in screen.vocab
        screen.query_one("#tag-input", Input).value = "mach"
        await pilot.pause()
        assert screen._matches()[0] == "machine-learning"
        await pilot.press("tab")  # accept the top suggestion
        await pilot.pause()
        assert screen.tags == ["machine-learning"]
        await pilot.press("ctrl+s")
        await pilot.pause()

    _drive_tag_screen(library, target, check)
    assert load_document(library, target).tags == ["machine-learning"]


def test_suggestions_exclude_already_added(library, make_pdf):
    doc_id = _seed(library, make_pdf, "a.pdf", tags=["ml"])
    _seed(library, make_pdf, "b.pdf", tags=["ml", "nlp"])

    async def check(pilot, app, screen):
        # "ml" is already on this doc, so it must not be offered as a suggestion.
        assert "ml" not in screen._matches()
        assert "nlp" in screen._matches()

    _drive_tag_screen(library, doc_id, check)


# -- sidebar tag filter ----------------------------------------------------


def _select_tag(app, name: str) -> None:
    sidebar = app.query_one(Sidebar)
    for i, item in enumerate(sidebar.children):
        if isinstance(item, SidebarRow) and item.kind == "tag" and item.value == name:
            sidebar.index = i
            return
    raise AssertionError(f"no sidebar row for tag {name!r}")


def test_sidebar_tag_filters_list(library, make_pdf):
    a = _seed(library, make_pdf, "a.pdf", tags=["ml"])
    _seed(library, make_pdf, "b.pdf", tags=["nlp"])

    async def check(pilot, app):
        _select_tag(app, "ml")
        await pilot.pause()
        assert app.filter_kind == "tag"
        assert app.filter_value == "ml"
        doclist = app.query_one(DocumentList)
        assert doclist.row_count == 1
        assert doclist.current_id == a
        assert "# ml" in str(doclist.border_title)

    _drive_app(library, check)


def test_tag_filter_composes_with_search(library, make_pdf):
    _seed(library, make_pdf, "alpha.pdf", tags=["ml"])
    _seed(library, make_pdf, "beta.pdf", tags=["ml"])

    async def check(pilot, app):
        _select_tag(app, "ml")
        await pilot.pause()
        assert app.query_one(DocumentList).row_count == 2
        # AND the text search on top of the tag filter.
        app.search_term = "alpha"
        app._populate_list()
        await pilot.pause()
        assert app.query_one(DocumentList).row_count == 1

    _drive_app(library, check)
