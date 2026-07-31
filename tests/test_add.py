"""Integration tests for `library.add` — manual ingestion with DOI/arXiv fetch."""

import httpx
import pytest
import yaml

from soap.db.documents import DocumentService
from soap.ingest.merge import Overrides
from soap.library import add, load_document
from soap.models.document import Document

from tests.conftest import mock_client

DOI = "10.5555/3295222.3295349"

CROSSREF_BODY = {
    "message": {
        "title": ["Attention Is All You Need"],
        "author": [
            {"given": "Ashish", "family": "Vaswani"},
            {"given": "Noam", "family": "Shazeer"},
        ],
        "issued": {"date-parts": [[2017]]},
        "container-title": ["NeurIPS"],
        "type": "proceedings-article",
        "DOI": DOI,
        "URL": f"https://doi.org/{DOI}",
    }
}

ARXIV_BODY = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2006.11239v2</id>
    <title>Denoising Diffusion Probabilistic Models</title>
    <summary>Image synthesis.</summary>
    <published>2020-06-19T00:00:00Z</published>
    <author><name>Jonathan Ho</name></author>
  </entry>
</feed>"""


ISBN = "9780134685991"

OPENLIBRARY_BODY = {
    f"ISBN:{ISBN}": {
        "title": "Effective Java",
        "authors": [{"name": "Joshua Bloch"}],
        "publish_date": "2018",
        "publishers": [{"name": "Addison-Wesley"}],
        "url": f"https://openlibrary.org/isbn/{ISBN}",
    }
}


def _crossref_client():
    return mock_client({"api.crossref.org": httpx.Response(200, json=CROSSREF_BODY)})


def _openlibrary_client():
    return mock_client(
        {"openlibrary.org/api/books": httpx.Response(200, json=OPENLIBRARY_BODY)}
    )


def _arxiv_client():
    return mock_client({"export.arxiv.org": httpx.Response(200, text=ARXIV_BODY)})


def _doc_row(library, doc_id):
    with DocumentService.open(library.db_path) as docs:
        return docs.conn.execute(
            "SELECT id, title, year, doi, source, review_status FROM documents WHERE id=?",
            (doc_id,),
        ).fetchone()


# --doi flag fetches Crossref -----------------------------------------------


def test_add_pdf_with_doi_flag_fetches_crossref(library, make_pdf):
    pdf = make_pdf("attn.pdf")
    outcome = add(library, str(pdf), overrides=Overrides(doi=DOI), client=_crossref_client())

    assert outcome.status == "added"
    assert outcome.citekey == "vaswani2017attention"
    assert outcome.document.title == "Attention Is All You Need"
    assert outcome.document.source == "crossref"
    assert outcome.document.review_status == "filed"

    folder = library.documents / "vaswani2017attention"
    assert (folder / "info.yaml").exists()
    assert (folder / "attn.pdf").exists()
    row = _doc_row(library, "vaswani2017attention")
    assert row[1] == "Attention Is All You Need" and row[3] == DOI


# --arxiv flag fetches arXiv ------------------------------------------------


def test_add_pdf_with_arxiv_flag(library, make_pdf):
    pdf = make_pdf("ddpm.pdf")
    outcome = add(
        library, str(pdf), overrides=Overrides(arxiv_id="2006.11239v2"),
        client=_arxiv_client(),
    )
    assert outcome.status == "added"
    assert outcome.document.title == "Denoising Diffusion Probabilistic Models"
    assert outcome.document.source == "arxiv"
    assert outcome.document.arxiv_id == "2006.11239v2"


def test_doi_option_without_positional_source_uses_canonical_url(library):
    outcome = add(
        library, overrides=Overrides(doi=f"https://doi.org/{DOI}"),
        client=_crossref_client(),
    )
    assert outcome.status == "added"
    assert outcome.document.doi == DOI
    assert outcome.document.url == f"https://doi.org/{DOI}"
    assert "https://doi.org/https" not in (outcome.document.url or "")


def test_bare_arxiv_option_without_positional_source_download_fallback(library):
    def handler(request):
        if "export.arxiv.org" in str(request.url):
            return httpx.Response(200, text=ARXIV_BODY)
        raise httpx.ConnectError("download unavailable")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    outcome = add(
        library, overrides=Overrides(arxiv_id="2006.11239v2"), client=client,
    )
    assert outcome.status == "added"
    assert outcome.document.arxiv_id == "2006.11239v2"
    assert outcome.document.url == "https://arxiv.org/abs/2006.11239v2"
    assert outcome.document.files == []
    assert outcome.document.title == "Denoising Diffusion Probabilistic Models"
    assert any("no PDF downloaded" in warning for warning in outcome.warnings)


# No identifier -> filename title, local, needs_review ----------------------


def test_add_pdf_no_identifier(library, make_pdf):
    pdf = make_pdf("smith-paper-final-v3.pdf")
    outcome = add(library, str(pdf), fetch=False)
    assert outcome.status == "added"
    assert outcome.document.source == "local"
    assert outcome.document.review_status == "needs_review"
    assert outcome.document.title == "smith paper final v3"


# --no-fetch makes no network calls -----------------------------------------


def test_no_fetch_makes_no_network_calls(library, make_pdf):
    pdf = make_pdf("x.pdf")

    def explode(request):
        raise AssertionError("network call made under --no-fetch")

    client = httpx.Client(transport=httpx.MockTransport(explode))
    outcome = add(library, str(pdf), overrides=Overrides(doi=DOI), fetch=False, client=client)
    assert outcome.status == "added"
    # the supplied DOI is recorded even without a fetch
    assert outcome.document.doi == DOI
    assert outcome.document.source == "manual"


# sha256 duplicate detection + --force --------------------------------------


def test_duplicate_by_sha256(library, make_pdf):
    pdf = make_pdf("a.pdf")
    first = add(library, str(pdf), fetch=False)
    assert first.status == "added"
    second = add(library, str(pdf), fetch=False)
    assert second.status == "skipped"
    assert second.matched_id == first.citekey
    forced = add(library, str(pdf), fetch=False, force=True)
    assert forced.status == "added"
    assert forced.citekey != first.citekey


# Explicit flags override fetched -------------------------------------------


def test_flags_override_fetched(library, make_pdf):
    pdf = make_pdf("attn.pdf")
    outcome = add(
        library, str(pdf), overrides=Overrides(doi=DOI, title="My Override"),
        client=_crossref_client(),
    )
    assert outcome.document.title == "My Override"


# Citekey collisions --------------------------------------------------------


def test_citekey_collision_suffixes(library, make_pdf):
    ov = Overrides(title="Same Title", authors=["Kim"], year=2021)
    a = add(library, str(make_pdf("1.pdf")), overrides=ov, fetch=False, force=True)
    b = add(library, str(make_pdf("2.pdf")), overrides=ov, fetch=False, force=True)
    c = add(library, str(make_pdf("3.pdf")), overrides=ov, fetch=False, force=True)
    assert {a.citekey, b.citekey, c.citekey} == {"kim2021same", "kim2021samea", "kim2021sameb"}


# Batch: one failure does not abort the rest --------------------------------


def test_batch_continues_after_failure(library, make_pdf, tmp_path):
    good = make_pdf("good.pdf")
    o_missing = add(library, str(tmp_path / "nope.pdf"), fetch=False)
    o_good = add(library, str(good), fetch=False)
    assert o_missing.status == "error"
    assert o_good.status == "added"


# Offline: network failure falls back to manual -----------------------------


def test_offline_network_failure_falls_back(library, make_pdf):
    pdf = make_pdf("x.pdf")

    def down(request):
        raise httpx.ConnectError("no network")

    client = httpx.Client(transport=httpx.MockTransport(down))
    outcome = add(library, str(pdf), overrides=Overrides(doi=DOI), client=client)
    assert outcome.status == "added"
    assert outcome.document.doi == DOI
    assert any("lookup failed" in w for w in outcome.warnings)


@pytest.mark.parametrize(
    "kind,bad_response,route",
    [
        ("doi", httpx.Response(200, json={"message": {"title": "wrong"}}), "api.crossref.org"),
        ("arxiv", httpx.Response(200, json=[]), "export.arxiv.org"),
        ("isbn", httpx.Response(200, json=[]), "openlibrary.org/api/books"),
    ],
)
def test_add_malformed_provider_payload_falls_back_with_warning(
    library, make_pdf, kind, bad_response, route
):
    pdf = make_pdf(f"bad-{kind}.pdf")
    overrides = {
        "doi": Overrides(doi=DOI),
        "arxiv": Overrides(arxiv_id="2006.11239v2"),
        "isbn": Overrides(isbn=ISBN),
    }[kind]
    outcome = add(
        library,
        str(pdf),
        overrides=overrides,
        client=mock_client({route: bad_response}),
    )
    assert outcome.status == "added"
    assert outcome.document.title == f"bad {kind}"
    assert any("lookup failed" in warning for warning in outcome.warnings)


# Dry-run writes nothing ----------------------------------------------------


def test_dry_run_writes_nothing(library, make_pdf):
    pdf = make_pdf("x.pdf")
    before = set(library.documents.iterdir())
    outcome = add(library, str(pdf), fetch=False, dry_run=True)
    assert outcome.status == "added" and outcome.dry_run
    assert set(library.documents.iterdir()) == before
    assert _doc_row(library, outcome.citekey) is None


# info.yaml round-trips through the model -----------------------------------


def test_info_yaml_round_trips(library, make_pdf):
    pdf = make_pdf("attn.pdf")
    outcome = add(
        library, str(pdf),
        overrides=Overrides(doi=DOI, tags=["nlp"], collections=["Thesis"]),
        client=_crossref_client(),
    )
    info = library.documents / outcome.citekey / "info.yaml"
    text = info.read_text()
    # Helper comment guides manual $EDITOR edits without breaking the round-trip.
    assert text.lstrip().startswith("#")
    assert "Last, First" in text
    restored = Document(**yaml.safe_load(text))
    assert restored.model_dump() == outcome.document.model_dump()
    assert restored.tags == ["nlp"]
    assert restored.collections == ["Thesis"]


# Author order preserved ----------------------------------------------------


def test_author_order_preserved(library, make_pdf):
    pdf = make_pdf("attn.pdf")
    outcome = add(library, str(pdf), overrides=Overrides(doi=DOI), client=_crossref_client())
    with DocumentService.open(library.db_path) as docs:
        rows = docs.conn.execute(
            "SELECT a.name FROM document_authors da "
            "JOIN authors a ON a.id = da.author_id "
            "WHERE da.document_id = ? ORDER BY da.position",
            (outcome.citekey,),
        ).fetchall()
    assert [r[0] for r in rows] == ["Ashish Vaswani", "Noam Shazeer"]


# --isbn flag fetches Open Library ------------------------------------------


def test_add_book_with_isbn_flag(library, make_pdf):
    pdf = make_pdf("java.pdf")
    outcome = add(
        library, str(pdf), overrides=Overrides(isbn="978-0-13-468599-1"),
        client=_openlibrary_client(),
    )
    assert outcome.status == "added"
    assert outcome.citekey == "bloch2018effective"
    assert outcome.document.title == "Effective Java"
    assert outcome.document.type == "book"
    assert outcome.document.source == "openlibrary"
    assert outcome.document.review_status == "filed"
    # ISBN is normalized (hyphens stripped) before storing
    assert outcome.document.isbn == ISBN
    assert _doc_row(library, "bloch2018effective")[3] is None  # no doi


def test_isbn_duplicate_detection(library, make_pdf):
    # Same ISBN with different hyphenation must dedupe.
    first = add(
        library, str(make_pdf("a.pdf")), overrides=Overrides(isbn=ISBN),
        client=_openlibrary_client(),
    )
    assert first.status == "added"
    second = add(
        library, str(make_pdf("b.pdf")),
        overrides=Overrides(isbn="978-0-13-468599-1"), fetch=False,
    )
    assert second.status in ("skipped", "upgraded")
    assert second.matched_id == first.citekey


def test_isbn_url_metadata_only(library):
    outcome = add(
        library, f"https://openlibrary.org/isbn/{ISBN}", client=_openlibrary_client()
    )
    assert outcome.status == "added"
    assert outcome.document.source == "openlibrary"
    assert outcome.document.title == "Effective Java"
    assert outcome.document.isbn == ISBN
    assert outcome.document.files == []
    assert outcome.document.review_status == "needs_review"


# arXiv URL -> metadata-only ------------------------------------------------


def test_arxiv_url_is_metadata_only(library):
    outcome = add(library, "https://arxiv.org/abs/2006.11239v2", client=_arxiv_client())
    assert outcome.status == "added"
    assert outcome.document.source == "arxiv"
    assert outcome.document.title == "Denoising Diffusion Probabilistic Models"
    assert outcome.document.files == []
    assert outcome.document.review_status == "needs_review"


# DOI URL -> metadata-only, then local file attaches (upgrade) --------------


def test_doi_url_then_upgrade(library, make_pdf):
    meta_only = add(library, f"https://doi.org/{DOI}", client=_crossref_client())
    assert meta_only.status == "added"
    assert meta_only.document.files == []
    assert meta_only.document.review_status == "needs_review"

    pdf = make_pdf("found.pdf")
    upgrade = add(library, str(pdf), overrides=Overrides(doi=DOI), fetch=False)
    assert upgrade.status == "upgraded"
    assert upgrade.matched_id == meta_only.citekey
    with DocumentService.open(library.db_path) as docs:
        assert docs.has_file(meta_only.citekey)
        assert _doc_row(library, meta_only.citekey)[5] == "filed"
    upgraded_doc = load_document(library, meta_only.citekey)
    assert upgraded_doc.review_status == "filed"
    assert [ref.path for ref in upgraded_doc.files] == [
        f"documents/{meta_only.citekey}/found.pdf"
    ]


def test_arxiv_metadata_only_then_upgrade_updates_disk_and_index(library, make_pdf):
    meta_only = add(
        library, "https://arxiv.org/abs/2006.11239v2", client=_arxiv_client()
    )
    pdf = make_pdf("found-arxiv.pdf")
    upgrade = add(
        library, str(pdf), overrides=Overrides(arxiv_id="2006.11239v2"),
        fetch=False,
    )

    assert upgrade.status == "upgraded"
    assert upgrade.document.review_status == "filed"
    on_disk = load_document(library, meta_only.citekey)
    assert on_disk.files[0].path == (
        f"documents/{meta_only.citekey}/found-arxiv.pdf"
    )
    with DocumentService.open(library.db_path) as docs:
        indexed = docs.get_document(meta_only.citekey)
    assert indexed.files == on_disk.files
    assert indexed.review_status == on_disk.review_status == "filed"


# Unrecognised URL -> metadata-only with URL as title -----------------------


def test_unrecognised_url_metadata_only(library):
    client = mock_client({})  # nothing matches
    outcome = add(library, "https://example.com/some-paper", fetch=False, client=client)
    assert outcome.status == "added"
    assert outcome.document.files == []
    assert outcome.document.review_status == "needs_review"
    assert any("not an arXiv or DOI" in w for w in outcome.warnings)


# --edit opens the editor and re-derives the citekey ------------------------


# arXiv / DOI / direct-PDF link -> download & attach the PDF -----------------

PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _pdf_response():
    return httpx.Response(
        200, content=PDF_BYTES, headers={"content-type": "application/pdf"}
    )


def _arxiv_client_with_pdf():
    return mock_client(
        {
            "export.arxiv.org": httpx.Response(200, text=ARXIV_BODY),
            "arxiv.org/pdf": _pdf_response(),
        }
    )


def _assert_attached(library, outcome, filename):
    assert outcome.status == "added"
    assert len(outcome.document.files) == 1
    ref = outcome.document.files[0]
    assert ref.path == f"documents/{outcome.citekey}/{filename}"
    assert ref.sha256 and ref.mime == "application/pdf"
    stored = library.documents / outcome.citekey / filename
    assert stored.exists() and stored.read_bytes().startswith(b"%PDF")


def test_arxiv_abs_url_downloads_pdf(library):
    outcome = add(
        library, "https://arxiv.org/abs/2006.11239v2", client=_arxiv_client_with_pdf()
    )
    _assert_attached(library, outcome, "2006.11239v2.pdf")
    assert outcome.document.source == "arxiv"
    # The abstract still flows from the metadata fetch alongside the file.
    assert outcome.document.abstract == "Image synthesis."


def test_arxiv_pdf_url_form_downloads(library):
    outcome = add(
        library, "https://arxiv.org/pdf/2006.11239v2", client=_arxiv_client_with_pdf()
    )
    _assert_attached(library, outcome, "2006.11239v2.pdf")


def test_bare_arxiv_id_downloads(library):
    outcome = add(library, "2006.11239v2", client=_arxiv_client_with_pdf())
    _assert_attached(library, outcome, "2006.11239v2.pdf")
    assert outcome.document.arxiv_id == "2006.11239v2"


def test_bare_arxiv_id_yields_to_local_file(library, make_pdf, monkeypatch):
    # A real file named like an arXiv id must be added as a file, not a ref.
    pdf = make_pdf("2006.11239v2")  # no .pdf suffix, but exists on disk
    monkeypatch.chdir(pdf.parent)
    outcome = add(library, "2006.11239v2", fetch=False)
    assert outcome.status == "added"
    assert outcome.document.source == "local"


def test_direct_pdf_url_downloads(library):
    client = mock_client({"example.com/foo.pdf": _pdf_response()})
    outcome = add(library, "https://example.com/foo.pdf", client=client)
    _assert_attached(library, outcome, "foo.pdf")
    assert outcome.document.title == "foo"


def test_doi_url_with_open_access_pdf_downloads(library):
    body = {"message": dict(CROSSREF_BODY["message"], link=[
        {"content-type": "application/pdf", "URL": "https://example.com/paper.pdf"},
    ])}
    client = mock_client({
        "api.crossref.org": httpx.Response(200, json=body),
        "example.com/paper.pdf": _pdf_response(),
    })
    outcome = add(library, f"https://doi.org/{DOI}", client=client)
    _assert_attached(library, outcome, "paper.pdf")
    assert outcome.document.doi == DOI


def test_doi_private_open_access_pdf_is_skipped(library):
    body = {"message": dict(CROSSREF_BODY["message"], link=[
        {"content-type": "application/pdf", "URL": "http://127.0.0.1/paper.pdf"},
    ])}
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if "api.crossref.org" in str(request.url):
            return httpx.Response(200, json=body)
        raise AssertionError("private metadata-selected PDF was requested")

    outcome = add(
        library,
        f"https://doi.org/{DOI}",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert outcome.status == "added"
    assert outcome.document.files == []
    assert seen == [f"https://api.crossref.org/works/{DOI}"]
    assert any("no downloadable PDF" in warning for warning in outcome.warnings)


def test_doi_url_paywalled_stays_metadata_only(library):
    # Crossref has no PDF link and the DOI landing page serves HTML (404 here).
    outcome = add(library, f"https://doi.org/{DOI}", client=_crossref_client())
    assert outcome.status == "added"
    assert outcome.document.files == []
    assert outcome.document.title == "Attention Is All You Need"
    assert any("no downloadable PDF" in w for w in outcome.warnings)


def test_download_failure_falls_back_to_metadata(library):
    # Metadata fetches fine; the PDF endpoint drops the connection.
    def handler(request):
        url = str(request.url)
        if "export.arxiv.org" in url:
            return httpx.Response(200, text=ARXIV_BODY)
        raise httpx.ConnectError("boom")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    outcome = add(library, "https://arxiv.org/abs/2006.11239v2", client=client)
    assert outcome.status == "added"
    assert outcome.document.files == []
    assert outcome.document.title == "Denoising Diffusion Probabilistic Models"
    assert any("no PDF downloaded" in w for w in outcome.warnings)


def test_no_fetch_url_makes_no_download(library):
    def explode(request):
        raise AssertionError("network call made under --no-fetch")

    client = httpx.Client(transport=httpx.MockTransport(explode))
    outcome = add(library, "https://example.com/foo.pdf", fetch=False, client=client)
    assert outcome.status == "added"
    assert outcome.document.files == []


def test_dry_run_url_downloads_nothing(library):
    before = set(library.documents.iterdir())
    outcome = add(
        library, "https://arxiv.org/abs/2006.11239v2",
        dry_run=True, client=_arxiv_client_with_pdf(),
    )
    assert outcome.status == "added" and outcome.dry_run
    assert outcome.document.files == []
    assert set(library.documents.iterdir()) == before


def test_edit_opens_editor_and_reidentifies(library, make_pdf):
    pdf = make_pdf("draft.pdf")

    def fake_editor(path):
        # Simulate the user correcting the metadata in $EDITOR.
        data = yaml.safe_load(path.read_text())
        data["title"] = "Corrected Title"
        data["authors"] = ["Grace Hopper"]
        data["year"] = 1952
        path.write_text(yaml.safe_dump(data, sort_keys=False))

    outcome = add(library, str(pdf), fetch=False, edit=True, editor_runner=fake_editor)
    assert outcome.status == "added"
    assert outcome.document.title == "Corrected Title"
    assert outcome.citekey == "hopper1952corrected"
    folder = library.documents / "hopper1952corrected"
    assert (folder / "info.yaml").exists()
    assert (folder / "draft.pdf").exists()
    # the file ref path tracks the re-derived citekey
    assert outcome.document.files[0].path == "documents/hopper1952corrected/draft.pdf"
