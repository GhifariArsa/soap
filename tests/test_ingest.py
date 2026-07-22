"""Unit tests for the pure ingest functions: no filesystem, no network."""

from soap.ingest.fetch import (
    FetchedMetadata,
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
from soap.ingest.url import arxiv_id_from_url, doi_from_url, is_url, isbn_from_url
from soap.models.document import ReviewStatus, Source


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
