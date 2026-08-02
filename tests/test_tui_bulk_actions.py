"""Pilot tests for the marked-selection bulk-action system and reduced footer.

Covers the captain-required revisions layered on top of BibTeX export: quiet
repeated selection (no toast), additive bulk tagging across several marked
documents, count-aware bulk delete with confirm + cancel, selection cleanup after
a bulk action, the preserved single-document behavior when nothing is marked, and
the reduced persistent footer. Async runs via ``asyncio.run`` — no pytest-asyncio
plugin, matching the other TUI pilot tests.
"""

import asyncio

from soap.library import add, load_document, save_document
from soap.models.document import Document
from soap.tui.app import SoapApp
from soap.tui.confirm import ConfirmBulkDeleteScreen, ConfirmDeleteScreen
from soap.tui.tags import BulkTagScreen, TagEditScreen
from soap.tui.widgets import DocumentList

from textual.widgets import Input


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


def _mark(pilot, app, *ids):
    async def go():
        for doc_id in ids:
            _select(app, doc_id)
            await pilot.press("space")
        await pilot.pause()

    return go()


# --- quiet selection ----------------------------------------------------------


def test_repeated_selection_is_quiet(library, make_pdf):
    a = _seed(library, make_pdf, "alpha.pdf")
    b = _seed(library, make_pdf, "beta.pdf")

    async def check(pilot, app):
        notifications = []
        orig = app.notify
        app.notify = lambda *a, **k: notifications.append((a, k))  # type: ignore
        try:
            await _mark(pilot, app, a, b, a)
        finally:
            app.notify = orig  # type: ignore
        # Marking/unmarking must never toast.
        assert notifications == []
        assert app.query_one(DocumentList).marked == {b}

    _drive(library, check)


# --- additive bulk tag --------------------------------------------------------


def test_bulk_tag_is_additive_across_marked(library, make_pdf):
    a = _seed(library, make_pdf, "alpha.pdf")
    b = _seed(library, make_pdf, "beta.pdf")

    async def check(pilot, app):
        # Give `a` a pre-existing tag through the app's real service so the bulk
        # add must union onto it rather than replace it.
        d = app.docs.get_document(a)
        data = d.model_dump(mode="json")
        data["tags"] = ["existing"]
        save_document(library, Document(**data), app.docs)
        app.refresh_data()
        await pilot.pause()

        await _mark(pilot, app, a, b)
        await pilot.press("t")
        await pilot.pause()
        assert isinstance(app.screen, BulkTagScreen)
        app.screen.query_one("#tag-input", Input).value = "shared, review"
        await pilot.press("ctrl+s")
        await pilot.pause()

        # Both documents gained the new tags; `a` kept its existing one.
        assert set(load_document(library, a).tags) == {"existing", "shared", "review"}
        assert set(load_document(library, b).tags) == {"shared", "review"}
        # Selection consumed after the bulk action.
        assert app.query_one(DocumentList).marked == set()

    _drive(library, check)


def test_single_tag_editor_when_nothing_marked(library, make_pdf):
    a = _seed(library, make_pdf, "alpha.pdf")

    async def check(pilot, app):
        _select(app, a)
        await pilot.press("t")
        await pilot.pause()
        # No marks → the single-document editor, not the bulk flow.
        assert isinstance(app.screen, TagEditScreen)
        await pilot.press("escape")

    _drive(library, check)


# --- bulk delete --------------------------------------------------------------


def test_bulk_delete_confirm_removes_all_marked(library, make_pdf):
    a = _seed(library, make_pdf, "alpha.pdf")
    b = _seed(library, make_pdf, "beta.pdf")
    keep = _seed(library, make_pdf, "gamma.pdf")

    async def check(pilot, app):
        before = app.docs.library_counts()["all"]
        await _mark(pilot, app, a, b)
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmBulkDeleteScreen)
        await pilot.press("y")
        await pilot.pause()

        assert not (library.documents / a).exists()
        assert not (library.documents / b).exists()
        assert (library.documents / keep).exists()
        assert app.docs.library_counts()["all"] == before - 2
        # Selection cleared, survivor still listed.
        assert app.query_one(DocumentList).marked == set()
        assert app.query_one(DocumentList)._ids == [keep]

    _drive(library, check)


def test_bulk_delete_cancel_changes_nothing(library, make_pdf):
    a = _seed(library, make_pdf, "alpha.pdf")
    b = _seed(library, make_pdf, "beta.pdf")

    async def check(pilot, app):
        await _mark(pilot, app, a, b)
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmBulkDeleteScreen)
        await pilot.press("n")
        await pilot.pause()

        assert (library.documents / a).exists()
        assert (library.documents / b).exists()
        # Cancel leaves the selection intact.
        assert app.query_one(DocumentList).marked == {a, b}

    _drive(library, check)


