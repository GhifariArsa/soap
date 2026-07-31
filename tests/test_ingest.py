"""Unit tests for the pure ingest functions: no filesystem, no network."""

import gzip
import json
import socket

import httpx
import pytest

from soap.ingest.download import arxiv_pdf_url, download_pdf, is_pdf_url
from soap.ingest.fetch import (
    MAX_METADATA_BYTES,
    FetchedMetadata,
    fetch_arxiv,
    fetch_crossref,
    fetch_isbn,
    parse_arxiv,
    parse_crossref,
    parse_openlibrary,
)
from soap.ingest.identifiers import normalize_isbn
from soap.ingest.merge import (
    Overrides,
    generate_citekey,
    merge_metadata,
    unique_citekey,
)
from soap.ingest import network as network_mod
from soap.ingest.network import MAX_REDIRECTS, UnsafeURL, validate_url
from soap.ingest.url import (
    arxiv_id_from_url,
    bare_arxiv_id,
    doi_from_url,
    is_url,
    isbn_from_url,
)
from soap.models.document import ReviewStatus, Source

from tests.conftest import mock_client


def _fetched(**kwargs):
    kwargs.setdefault("source", "crossref")
    return FetchedMetadata(**kwargs)


# --- citekey --------------------------------------------------------------


def test_citekey_canonical():
    assert generate_citekey(["Ashish Vaswani"], 2017, "Attention Is All You Need") == "vaswani2017attention"


def test_citekey_skips_stopwords_in_title():
    assert generate_citekey(["Ho"], 2020, "The Denoising Model") == "ho2020denoising"


def test_citekey_comma_name():
    assert generate_citekey(["Vaswani, Ashish"], 2017, "Attention") == "vaswani2017attention"


def test_citekey_no_author_uses_title():
    assert generate_citekey([], 2019, "Unknown Paper") == "unknown2019"


def test_citekey_no_year_omitted():
    assert generate_citekey(["Smith"], None, "Widgets") == "smithwidgets"


def test_citekey_ascii_folds():
    assert generate_citekey(["Érdős"], 1999, "Números") == "erdos1999numeros"


def test_unique_citekey_appends_suffixes():
    taken = {"smith2019", "smith2019a"}
    assert unique_citekey("smith2019", lambda k: k in taken) == "smith2019b"
    assert unique_citekey("free2020", lambda k: k in taken) == "free2020"


# --- merge precedence -----------------------------------------------------


def test_merge_flags_beat_everything():
    fetched = _fetched(title="Crossref Title", year=2000)
    merged = merge_metadata(
        Overrides(title="Flag Title", year=1999), fetched, "filename"
    )
    assert merged.title == "Flag Title"
    assert merged.year == 1999


def test_merge_filename_only_needs_review():
    merged = merge_metadata(Overrides(), None, "some filename")
    assert merged.source == Source.LOCAL
    assert merged.review_status == ReviewStatus.NEEDS_REVIEW
    assert merged.title == "some filename"


def test_merge_fetched_is_filed_high_confidence():
    merged = merge_metadata(Overrides(), _fetched(title="T"), None)
    assert merged.source == Source.CROSSREF
    assert merged.review_status == ReviewStatus.FILED
    assert merged.confidence >= 0.9


def test_merge_arxiv_source():
    merged = merge_metadata(Overrides(), _fetched(title="T", source="arxiv"), None)
    assert merged.source == Source.ARXIV
    assert merged.review_status == ReviewStatus.FILED


def test_merge_manual_is_filed():
    merged = merge_metadata(Overrides(title="Manual"), None, None)
    assert merged.source == Source.MANUAL
    assert merged.review_status == ReviewStatus.FILED


def test_merge_openlibrary_source():
    merged = merge_metadata(Overrides(), _fetched(title="Book", source="openlibrary"), None)
    assert merged.source == Source.OPENLIBRARY
    assert merged.review_status == ReviewStatus.FILED


# --- ISBN -----------------------------------------------------------------


def test_normalize_isbn():
    assert normalize_isbn("978-0-13-468599-1") == "9780134685991"
    assert normalize_isbn("0-306-40615-2") == "0306406152"
    assert normalize_isbn("080442957x") == "080442957X"


def test_isbn_from_url():
    assert isbn_from_url("https://openlibrary.org/isbn/9780134685991") == "9780134685991"
    assert isbn_from_url("https://example.com/book") is None


