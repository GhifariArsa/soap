"""Read-status cycling in the main browse view."""

import asyncio

from soap.db.documents import DocumentService
from soap.ingest.merge import Overrides
from soap.library import add, load_document
from soap.tui.app import SoapApp
from soap.tui.widgets import DocumentList, SidebarRow


def _seed(library, make_pdf):
    outcome = add(
        library,
        str(make_pdf("paper.pdf")),
        fetch=False,
        overrides=Overrides(title="Paper"),
    )
    assert outcome.status == "added"
    return outcome.citekey


def test_mark_cycles_all_states_and_persists_yaml_and_sqlite(library, make_pdf):
    doc_id = _seed(library, make_pdf)

    async def scenario():
        app = SoapApp(library)
        async with app.run_test() as pilot:
            await pilot.pause()
            for expected in ("reading", "read", "unread"):
                await pilot.press("m")
                await pilot.pause()
                assert app.docs.get_document(doc_id).read_status == expected
                assert expected in app.query_one("#detail-body").render().plain
                assert app.query_one(DocumentList).current_id == doc_id

    asyncio.run(scenario())
    assert load_document(library, doc_id).read_status == "unread"
    with DocumentService.open(library.db_path) as docs:
        assert docs.get_document(doc_id).read_status == "unread"


def test_mark_updates_sidebar_counts_and_reading_filter(library, make_pdf):
    doc_id = _seed(library, make_pdf)

    async def scenario():
        app = SoapApp(library)
        async with app.run_test() as pilot:
            await pilot.pause()
            sidebar = app.query_one("#sidebar")
            toread = next(row for row in sidebar.children if isinstance(row, SidebarRow) and row.kind == "toread")
            reading = next(row for row in sidebar.children if isinstance(row, SidebarRow) and row.kind == "reading")
            assert toread.query_one(".side-count").render().plain == "1"
            await pilot.press("m")
            await pilot.pause()
            toread = next(row for row in sidebar.children if isinstance(row, SidebarRow) and row.kind == "toread")
            reading = next(row for row in sidebar.children if isinstance(row, SidebarRow) and row.kind == "reading")
            assert toread.query_one(".side-count").render().plain == "0"
            assert reading.query_one(".side-count").render().plain == "1"
            app.filter_kind = "reading"
            app.filter_value = None
            app._populate_list()
            app.query_one(DocumentList).focus()
            await pilot.pause()
            assert app.query_one(DocumentList).current_id == doc_id
            app.action_cycle_read_status()
            await pilot.pause()
            assert app.docs.get_document(doc_id).read_status == "read"
            assert app.docs.list_documents(filter_kind="reading") == []
            assert app.filter_kind == "reading"
            assert app.query_one(DocumentList).row_count == 0

    asyncio.run(scenario())


def test_mark_binding_is_discoverable_and_non_conflicting():
    binding = next(b for b in SoapApp.BINDINGS if b.key == "m")
    assert binding.action == "cycle_read_status"
    assert binding.show is False
    assert {b.key for b in SoapApp.BINDINGS} >= {"m", "r", "t", "e", "o", "enter"}
