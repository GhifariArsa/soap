"""`always_review: true` forces every add into the needs_review queue."""

import httpx

from soap.config import config_path
from soap.ingest.merge import Overrides
from soap.library import add

from tests.conftest import mock_client

DOI = "10.5555/3295222.3295349"

CROSSREF_BODY = {
    "message": {
        "title": ["Attention Is All You Need"],
        "author": [{"given": "Ashish", "family": "Vaswani"}],
        "issued": {"date-parts": [[2017]]},
        "container-title": ["NeurIPS"],
        "type": "proceedings-article",
        "DOI": DOI,
        "URL": f"https://doi.org/{DOI}",
    }
}


def _crossref_client():
    return mock_client({"api.crossref.org": httpx.Response(200, json=CROSSREF_BODY)})


def test_confident_add_is_filed_without_config(library, make_pdf):
    # Baseline: a DOI-fetched add with a file is `filed` when no config exists.
    outcome = add(
        library, str(make_pdf("a.pdf")),
        overrides=Overrides(doi=DOI), client=_crossref_client(),
    )
    assert outcome.document.review_status == "filed"


def test_always_review_forces_needs_review(library, make_pdf):
    config_path(library.path).write_text("always_review: true\n")
    outcome = add(
        library, str(make_pdf("a.pdf")),
        overrides=Overrides(doi=DOI), client=_crossref_client(),
    )
    # The same add that would normally file now lands in the review queue.
    assert outcome.status == "added"
    assert outcome.document.review_status == "needs_review"


def test_always_review_false_is_unchanged(library, make_pdf):
    config_path(library.path).write_text("always_review: false\n")
    outcome = add(
        library, str(make_pdf("a.pdf")),
        overrides=Overrides(doi=DOI), client=_crossref_client(),
    )
    assert outcome.document.review_status == "filed"


def test_always_review_persists_to_disk_and_db(library, make_pdf):
    config_path(library.path).write_text("always_review: true\n")
    outcome = add(library, str(make_pdf("a.pdf")), fetch=False)
    # Disk (info.yaml) and the DB index agree on needs_review.
    from soap.db.documents import DocumentService
    from soap.library import load_document

    assert load_document(library, outcome.citekey).review_status == "needs_review"
    with DocumentService.open(library.db_path) as docs:
        assert outcome.citekey in docs.needs_review_ids()