def test_parse_openlibrary_maps_fields():
    payload = {
        "ISBN:9780134685991": {
            "title": "Effective Java",
            "authors": [{"name": "Joshua Bloch"}],
            "publish_date": "2018",
            "publishers": [{"name": "Addison-Wesley"}],
            "url": "https://openlibrary.org/books/OL27century/Effective_Java",
        }
    }
    meta = parse_openlibrary(payload, "9780134685991")
    assert meta.title == "Effective Java"
    assert meta.authors == ["Joshua Bloch"]
    assert meta.year == 2018
    assert meta.publisher == "Addison-Wesley"
    assert meta.type == "book"
    assert meta.isbn == "9780134685991"


def test_parse_openlibrary_unknown_isbn():
    assert parse_openlibrary({}, "9999999999999") is None


@pytest.mark.parametrize(
    "payload",
    [None, [], {"ISBN:9780134685991": None}, {"ISBN:9780134685991": []}],
)
def test_parse_openlibrary_malformed_payload_is_a_miss(payload):
    assert parse_openlibrary(payload, "9780134685991") is None


def test_parse_openlibrary_wrong_typed_nested_fields_are_a_miss():
    assert parse_openlibrary({"ISBN:9780134685991": {
        "title": 42,
        "authors": [None, "wrong", {"name": 7}],
        "publish_date": [],
        "publishers": [None, {"name": []}],
    }}, "9780134685991") is None


# --- fetch parsers --------------------------------------------------------


def test_parse_crossref_maps_fields():
    payload = {
        "message": {
            "title": ["Attention Is All You Need"],
            "author": [
                {"given": "Ashish", "family": "Vaswani"},
                {"given": "Noam", "family": "Shazeer"},
            ],
            "issued": {"date-parts": [[2017]]},
            "container-title": ["NeurIPS"],
            "publisher": "Curran",
            "type": "proceedings-article",
            "DOI": "10.5555/3295222.3295349",
            "URL": "https://doi.org/10.5555/3295222.3295349",
            "abstract": "<jats:p>The dominant models...</jats:p>",
        }
    }
    meta = parse_crossref(payload)
    assert meta.title == "Attention Is All You Need"
    assert meta.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert meta.year == 2017
    assert meta.venue == "NeurIPS"
    assert meta.type == "inproceedings"
    assert meta.abstract == "The dominant models..."
    assert meta.pdf_url is None


def test_parse_crossref_extracts_open_access_pdf_link():
    payload = {"message": {
        "title": ["Open Paper"],
        "link": [
            {"content-type": "text/xml", "URL": "https://x/tm.xml"},
            {"content-type": "application/pdf", "URL": "https://x/full.pdf"},
        ],
    }}
    assert parse_crossref(payload).pdf_url == "https://x/full.pdf"


@pytest.mark.parametrize(
    "payload",
    [None, [], {"message": None}, {"message": []}, {"message": {"title": "wrong"}}],
)
def test_parse_crossref_malformed_payload_is_a_miss(payload):
    assert parse_crossref(payload) is None


def test_parse_crossref_wrong_typed_nested_fields_are_a_miss():
    assert parse_crossref({"message": {
        "title": ["Valid"],
        "author": [None, "wrong", {"given": 4, "family": ["bad"]}],
        "issued": {"date-parts": "wrong"},
        "link": [None, {"content-type": [], "URL": {}}],
    }}) is None