def test_single_delete_when_nothing_marked(library, make_pdf):
    a = _seed(library, make_pdf, "alpha.pdf")
    _seed(library, make_pdf, "beta.pdf")

    async def check(pilot, app):
        _select(app, a)
        await pilot.press("d")
        await pilot.pause()
        # No marks → the single-document confirm, not the bulk one.
        assert isinstance(app.screen, ConfirmDeleteScreen)
        assert not isinstance(app.screen, ConfirmBulkDeleteScreen)
        await pilot.press("n")

    _drive(library, check)


# --- reduced footer -----------------------------------------------------------


# --- unselect all -------------------------------------------------------------


def test_clear_selection_unmarks_all_quietly(library, make_pdf):
    a = _seed(library, make_pdf, "alpha.pdf")
    b = _seed(library, make_pdf, "beta.pdf")
    c = _seed(library, make_pdf, "gamma.pdf")

    async def check(pilot, app):
        await _mark(pilot, app, a, b, c)
        assert app.query_one(DocumentList).marked == {a, b, c}

        notifications = []
        orig = app.notify
        app.notify = lambda *a, **k: notifications.append((a, k))  # type: ignore
        try:
            await pilot.press("u")
            await pilot.pause()
        finally:
            app.notify = orig  # type: ignore

        # Every mark cleared, and clearing stayed quiet (no toast).
        assert app.query_one(DocumentList).marked == set()
        assert notifications == []

    _drive(library, check)


def test_clear_selection_is_noop_when_nothing_marked(library, make_pdf):
    a = _seed(library, make_pdf, "alpha.pdf")

    async def check(pilot, app):
        _select(app, a)
        assert app.query_one(DocumentList).marked == set()
        notifications = []
        orig = app.notify
        app.notify = lambda *a, **k: notifications.append((a, k))  # type: ignore
        try:
            await pilot.press("u")
            await pilot.pause()
        finally:
            app.notify = orig  # type: ignore
        # Harmless no-op: still nothing marked, nothing announced.
        assert app.query_one(DocumentList).marked == set()
        assert notifications == []

    _drive(library, check)


def test_clear_selection_does_not_fire_while_typing_in_search(library, make_pdf):
    # Titles both contain "u" so a "u" search keeps them visible — isolating the
    # question "did the u *action* fire?" from the separate filter-prunes-marks
    # behavior of populate().
    a = _seed(library, make_pdf, "ubuntu.pdf")
    b = _seed(library, make_pdf, "under.pdf")

    async def check(pilot, app):
        await _mark(pilot, app, a, b)
        assert app.query_one(DocumentList).marked == {a, b}
        # Focus the search box and type a "u" — it must land in the field as text,
        # not trigger the clear-selection action.
        app.query_one("#search", Input).focus()
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        assert app.query_one("#search", Input).value == "u"
        # Both documents still match the search, so if the u *action* had fired it
        # would have cleared these still-visible marks. They survive → it didn't.
        assert app.query_one(DocumentList).marked == {a, b}

    _drive(library, check)


def test_clear_selection_is_discoverable_in_help_and_palette(library, make_pdf):
    _seed(library, make_pdf, "alpha.pdf")

    async def check(pilot, app):
        # Command palette: the app surfaces a "Clear selection" system command.
        titles = [c.title for c in app.get_system_commands(app.screen)]
        assert "Clear selection" in titles
        # Full keyboard reference (?): the ACT column lists the u binding.
        from soap.tui.app import HelpScreen

        keys = [k for _, rows in HelpScreen._COLS for k, _ in rows]
        assert "u" in keys

    _drive(library, check)


def test_footer_shows_reduced_concepts_including_help():
    # The persistent footer is built by _cheatbar(); assert on its markup so the
    # reduced concept set is pinned regardless of rendering.
    from soap.tui.app import _cheatbar

    text = _cheatbar()
    for concept in ("select", "edit", "tag", "read", "export", "pane", "keys"):
        assert concept in text, f"missing {concept} in footer: {text!r}"
    # Export appears exactly once (no duplicate entry).
    assert text.count("export") == 1
    # The reference/help affordance is present.
    assert "?" in text
    # Dropped verbs no longer clutter the bar.
    assert "palette" not in text
    assert "quit" not in text
    assert "review" not in text
    assert "find" not in text
    # Unselect-all is discoverable via ? / palette, deliberately not on the bar.
    assert "clear" not in text
    assert "unselect" not in text
