"""URL sources: resolve an arXiv, DOI, or Open Library (ISBN) link to metadata.

A URL is recognised as an arXiv, DOI, ``openlibrary.org/isbn``, or direct-PDF
link; the identifier (if any) is parsed straight from the URL and metadata is
fetched from the matching API. When ``download`` is set, we additionally
best-effort fetch the paper's PDF (arXiv's canonical PDF, a direct ``.pdf`` URL,
or an open-access PDF a DOI exposes) and attach it — see :mod:`soap.ingest.download`.
No HTML is scraped and no paywall is bypassed: a paywalled DOI or an unresolvable
link stays metadata-only, and the file can be attached later with
``soap add file.pdf --doi/--arxiv/--isbn ...`` (the upgrade path).
"""

from pathlib import Path
import re
from dataclasses import dataclass, field

import httpx

from soap.ingest.download import arxiv_pdf_url, download_pdf, is_pdf_url
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
# A bare arXiv id: new-style ``2101.00001`` (optional ``vN``) or old-style
# ``hep-th/9901001``. Matched only against a source that isn't a local file.
_BARE_ARXIV_RE = re.compile(
    r"^(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z][a-z.\-]+/\d{7}(?:v\d+)?)$", re.I
)


@dataclass
class UrlResolution:
    """Metadata a URL source contributes, plus an optionally downloaded PDF."""

    url: str
    identifier: Identifier | None = None
    fetched: FetchedMetadata | None = None
    fallback_title: str | None = None  # used when the URL is unrecognised
    warnings: list[str] = field(default_factory=list)
    # A downloaded PDF (temp file) the caller must move into the library then
    # remove; ``None`` when nothing was downloaded (metadata-only).
    file_path: Path | None = None
    file_name: str | None = None
    file_mime: str | None = None


def is_url(source: str) -> bool:
    return bool(re.match(r"^https?://", source, re.I))


def arxiv_id_from_url(url: str) -> str | None:
    m = _ARXIV_URL_RE.search(url)
    return m.group(1) if m else None


def bare_arxiv_id(source: str) -> str | None:
    """Return ``source`` if it is a bare arXiv id (``2101.00001``), else ``None``."""
    s = source.strip()
    return s if _BARE_ARXIV_RE.match(s) else None


def doi_from_url(url: str) -> str | None:
    m = _DOI_URL_RE.search(url)
    if not m:
        return None
    return m.group(1).rstrip(".,;:)]}>\"'")


def doi_from_input(value: str) -> str | None:
    """Extract and normalize a DOI from a DOI URL or bare DOI."""
    value = value.strip()
    found = doi_from_url(value)
    if found:
        return found
    if re.match(r"^10\.\d{4,9}/\S+$", value, re.I):
        return value.rstrip(".,;:)]}>\"'")
    return None


def canonical_arxiv_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/abs/{arxiv_id}"


def canonical_doi_url(doi: str) -> str:
    return f"https://doi.org/{doi}"


def isbn_from_url(url: str) -> str | None:
    m = _ISBN_URL_RE.search(url)
    return m.group(1) if m else None


def _attach_pdf(
    result: UrlResolution,
    pdf_url: str,
    client: httpx.Client | None,
    *,
    label: str,
    warn: bool = True,
) -> bool:
    """Try to download ``pdf_url`` and attach it to ``result``.

    On success sets the file fields and returns ``True``; on failure (optionally)
    appends a warning and returns ``False``. Never raises.
    """
    owns = client is None
    client = client or httpx.Client()
    try:
        dl = download_pdf(pdf_url, client=client)
    finally:
        if owns:
            client.close()
    if dl.path is not None:
        result.file_path = dl.path
        result.file_name = dl.filename
        result.file_mime = dl.mime
        return True
    if warn:
        result.warnings.append(f"no PDF downloaded for {label} ({dl.error})")
    return False


def resolve_url(
    url: str,
    *,
    fetch: bool = True,
    download: bool = False,
    client: httpx.Client | None = None,
) -> UrlResolution:
    """Resolve an arXiv/DOI/ISBN/PDF URL to an identifier and fetched metadata.

    When ``download`` is set, best-effort fetch the paper's PDF as well (arXiv's
    canonical PDF, a direct ``.pdf`` URL, or an open-access PDF a DOI exposes) and
    attach it to the result. A paywalled DOI or unresolvable link stays
    metadata-only with a clear warning. Unrecognised URLs yield a metadata-only
    result carrying just the URL as a fallback title.
    """
    result = UrlResolution(url=url)

    arxiv_id = arxiv_id_from_url(url) or bare_arxiv_id(url)
    if arxiv_id:
        result.url = url if is_url(url) else canonical_arxiv_url(arxiv_id)
        result.identifier = Identifier("arxiv", arxiv_id)
        if fetch:
            result.fetched = fetch_arxiv(arxiv_id, client=client)
            if result.fetched is None:
                result.warnings.append(f"arXiv lookup failed for {arxiv_id}")
        if download:
            _attach_pdf(result, arxiv_pdf_url(arxiv_id), client, label=f"arXiv {arxiv_id}")
        return result

    doi = doi_from_input(url)
    if doi:
        result.url = url if is_url(url) else canonical_doi_url(doi)
        result.identifier = Identifier("doi", doi)
        if fetch:
            result.fetched = fetch_crossref(doi, client=client)
            if result.fetched is None:
                result.warnings.append(f"Crossref lookup failed for {doi}")
        if download:
            # Prefer an open-access PDF the metadata exposes; otherwise let the
            # DOI resolve and keep the result only if it lands on a real PDF
            # (most paywalled landing pages serve HTML → metadata-only).
            pdf_url = result.fetched.pdf_url if result.fetched else None
            if not _attach_pdf(result, pdf_url or url, client, label=f"DOI {doi}", warn=False):
                result.warnings.append(
                    f"metadata added for DOI {doi} but no downloadable PDF was found; "
                    f"attach one later with `soap add <file> --doi {doi}`"
                )
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

    # A direct link straight at a PDF: no identifier, but a real file to attach.
    if is_pdf_url(url):
        result.fallback_title = url
        if download:
            _attach_pdf(result, url, client, label=url)
        return result

    result.fallback_title = url
    result.warnings.append(
        f"{url} is not an arXiv or DOI link; recording as metadata-only "
        "(use --title/--edit to complete it)"
    )
    return result
