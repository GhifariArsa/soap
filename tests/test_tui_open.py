"""Document opening precedence and URL fallback tests."""

from pathlib import Path
from types import SimpleNamespace

from soap.models.document import Document, FileRef
from soap.tui.app import SoapApp


class _Docs:
    def __init__(self, document):
        self.document = document

    def get_document(self, _id):
        return self.document


def _app(library, document):
    app = SoapApp(library)
    app.docs = _Docs(document)
    app.query_one = lambda _kind: SimpleNamespace(current_id=document.id)
    return app


def test_open_prefers_first_attached_local_file(library, monkeypatch):
    local = library.path / "paper.pdf"
    local.write_bytes(b"%PDF")
    doc = Document(
        id="paper", title="Paper", url="https://doi.org/10.1234/paper",
        files=[FileRef(path=str(local.relative_to(library.path)))],
    )
    app = _app(library, doc)
    launched = []
    monkeypatch.setattr(app, "_launch", launched.append)

    app.action_open()

    assert launched == [local]


def test_open_rejects_file_reference_symlink_escape(library, tmp_path, monkeypatch):
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    (library.path / "redirect.pdf").symlink_to(outside)
    doc = Document(
        id="paper", title="Paper", files=[FileRef(path="redirect.pdf")]
    )
    app = _app(library, doc)
    launched = []
    warnings = []
    monkeypatch.setattr(app, "_launch", launched.append)
    monkeypatch.setattr(app, "notify", lambda message, **kwargs: warnings.append(message))

    app.action_open()

    assert launched == []
    assert warnings and "unsafe file reference" in warnings[0]


def test_open_falls_back_to_document_url_without_file(library, monkeypatch):
    url = "https://arxiv.org/abs/1234.5678"
    doc = Document(id="paper", title="Paper", url=url)
    app = _app(library, doc)
    launched = []
    monkeypatch.setattr(app, "_launch", launched.append)

    app.action_open()

    assert launched == [url]


def test_open_warns_only_without_file_or_url(library, monkeypatch):
    doc = Document(id="paper", title="Paper")
    app = _app(library, doc)
    warnings = []
    monkeypatch.setattr(app, "notify", lambda message, **kwargs: warnings.append(message))
    monkeypatch.setattr(app, "_launch", lambda _target: (_ for _ in ()).throw(AssertionError()))

    app.action_open()

    assert warnings == ["no file attached to this document"]
