"""Tests for the inline field-by-field correction walk.

Covers the pure helper :func:`soap.library.prompt_fields` (Enter-keeps,
type-overrides, citekey-pinned-on-edit), its disk-backed sibling
:func:`soap.library.correct_document`, the ``correct`` action wired into
:func:`soap.library.review_inbox`, and the ``soap add --confirm`` flow. IO is
scripted exactly like ``tests/test_inbox_review.py`` so nothing needs a terminal.
"""

from typer.testing import CliRunner

from soap.db.documents import DocumentService
from soap.library import (
    CORE_REVIEW_FIELDS,
    add,
    correct_document,
    load_document,
    prompt_fields,
    review_inbox,
)
from soap.main import app
from soap.models.document import Document, ReviewStatus, Source


class _FieldScript:
    """A scripted ``prompt_fn``: maps field name -> answer, "" == keep detected.

    Any field not in the map yields "" (Enter-keeps), and an exhausted script
    raises ``EOFError`` so mid-walk end-of-input can be exercised.
    """

    def __init__(self, answers: dict[str, str] | None = None, *, raise_after=None):
        self.answers = answers or {}
        self.calls: list[tuple[str, str]] = []
        self._raise_after = raise_after

    def __call__(self, field: str, current: str) -> str:
        if self._raise_after is not None and len(self.calls) >= self._raise_after:
            raise EOFError
        self.calls.append((field, current))
        return self.answers.get(field, "")


def _detected_doc() -> Document:
    """A filename-only detected document, like a fresh needs_review item."""
    return Document(
        id="attention",
        title="attention is all you need",
        source=Source.LOCAL,
        confidence=0.3,
        review_status=ReviewStatus.NEEDS_REVIEW,
    )


# prompt_fields: Enter keeps every detected value (a safe no-op) --------------


def test_prompt_fields_enter_keeps_everything():
    doc = _detected_doc()
    script = _FieldScript()  # every field answered with ""
    updated = prompt_fields(doc, script)

    assert [f for f, _ in script.calls] == list(CORE_REVIEW_FIELDS)
    assert updated.title == "attention is all you need"
    assert updated.authors == []
    assert updated.year is None
    assert updated.type == "article"
    assert updated.venue is None
    assert updated.id == "attention"  # unchanged


# prompt_fields: typed input overrides the detected value --------------------


def test_prompt_fields_type_overrides():
    doc = _detected_doc()
    script = _FieldScript(
        {
            "title": "Attention Is All You Need",
            "authors": "Vaswani, Ashish; Shazeer, Noam",
            "year": "2017",
        }
    )
    updated = prompt_fields(doc, script)

    assert updated.title == "Attention Is All You Need"
    assert updated.authors == ["Vaswani, Ashish", "Shazeer, Noam"]
    assert updated.year == 2017
    # untouched fields keep their detected values
    assert updated.type == "article"
    # each field was prefilled with its detected string
    prefill = dict(script.calls)
    assert prefill["title"] == "attention is all you need"
    assert prefill["year"] == ""


# prompt_fields: the citekey/id is pinned even when title/authors/year change -


def test_prompt_fields_pins_citekey_on_edit():
    doc = _detected_doc()
    # These edits *would* regenerate the key to vaswani2017attention in add(),
    # but a review-edit must pin the id (decision 2).
    script = _FieldScript(
        {
            "title": "Attention Is All You Need",
            "authors": "Vaswani, Ashish",
            "year": "2017",
        }
    )
    updated = prompt_fields(doc, script)
    assert updated.id == "attention"  # pinned, not vaswani2017attention


# prompt_fields: a non-numeric year raises ValueError for the caller ---------


def test_prompt_fields_bad_year_raises():
    doc = _detected_doc()
    script = _FieldScript({"year": "not-a-year"})
    try:
        prompt_fields(doc, script)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a non-numeric year")


# correct_document: walks a queued item and persists it, id/folder pinned ----


def _seed(library, make_pdf, name: str) -> str:
    outcome = add(library, str(make_pdf(name)), fetch=False)
    assert outcome.status == "added"
    return outcome.citekey


