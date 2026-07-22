"""URL sources: resolve an arXiv, DOI, or Open Library (ISBN) link to metadata.

Deliberately minimal. A URL is recognised only if it is an arXiv, DOI, or
``openlibrary.org/isbn`` link, in which case the identifier is parsed straight
from the URL and the metadata is fetched from the matching API. No file is
downloaded and no HTML is scraped — a URL add always produces a metadata-only
document, and the file can be attached later with
``soap add file.pdf --doi/--arxiv/--isbn ...`` (the upgrade path).
"""

import re
from dataclasses import dataclass, field

import httpx

from soap.ingest.fetch import (
    FetchedMetadata,
    fetch_arxiv,
    fetch_crossref,
    fetch_isbn,
)
from soap.ingest.identifiers import Identifier, normalize_isbn

_ARXIV_URL_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:\.pdf)?(?:[?#].*)?$", re.I
)
_DOI_URL_RE = re.compile(r"(?:dx\.)?doi\.org/(10\.\d{4,9}/[^\s?#]+)", re.I)
_ISBN_URL_RE = re.compile(r"openlibrary\.org/isbn/([0-9Xx-]+)", re.I)


@dataclass
class UrlResolution:
    """Metadata a URL source contributes (never a file)."""

    url: str
    identifier: Identifier | None = None
    fetched: FetchedMetadata | None = None
    fallback_title: str | None = None  # used when the URL is unrecognised
    warnings: list[str] = field(default_factory=list)


def is_url(source: str) -> bool:
    return bool(re.match(r"^https?://", source, re.I))


def arxiv_id_from_url(url: str) -> str | None:
    m = _ARXIV_URL_RE.search(url)
    return m.group(1) if m else None


def doi_from_url(url: str) -> str | None:
    m = _DOI_URL_RE.search(url)
    if not m:
        return None
    return m.group(1).rstrip(".,;:)]}>\"'")


def isbn_from_url(url: str) -> str | None:
    m = _ISBN_URL_RE.search(url)
    return m.group(1) if m else None


def resolve_url(
    url: str,
    *,
    fetch: bool = True,
    client: httpx.Client | None = None,
) -> UrlResolution:
    """Resolve an arXiv/DOI URL to an identifier and fetched metadata.

    Unrecognised URLs yield a metadata-only result carrying just the URL as a
    fallback title, so the user can fill the rest in with ``--edit`` or flags.
    """
    result = UrlResolution(url=url)

    arxiv_id = arxiv_id_from_url(url)
    if arxiv_id:
        result.identifier = Identifier("arxiv", arxiv_id)
        if fetch:
            result.fetched = fetch_arxiv(arxiv_id, client=client)
            if result.fetched is None:
                result.warnings.append(f"arXiv lookup failed for {arxiv_id}")
        return result

    doi = doi_from_url(url)
    if doi:
        result.identifier = Identifier("doi", doi)
        if fetch:
            result.fetched = fetch_crossref(doi, client=client)
            if result.fetched is None:
                result.warnings.append(f"Crossref lookup failed for {doi}")
        return result

    isbn = isbn_from_url(url)
    if isbn:
        isbn = normalize_isbn(isbn)
        result.identifier = Identifier("isbn", isbn)
        if fetch:
            result.fetched = fetch_isbn(isbn, client=client)
            if result.fetched is None:
                result.warnings.append(f"Open Library lookup failed for ISBN {isbn}")
        return result

    result.fallback_title = url
    result.warnings.append(
        f"{url} is not an arXiv or DOI link; recording as metadata-only "
        "(use --title/--edit to complete it)"
    )
    return result
