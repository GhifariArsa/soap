"""Detail-pane rendering and navigation regressions."""

import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Input

from soap.models.document import Document, FileRef
from soap.tui.widgets_detail import DetailPane


class _DetailApp(App[None]):
    def compose(self) -> ComposeResult:
        yield Input(id="other")
        yield DetailPane(id="detail")


def _document(**kwargs) -> Document:
    return Document(
        id="paper",
        title="A paper",
        abstract=" ".join(f"abstract-word-{i}" for i in range(220)),
        url="https://example.test/paper",
        **kwargs,
    )


def test_full_abstract_and_bottom_file_path():
    pane = DetailPane()
    doc = _document(files=[FileRef(path="documents/paper/paper.pdf")])
    markup = pane._to_markup(doc)
    assert "ABSTRACT" in markup
    assert "abstract-word-219" in markup
    assert "paper.pdf" in markup
    assert "documents/paper/paper.pdf" in markup


def test_url_fallback_and_empty_abstract():
    pane = DetailPane()
    markup = pane._to_markup(_document(files=[]))
    assert "abstract-word-219" in markup
    assert "https://example.test/paper" in markup

    empty = pane._to_markup(Document(id="empty", title="Empty"))
    assert "ABSTRACT" not in empty
    assert "no file attached" in empty


def test_focus_scroll_navigation_preserves_full_abstract():
    async def scenario():
        app = _DetailApp()
        async with app.run_test(size=(100, 20)) as pilot:
            pane = app.query_one(DetailPane)
            app.query_one(Input).focus()
            await pilot.pause()
            doc = _document()
            pane.show(doc)
            body = pane.query_one("#detail-body")
            assert "abstract-word-219" in body.render().plain

            pane.focus()
            await pilot.pause()
            assert "abstract-word-219" in body.render().plain

            pane.scroll_home(animate=False)
            await pilot.press("j")
            await pilot.pause()
            assert pane.scroll_y > 0
            before = pane.scroll_y
            await pilot.press("k")
            await pilot.pause()
            assert pane.scroll_y < before

            pane.blur()
            await pilot.pause()
            assert "abstract-word-219" in body.render().plain

    asyncio.run(scenario())