def test_correct_document_persists_and_pins(library, make_pdf):
    doc_id = _seed(library, make_pdf, "attention_is_all_you_need.pdf")
    folder = library.documents / doc_id

    with DocumentService.open(library.db_path) as docs:
        updated = correct_document(
            library,
            doc_id,
            docs,
            _FieldScript(
                {
                    "title": "Attention Is All You Need",
                    "authors": "Vaswani, Ashish; Shazeer, Noam",
                    "year": "2017",
                }
            ),
        )

    assert updated.id == doc_id  # pinned
    assert folder.exists()  # folder never moved
    # disk carries the corrected metadata
    on_disk = load_document(library, doc_id)
    assert on_disk.title == "Attention Is All You Need"
    assert on_disk.authors == ["Vaswani, Ashish", "Shazeer, Noam"]
    assert on_disk.year == 2017
    # still needs_review — correction alone does not file it
    assert on_disk.review_status == "needs_review"
    # DB index reflects the edit
    with DocumentService.open(library.db_path) as docs:
        row = docs.conn.execute(
            "SELECT title, year FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
    assert row == ("Attention Is All You Need", 2017)


# review_inbox: the `correct` action walks fields, then accept files it ------


def _action_script(*answers: str):
    class _S:
        def __init__(self):
            self.answers = list(answers)

        def __call__(self) -> str:
            if not self.answers:
                raise EOFError
            return self.answers.pop(0)

    return _S()


def test_review_correct_then_accept(library, make_pdf):
    doc_id = _seed(library, make_pdf, "draft.pdf")
    field_script = _FieldScript({"title": "Corrected Inline", "year": "2020"})

    with DocumentService.open(library.db_path) as docs:
        summary = review_inbox(
            library,
            docs,
            render=lambda *a: None,
            ask_action=_action_script("c", "a"),
            confirm_delete=lambda doc: True,
            report=lambda *a: None,
            prompt_field=field_script,
        )

    assert summary.corrected == 1 and summary.filed == 1
    doc = load_document(library, doc_id)
    assert doc.title == "Corrected Inline"
    assert doc.year == 2020
    assert doc.review_status == "filed"
    assert doc.id == doc_id  # pinned


def test_review_correct_unavailable_without_prompt(library, make_pdf):
    _seed(library, make_pdf, "draft.pdf")
    reports: list[str] = []
    with DocumentService.open(library.db_path) as docs:
        summary = review_inbox(
            library,
            docs,
            render=lambda *a: None,
            ask_action=_action_script("c", "s"),
            confirm_delete=lambda doc: True,
            report=reports.append,
            prompt_field=None,  # not wired
        )
    assert summary.corrected == 0 and summary.skipped == 1
    assert any("correction not available" in m for m in reports)


# CLI shim: a scripted `correct` walk files the corrected record end-to-end ---


def test_cli_correct_files_document(library, make_pdf):
    doc_id = _seed(library, make_pdf, "a.pdf")
    runner = CliRunner()
    # c -> walk 5 fields (title override, then Enter x4) -> a to file.
    stdin = "c\nRenamed By CLI\n\n\n\n\na\n"
    result = runner.invoke(
        app, ["inbox", "review", "--path", str(library.path)], input=stdin
    )
    assert result.exit_code == 0, result.stdout
    doc = load_document(library, doc_id)
    assert doc.title == "Renamed By CLI"
    assert doc.review_status == "filed"
    assert doc.id == doc_id  # pinned
    assert "1 corrected" in result.stdout


# soap add --confirm: runs the same walk and re-derives the citekey ----------


def test_add_confirm_walks_and_rekeys(library, make_pdf):
    pdf = make_pdf("attention_is_all_you_need.pdf")
    runner = CliRunner()
    # Give it a real title/author/year so the citekey regenerates (add() derives
    # a fresh key at add time — the pin only applies to in-place review edits).
    stdin = "Attention Is All You Need\nVaswani, Ashish\n2017\n\n\n"
    result = runner.invoke(
        app,
        ["add", str(pdf), "--no-fetch", "--confirm", "--path", str(library.path)],
        input=stdin,
    )
    assert result.exit_code == 0, result.stdout
    with DocumentService.open(library.db_path) as docs:
        ids = [r.id for r in docs.list_documents(filter_kind="all")]
    assert "vaswani2017attention" in ids
    doc = load_document(library, "vaswani2017attention")
    assert doc.title == "Attention Is All You Need"
    assert doc.year == 2017
