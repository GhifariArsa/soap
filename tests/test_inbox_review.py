"""Tests for the `soap inbox review` walk (`library.review_inbox`) and CLI shim.

The interactive loop is driven with scripted IO so the accept/edit/skip/delete/
quit actions are exercised without a terminal. Each assertion checks that disk
(info.yaml, the source of truth) and the SQLite index stay in sync.
"""

import yaml
from typer.testing import CliRunner

from soap.db.documents import DocumentService
from soap.library import load_document, review_inbox
from soap.main import app


class _Script:
    """A scripted ``ask_action``: pops queued answers, raises EOFError when dry."""

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


def _seed(library, make_pdf, name: str) -> str:
    """Add a local, no-identifier PDF — always lands in needs_review — and return its id."""
    from soap.library import add

    outcome = add(library, str(make_pdf(name)), fetch=False)
    assert outcome.status == "added"
    return outcome.citekey


def _run(library, script, *, confirm=lambda doc: True, editor_runner=None):
    with DocumentService.open(library.db_path) as docs:
        return review_inbox(
            library, docs,
            render=lambda *a: None,
            ask_action=script,
            confirm_delete=confirm,
            report=lambda *a: None,
            editor_runner=editor_runner,
        )


def _db_status(library, doc_id):
    with DocumentService.open(library.db_path) as docs:
        row = docs.conn.execute(
            "SELECT review_status FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        return row[0] if row else None


def _queue(library):
    with DocumentService.open(library.db_path) as docs:
        return docs.needs_review_ids()


# accept files the document (moves it out of the queue) ----------------------


def test_accept_files_document(library, make_pdf):
    doc_id = _seed(library, make_pdf, "a.pdf")
    summary = _run(library, _Script("a"))

    assert summary.filed == 1
    assert doc_id not in _queue(library)
    # disk + DB agree on filed
    assert load_document(library, doc_id).review_status == "filed"
    assert _db_status(library, doc_id) == "filed"


# skip leaves the document in the queue --------------------------------------


def test_skip_leaves_document(library, make_pdf):
    doc_id = _seed(library, make_pdf, "a.pdf")
    summary = _run(library, _Script("s"))

    assert summary.skipped == 1
    assert doc_id in _queue(library)
    assert load_document(library, doc_id).review_status == "needs_review"


# delete removes it from disk and the index ----------------------------------


def test_delete_removes_document(library, make_pdf):
    doc_id = _seed(library, make_pdf, "a.pdf")
    folder = library.documents / doc_id
    assert folder.exists()

    summary = _run(library, _Script("d"), confirm=lambda doc: True)

    assert summary.deleted == 1
    assert not folder.exists()  # disk gone
    assert _db_status(library, doc_id) is None  # index gone
    assert doc_id not in _queue(library)


def test_delete_cancelled_keeps_document(library, make_pdf):
    doc_id = _seed(library, make_pdf, "a.pdf")
    # Decline the delete, then quit — the document survives untouched.
    summary = _run(library, _Script("d", "q"), confirm=lambda doc: False)

    assert summary.deleted == 0
    assert (library.documents / doc_id).exists()
    assert doc_id in _queue(library)


# edit re-indexes, then the reviewer files the corrected record --------------


def test_edit_reindexes_then_accept(library, make_pdf):
    doc_id = _seed(library, make_pdf, "draft.pdf")

    def fake_editor(path):
        data = yaml.safe_load(path.read_text())
        data["title"] = "Corrected By Reviewer"
        path.write_text(yaml.safe_dump(data, sort_keys=False))

    # edit stays on the same doc, then accept files it.
    summary = _run(library, _Script("e", "a"), editor_runner=fake_editor)

    assert summary.edited == 1 and summary.filed == 1
    # disk carries the corrected title AND the filed flag
    doc = load_document(library, doc_id)
    assert doc.title == "Corrected By Reviewer"
    assert doc.review_status == "filed"
    # DB index reflects the edit
    with DocumentService.open(library.db_path) as docs:
        row = docs.conn.execute(
            "SELECT title, review_status FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
    assert row == ("Corrected By Reviewer", "filed")


# empty queue exits cleanly, never prompting ---------------------------------


def test_empty_queue_exits_without_prompting(library):
    script = _Script("a", "a")  # would be consumed if the loop ran
    with DocumentService.open(library.db_path) as docs:
        summary = review_inbox(
            library, docs,
            render=lambda *a: None,
            ask_action=script,
            confirm_delete=lambda doc: True,
            report=lambda *a: None,
        )
    assert summary.filed == 0 and summary.skipped == 0
    assert script.calls == 0  # never prompted


# EOF / piped-empty stdin ends the session cleanly, no hang ------------------


def test_eof_quits_without_change(library, make_pdf):
    doc_id = _seed(library, make_pdf, "a.pdf")
    summary = _run(library, _Script())  # first ask_action raises EOFError

    assert summary.filed == 0
    assert doc_id in _queue(library)  # untouched


def test_unknown_action_reprompts(library, make_pdf):
    doc_id = _seed(library, make_pdf, "a.pdf")
    reports = []
    with DocumentService.open(library.db_path) as docs:
        summary = review_inbox(
            library, docs,
            render=lambda *a: None,
            ask_action=_Script("huh?", "a"),
            confirm_delete=lambda doc: True,
            report=reports.append,
        )
    assert summary.filed == 1
    assert any("unknown action" in m for m in reports)
    assert _db_status(library, doc_id) == "filed"


# one document's error does not abort the whole session ----------------------


def test_error_on_one_doc_continues(library, make_pdf):
    first = _seed(library, make_pdf, "first.pdf")
    second = _seed(library, make_pdf, "second.pdf")
    # Corrupt the first document's info.yaml so accept raises; the walk must
    # report and continue to the second.
    order = _queue(library)
    broken, ok = order[0], order[1]
    (library.documents / broken / "info.yaml").unlink()

    reports = []
    with DocumentService.open(library.db_path) as docs:
        summary = review_inbox(
            library, docs,
            render=lambda *a: None,
            ask_action=_Script("a", "a"),
            confirm_delete=lambda doc: True,
            report=reports.append,
        )
    assert summary.errors == 1
    assert summary.filed == 1
    assert _db_status(library, ok) == "filed"
    assert any(broken in m for m in reports)
    assert {first, second} == {broken, ok}


# CLI shim: empty queue prints a message and exits 0 -------------------------


def test_cli_empty_queue_exit_0(library):
    runner = CliRunner()
    result = runner.invoke(
        app, ["inbox", "review", "--path", str(library.path)], input=""
    )
    assert result.exit_code == 0
    assert "Nothing to review" in result.stdout


# CLI shim: piped/empty stdin with a queued doc must not hang ----------------


def test_cli_eof_does_not_hang(library, make_pdf):
    _seed(library, make_pdf, "a.pdf")
    runner = CliRunner()
    # Empty stdin -> the first prompt reads EOF -> clean exit, no hang.
    result = runner.invoke(
        app, ["inbox", "review", "--path", str(library.path)], input=""
    )
    assert result.exit_code == 0


# CLI shim: a scripted accept files the document end-to-end ------------------


def test_cli_accept_files(library, make_pdf):
    doc_id = _seed(library, make_pdf, "a.pdf")
    runner = CliRunner()
    result = runner.invoke(
        app, ["inbox", "review", "--path", str(library.path)], input="a\n"
    )
    assert result.exit_code == 0
    assert load_document(library, doc_id).review_status == "filed"
    assert _db_status(library, doc_id) == "filed"
