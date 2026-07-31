"""Pilot coverage for search query handoff and list navigation."""

import asyncio

from soap.ingest.merge import Overrides
from soap.library import add
from soap.tui.app import SoapApp, SearchInput
from soap.tui.widgets import DocumentList
from soap.tui.widgets_detail import DetailPane


def _seed(library, make_pdf, name: str) -> str:
    outcome = add(
        library,
        str(make_pdf(name)),
        fetch=False,
        overrides=Overrides(),
    )
    assert outcome.status == "added"
    return outcome.citekey


def _drive(library, check):
    async def main():
        app = SoapApp(library)
        async with app.run_test() as pilot:
            await pilot.pause()
            await check(pilot, app)

    asyncio.run(main())


def test_search_enter_preserves_query_and_focuses_list(library, make_pdf):
    _seed(library, make_pdf, "paper-alpha.pdf")
    _seed(library, make_pdf, "paper-beta.pdf")

    async def check(pilot, app):
        await pilot.press("/")
        await pilot.press(*"paper")
        await pilot.pause()
        search = app.query_one(SearchInput)
        assert search.value == "paper"
        assert app.search_term == "paper"
        assert app.query_one(DocumentList).row_count == 2
        await pilot.press("enter")
        assert search.value == "paper"
        assert app.focused is app.query_one(DocumentList)

    _drive(library, check)


def test_search_down_and_tab_preserve_query_and_focus_list(library, make_pdf):
    _seed(library, make_pdf, "paper-alpha.pdf")

    async def check(pilot, app):
        search = app.query_one(SearchInput)
        await pilot.press("/")
        await pilot.press(*"paper")
        await pilot.press("down")
        assert search.value == "paper"
        assert app.focused is app.query_one(DocumentList)
        await pilot.press("/")
        await pilot.press("tab")
        assert search.value == "paper"
        assert app.focused is app.query_one(DocumentList)

    _drive(library, check)


def test_search_escape_clears_query_and_focuses_list(library, make_pdf):
    _seed(library, make_pdf, "paper-alpha.pdf")

    async def check(pilot, app):
        await pilot.press("/")
        await pilot.press(*"paper")
        await pilot.press("escape")
        assert app.query_one(SearchInput).value == ""
        assert app.search_term == ""
        assert app.focused is app.query_one(DocumentList)

    _drive(library, check)


def test_search_handoff_enables_j_k_navigation_and_detail_updates(library, make_pdf):
    first = _seed(library, make_pdf, "paper-alpha.pdf")
    second = _seed(library, make_pdf, "paper-beta.pdf")

    async def check(pilot, app):
        await pilot.press("/")
        await pilot.press(*"paper")
        await pilot.press("enter")
        doclist = app.query_one(DocumentList)
        assert doclist.current_id == first
        await pilot.press("j")
        assert doclist.current_id == second
        assert app.query_one(DetailPane)._document.id == second
        await pilot.press("k")
        assert doclist.current_id == first
        assert app.query_one(DetailPane)._document.id == first

    _drive(library, check)