def test_parse_arxiv_maps_fields():
    xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry>
        <id>http://arxiv.org/abs/2006.11239v2</id>
        <title>Denoising Diffusion Probabilistic Models</title>
        <summary>We present high quality image synthesis.</summary>
        <published>2020-06-19T00:00:00Z</published>
        <author><name>Jonathan Ho</name></author>
        <author><name>Ajay Jain</name></author>
      </entry>
    </feed>"""
    meta = parse_arxiv(xml)
    assert meta.title == "Denoising Diffusion Probabilistic Models"
    assert meta.authors == ["Jonathan Ho", "Ajay Jain"]
    assert meta.year == 2020
    assert meta.arxiv_id == "2006.11239v2"


def test_parse_arxiv_no_entry():
    xml = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    assert parse_arxiv(xml) is None


@pytest.mark.parametrize("payload", [None, b"not xml", "<html>no feed</html>", "<feed"])
def test_parse_arxiv_malformed_payload_is_a_miss(payload):
    assert parse_arxiv(payload) is None


# --- fetch boundary -------------------------------------------------------


def test_fetch_providers_reject_malformed_payloads_without_raising():
    crossref = mock_client({"api.crossref.org": httpx.Response(200, json=None)})
    assert fetch_crossref("10.5555/example", client=crossref) is None

    arxiv = mock_client({"export.arxiv.org": httpx.Response(200, json=[])})
    assert fetch_arxiv("2006.11239", client=arxiv) is None

    openlibrary = mock_client({"openlibrary.org/api/books": httpx.Response(200, json=[])})
    assert fetch_isbn("9780134685991", client=openlibrary) is None


def test_metadata_response_is_bounded_without_content_length():
    oversized = b"x" * (MAX_METADATA_BYTES + 1)
    client = mock_client({"api.crossref.org": httpx.Response(200, content=oversized)})
    assert fetch_crossref("10.5555/example", client=client) is None


def test_metadata_content_length_is_bounded_before_reading_body():
    response = httpx.Response(
        200,
        content=b"{}",
        headers={"content-length": str(MAX_METADATA_BYTES + 1)},
    )
    client = mock_client({"api.crossref.org": response})
    assert fetch_crossref("10.5555/example", client=client) is None


def _gzip_response(payload: object) -> httpx.Response:
    """A response as a gzipping provider sends it: the body is real gzip bytes
    and the header declares ``content-encoding: gzip``.

    When ``_bounded_get`` streams this, httpx transparently decompresses the
    body via ``iter_bytes()`` but the header still says gzip.  Reconstructing a
    ``Response`` from the *decoded* body while carrying that stale header made
    httpx re-run the decoder on plain bytes and raise ``DecodingError``, which
    the fetch swallowed as a lookup miss — the regression this guards.
    """
    body = gzip.compress(json.dumps(payload).encode())
    return httpx.Response(200, headers={"content-encoding": "gzip"}, content=body)


def test_fetch_isbn_survives_gzip_content_encoding():
    payload = {"ISBN:1098166302": {"title": "AI Engineering", "authors": [{"name": "Chip Huyen"}]}}
    client = mock_client({"openlibrary.org/api/books": _gzip_response(payload)})
    meta = fetch_isbn("1098166302", client=client)
    assert meta is not None
    assert meta.title == "AI Engineering"
    assert meta.authors == ["Chip Huyen"]


def test_fetch_crossref_survives_gzip_content_encoding():
    payload = {"message": {"title": ["Gzipped Work"], "DOI": "10.5555/example"}}
    client = mock_client({"api.crossref.org": _gzip_response(payload)})
    meta = fetch_crossref("10.5555/example", client=client)
    assert meta is not None and meta.title == "Gzipped Work"


def test_fetch_arxiv_survives_gzip_content_encoding():
    atom = (
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry><title>Gzipped Paper</title>"
        "<id>http://arxiv.org/abs/2006.11239</id></entry></feed>"
    )
    body = gzip.compress(atom.encode())
    response = httpx.Response(200, headers={"content-encoding": "gzip"}, content=body)
    client = mock_client({"export.arxiv.org": response})
    meta = fetch_arxiv("2006.11239", client=client)
    assert meta is not None and meta.title == "Gzipped Paper"


def test_metadata_redirect_chain_is_followed_and_validated():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/works/10.5555/example":
            return httpx.Response(302, headers={"location": "/metadata"})
        return httpx.Response(200, json={"message": {"title": ["Safe"]}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    meta = fetch_crossref("10.5555/example", client=client)
    assert meta is not None and meta.title == "Safe"
    assert seen == [
        "https://api.crossref.org/works/10.5555/example",
        "https://api.crossref.org/metadata",
    ]


def test_metadata_private_redirect_is_rejected_before_request():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_crossref("10.5555/example", client=client) is None
    assert seen == ["https://api.crossref.org/works/10.5555/example"]


# --- url helpers ----------------------------------------------------------


def test_is_url():
    assert is_url("https://arxiv.org/abs/1706.03762")
    assert is_url("http://x.com")
    assert not is_url("/local/path.pdf")
    assert not is_url("paper.pdf")


def test_arxiv_id_from_url():
    assert arxiv_id_from_url("https://arxiv.org/abs/1706.03762") == "1706.03762"
    assert arxiv_id_from_url("https://arxiv.org/pdf/2006.11239v2") == "2006.11239v2"
    assert arxiv_id_from_url("https://arxiv.org/pdf/2006.11239.pdf") == "2006.11239"


def test_doi_from_url():
    assert doi_from_url("https://doi.org/10.1000/abc") == "10.1000/abc"
    assert doi_from_url("https://dx.doi.org/10.2000/xyz") == "10.2000/xyz"
    assert doi_from_url("https://example.com") is None


def test_bare_arxiv_id():
    assert bare_arxiv_id("2006.11239") == "2006.11239"
    assert bare_arxiv_id("2006.11239v2") == "2006.11239v2"
    assert bare_arxiv_id("hep-th/9901001") == "hep-th/9901001"
    assert bare_arxiv_id("paper.pdf") is None
    assert bare_arxiv_id("10.5555/3295222.3295349") is None  # a DOI, not an id
    assert bare_arxiv_id("/some/local/path") is None


# --- PDF download ----------------------------------------------------------


def test_arxiv_pdf_url_and_is_pdf_url():
    assert arxiv_pdf_url("2006.11239v2") == "https://arxiv.org/pdf/2006.11239v2.pdf"
    assert is_pdf_url("https://x.org/a/b.pdf")
    assert is_pdf_url("https://x.org/b.PDF?token=1")
    assert not is_pdf_url("https://x.org/abs/2006.11239")


def test_download_pdf_success():
    client = mock_client(
        {"x.pdf": httpx.Response(
            200, content=b"%PDF-1.5 hello", headers={"content-type": "application/pdf"}
        )}
    )
    res = download_pdf("https://h/x.pdf", client=client)
    assert res.error is None and res.path is not None
    assert res.path.read_bytes().startswith(b"%PDF")
    assert res.filename == "x.pdf" and res.mime == "application/pdf"
    res.path.unlink()


def test_download_pdf_accepts_magic_without_pdf_content_type():
    client = mock_client(
        {"x": httpx.Response(200, content=b"%PDF-1.4 body",
                             headers={"content-type": "application/octet-stream"})}
    )
    res = download_pdf("https://h/x", client=client)
    assert res.path is not None
    res.path.unlink()


def test_download_pdf_requires_magic_even_when_content_type_is_pdf():
    client = mock_client(
        {"x.pdf": httpx.Response(
            200, content=b"<html>not a pdf</html>",
            headers={"content-type": "application/pdf"},
        )}
    )
    res = download_pdf("https://h/x.pdf", client=client)
    assert res.path is None and "not a PDF" in res.error


def test_validate_url_blocks_hostname_resolving_to_private(monkeypatch):
    def resolve_private(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port or 0))]

    monkeypatch.setattr(network_mod.socket, "getaddrinfo", resolve_private)
    with pytest.raises(UnsafeURL, match="private or reserved"):
        validate_url("http://public-name.example/paper.pdf")


def test_validate_url_allows_hostname_resolving_to_public():
    assert (
        validate_url("https://api.crossref.org/works")
        == "https://api.crossref.org/works"
    )


def test_download_pdf_rejects_private_initial_destinations():
    for url in (
        "http://127.0.0.1/paper.pdf",
        "http://10.0.0.1/paper.pdf",
        "http://169.254.169.254/paper.pdf",
        "http://192.0.2.1/paper.pdf",
        "http://[::1]/paper.pdf",
        "http://localhost/paper.pdf",
    ):
        called = []

        def handler(request):
            called.append(request)
            raise AssertionError("private destination was requested")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        res = download_pdf(url, client=client)
        assert res.path is None and "private" in res.error.lower()
        assert called == []


def test_download_pdf_validates_each_redirect_target():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/start.pdf":
            return httpx.Response(302, headers={"location": "/hop"})
        if request.url.path == "/hop":
            return httpx.Response(302, headers={"location": "/final.pdf"})
        return httpx.Response(
            200, content=b"%PDF-1.7 body", headers={"content-type": "application/pdf"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    res = download_pdf("https://example.com/start.pdf", client=client)
    assert res.path is not None and res.filename == "final.pdf"
    assert seen == [
        "https://example.com/start.pdf",
        "https://example.com/hop",
        "https://example.com/final.pdf",
    ]
    res.path.unlink()


def test_download_pdf_rejects_private_redirect_before_requesting_it():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    res = download_pdf("https://example.com/start.pdf", client=client)
    assert res.path is None and "private" in res.error.lower()
    assert seen == ["https://example.com/start.pdf"]


def test_download_pdf_stops_redirect_loop():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "/again.pdf"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    res = download_pdf("https://example.com/start.pdf", client=client)
    assert res.path is None and "too many redirects" in res.error
    assert len(seen) == MAX_REDIRECTS + 1


def test_download_pdf_rejects_html():
    client = mock_client(
        {"x": httpx.Response(200, content=b"<html>paywall</html>",
                             headers={"content-type": "text/html"})}
    )
    res = download_pdf("https://h/x", client=client)
    assert res.path is None and "not a PDF" in res.error


def test_download_pdf_404_is_error_not_raise():
    res = download_pdf("https://h/missing.pdf", client=mock_client({}))
    assert res.path is None and "HTTP 404" in res.error


def test_download_pdf_oversize_rejected():
    client = mock_client(
        {"x.pdf": httpx.Response(
            200, content=b"%PDF-" + b"A" * 100, headers={"content-type": "application/pdf"}
        )}
    )
    res = download_pdf("https://h/x.pdf", client=client, max_bytes=10)
    assert res.path is None and "exceeds max size" in res.error
