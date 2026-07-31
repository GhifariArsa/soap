"""Filed-document editor regressions for the main browse view."""

from contextlib import nullcontext

import yaml

from soap.db.documents import DocumentService
from soap.library import info_yaml_path, load_document, save_document
from soap.models.document import Document
from soap.tui.app import SoapApp


def _filed(library, title="Original"):
    doc = Document(id="fixed-key", title=title, authors=["Doe, Jane"])
    (library.documents / doc.id).mkdir()
    docs = DocumentService.open(library.db_path)
    save_document(library, doc, docs)
    return docs, doc


def _run_edit(library, docs, editor):
    app = SoapApp(library, editor_runner=editor)
    app.docs = docs
    row = type("Row", (), {
        "current_id": "fixed-key", "_ids": ["fixed-key"],
        "move_cursor": lambda self, **_kwargs: None,
    })()
    app.query_one = lambda _kind: row
    app.suspend = nullcontext
    app.refresh_data = lambda: None
    app._show_detail = lambda _doc_id: None
    app.notify = lambda *_args, **_kwargs: None
    # Exercise the same action dispatched by the hidden `e` binding.
    app.action_edit_metadata()


def test_main_view_editor_updates_disk_and_sqlite(library):
    docs, doc = _filed(library)
    seen = []

    def editor(path):
        seen.append(path)
        data = yaml.safe_load(path.read_text())
        data["title"] = "Edited title"
        data["id"] = "attempted-new-key"
        path.write_text(yaml.safe_dump(data, sort_keys=False))

    try:
        _run_edit(library, docs, editor)
    finally:
        docs.close()

    assert seen == [info_yaml_path(library, doc.id)]
    assert load_document(library, doc.id).title == "Edited title"
    assert load_document(library, doc.id).id == "fixed-key"
    with DocumentService.open(library.db_path) as refreshed:
        assert refreshed.get_document("fixed-key").title == "Edited title"
        assert refreshed.get_document("attempted-new-key") is None


def test_main_view_invalid_edit_preserves_file_and_index(library):
    docs, doc = _filed(library)

    def editor(path):
        path.write_text("title: [invalid\n")

    try:
        _run_edit(library, docs, editor)
    finally:
        docs.close()

    assert info_yaml_path(library, doc.id).read_text() == "title: [invalid\n"
    with DocumentService.open(library.db_path) as unchanged:
        assert unchanged.get_document(doc.id).title == "Original"


def test_main_view_has_hidden_editor_keyboard_action():
    binding = next(b for b in SoapApp.BINDINGS if b.key == "e")
    assert binding.action == "edit_metadata"
    assert binding.show is False
