"""Focused detail-pane rendering and navigation regressions."""

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


def test_unfocused_preview_and_bottom_file_path():
    pane = DetailPane()
    doc = _document(files=[FileRef(path="documents/paper/paper.pdf")])
    markup = pane._to_markup(doc, focused=False)
    assert "ABSTRACT" in markup
    assert "…" in markup
    assert "abstract-word-219" not in markup
    assert "paper.pdf" in markup
    assert "documents/paper/paper.pdf" in markup


def test_focused_render_contains_full_abstract_and_url_fallback():
    pane = DetailPane()
    doc = _document(files=[])
    markup = pane._to_markup(doc, focused=True)
    assert "abstract-word-219" in markup
    assert "https://example.test/paper" in markup


def test_empty_abstract_and_link_keep_existing_message():
    pane = DetailPane()
    doc = Document(id="empty", title="Empty")
    markup = pane._to_markup(doc, focused=False)
    assert "ABSTRACT" not in markup
    assert "no file attached" in markup


def test_focus_transition_and_j_k_scroll_without_list():
    async def scenario():
        app = _DetailApp()
        async with app.run_test(size=(100, 20)) as pilot:
            pane = app.query_one(DetailPane)
            app.query_one(Input).focus()
            await pilot.pause()
            doc = _document()
            pane.show(doc)
            body = pane.query_one("#detail-body")
            assert "abstract-word-219" not in body.render().plain

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
            assert "abstract-word-219" not in body.render().plain

    asyncio.run(scenario())
