"""Detail-pane rendering regressions."""

from soap.models.document import Document, FileRef
from soap.tui.widgets_detail import DetailPane


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
