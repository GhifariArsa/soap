"""Bounded metadata fetches from Crossref, arXiv and Open Library.

The HTTP boundary is deliberately defensive: requests use a timeout, follow
redirects one validated hop at a time, and cap the response body before a JSON
or XML parser sees it.  Malformed provider data is a normal lookup miss, so
``soap add`` can continue with manual/filename metadata.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

from soap.ingest.network import (
    MAX_REDIRECTS,
    UnsafeURL,
    is_redirect,
    redirect_target,
    validate_url,
)

# Crossref etiquette: identify ourselves with a contact so they can reach us if
# our traffic misbehaves. This also lands us in their "polite pool".
CONTACT = "team@monashmed.tech"
USER_AGENT = f"soap/0.0.1 (mailto:{CONTACT})"

CROSSREF_URL = "https://api.crossref.org/works/{doi}"
ARXIV_URL = "http://export.arxiv.org/api/query?id_list={id}"
OPENLIBRARY_URL = (
    "https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
)

REQUEST_TIMEOUT = 10.0
# Metadata is small.  This protects the process even when a server omits
# Content-Length or lies about it; PDF downloads have their separate 100 MiB
# cap in download.py.
MAX_METADATA_BYTES = 2 * 1024 * 1024

# Crossref work types → BibTeX types. Anything unmapped passes through verbatim.
_CROSSREF_TYPE = {
    "journal-article": "article",
    "proceedings-article": "inproceedings",
    "book-chapter": "incollection",
    "book": "book",
    "monograph": "book",
    "dissertation": "phdthesis",
    "report": "techreport",
    "posted-content": "misc",
}

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"


@dataclass
class FetchedMetadata:
    """Normalized metadata from an online source, mapped onto our model."""

    source: str  # "crossref", "arxiv", or "openlibrary"
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    publisher: str | None = None
    type: str | None = None
    abstract: str | None = None
    url: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    isbn: str | None = None
    # An open-access/full-text PDF URL the source exposed (Crossref ``link``),
    # if any. Used only to best-effort download a file for a link add.
    pdf_url: str | None = None


def _text(value) -> str | None:
    """Return a stripped string, rejecting provider scalar type surprises."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _first_text(value) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        text = _text(item)
        if text:
            return text
    return None


def _strip_jats(text: str | None) -> str | None:
    """Crossref abstracts arrive as JATS XML; strip tags to plain text."""
    text = _text(text)
    if not text:
        return None
    cleaned = re.sub(r"<[^>]+>", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _valid_optional_string(value) -> bool:
    return value is None or isinstance(value, str)


def _crossref_payload_valid(message: dict) -> bool:
    """Check the JSON shapes used by the mapper before field extraction."""
    if "title" not in message:
        return False
    titles = message["title"]
    if (
        not isinstance(titles, list)
        or not titles
        or any(not isinstance(item, str) for item in titles)
        or not any(item.strip() for item in titles)
    ):
        return False
    if "container-title" in message:
        containers = message["container-title"]
        if not isinstance(containers, list) or any(
            not isinstance(item, str) for item in containers
        ):
            return False
    for key in ("publisher", "type", "abstract", "URL", "DOI"):
        if key in message and not _valid_optional_string(message[key]):
            return False

    authors = message.get("author")
    if authors is not None:
        if not isinstance(authors, list):
            return False
        for author in authors:
            if not isinstance(author, dict):
                return False
            if any(
                key in author and not _valid_optional_string(author[key])
                for key in ("given", "family", "name")
            ):
                return False

    links = message.get("link")
    if links is not None:
        if not isinstance(links, list):
            return False
        for link in links:
            if not isinstance(link, dict):
                return False
            if any(
                key in link and not _valid_optional_string(link[key])
                for key in ("content-type", "URL")
            ):
                return False

    for key in ("published", "published-print", "published-online", "issued"):
        date = message.get(key)
        if date is None:
            continue
        if not isinstance(date, dict):
            return False
        parts = date.get("date-parts")
        if parts is None:
            continue
        if not isinstance(parts, list) or any(not isinstance(part, list) for part in parts):
            return False
        if any(
            any(isinstance(item, bool) or not isinstance(item, (int, str)) for item in part)
            for part in parts
        ):
            return False
    return True


def _crossref_year(message: dict) -> int | None:
    for key in ("published", "published-print", "published-online", "issued"):
        value = message.get(key)
        if not isinstance(value, dict):
            continue
        parts = value.get("date-parts")
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
            continue
        first = parts[0][0] if parts[0] else None
        if isinstance(first, bool):
            continue
        if isinstance(first, int):
            return first
        # Some providers serialize date parts as strings.  Accept only a
        # complete integer rather than allowing arbitrary conversion errors.
        if isinstance(first, str) and re.fullmatch(r"\d{1,4}", first.strip()):
            return int(first.strip())
    return None


def parse_crossref(payload: object) -> FetchedMetadata | None:
    """Map a Crossref ``/works/{doi}`` JSON body onto our model.

    Crossref occasionally returns an error-shaped body with a 200 status.  A
    wrong top-level shape is a lookup miss, not an exception for ``add`` to
    leak to the CLI.
    """
    if not isinstance(payload, dict):
        return None
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    # A present collection field with the wrong container type is an invalid
    # provider payload, not an empty optional field.
    if not _crossref_payload_valid(message):
        return None

    title = _first_text(message.get("title"))

    authors: list[str] = []
    raw_authors = message.get("author")
    if isinstance(raw_authors, list):
        for author in raw_authors:
            if not isinstance(author, dict):
                continue
            given = _text(author.get("given"))
            family = _text(author.get("family"))
            name = " ".join(p for p in (given, family) if p).strip()
            if not name:
                name = _text(author.get("name")) or ""
            if name:
                authors.append(name)

    containers = message.get("container-title")
    venue = _first_text(containers)

    cr_type = _text(message.get("type"))
    doc_type = _CROSSREF_TYPE.get(cr_type, cr_type)

    # An open-access full-text PDF link, if Crossref exposes one. Most works
    # don't; paywalled ones list only text-mining links behind the paywall.
    pdf_url = None
    raw_links = message.get("link")
    if isinstance(raw_links, list):
        for link in raw_links:
            if not isinstance(link, dict):
                continue
            content_type = (_text(link.get("content-type")) or "").lower()
            candidate = _text(link.get("URL"))
            if content_type == "application/pdf" and candidate:
                pdf_url = candidate
                break

    return FetchedMetadata(
        source="crossref",
        title=title,
        authors=authors,
        year=_crossref_year(message),
        venue=venue,
        publisher=_text(message.get("publisher")),
        type=doc_type,
        abstract=_strip_jats(message.get("abstract")),
        url=_text(message.get("URL")),
        doi=_text(message.get("DOI")),
        pdf_url=pdf_url,
    )


def parse_arxiv(xml_text: object) -> FetchedMetadata | None:
    """Map an arXiv Atom API response onto ``FetchedMetadata``.

    Returns ``None`` for malformed XML, a non-feed document, or a feed with no
    entry (unknown ID).
    """
    if not isinstance(xml_text, str):
        return None
    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, TypeError, ValueError):
        return None

    if root.tag != f"{_ATOM}feed":
        return None
    entry = root.find(f"{_ATOM}entry")
    if entry is None:
        return None

    title_el = entry.find(f"{_ATOM}title")
    title = None
    if title_el is not None and isinstance(title_el.text, str):
        title = re.sub(r"\s+", " ", title_el.text).strip() or None
    if not title:
        return None

    authors = []
    for author in entry.findall(f"{_ATOM}author"):
        name_el = author.find(f"{_ATOM}name")
        name = _text(name_el.text if name_el is not None else None)
        if name:
            authors.append(name)

    published = entry.find(f"{_ATOM}published")
    year = None
    published_text = _text(published.text if published is not None else None)
    if published_text and len(published_text) >= 4 and published_text[:4].isdigit():
        year = int(published_text[:4])

    summary_el = entry.find(f"{_ATOM}summary")
    summary = _text(summary_el.text if summary_el is not None else None)
    abstract = re.sub(r"\s+", " ", summary).strip() if summary else None

    id_el = entry.find(f"{_ATOM}id")
    url = _text(id_el.text if id_el is not None else None)
    arxiv_id = None
    if url:
        match = re.search(r"arxiv\.org/abs/([^?#]+)$", url)
        if match:
            arxiv_id = match.group(1)

    doi_el = entry.find(f"{_ARXIV_NS}doi")
    doi = _text(doi_el.text if doi_el is not None else None)

    journal_el = entry.find(f"{_ARXIV_NS}journal_ref")
    venue = _text(journal_el.text if journal_el is not None else None)

    return FetchedMetadata(
        source="arxiv",
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        type="article",
        abstract=abstract,
        url=url,
        doi=doi,
        arxiv_id=arxiv_id,
    )


def parse_openlibrary(payload: object, isbn: str) -> FetchedMetadata | None:
    """Map an Open Library ``books?jscmd=data`` body onto our model.

    The response is keyed by ``"ISBN:<isbn>"``; an unknown ISBN or malformed
    entry is a miss.
    """
    if not isinstance(payload, dict) or not isinstance(isbn, str):
        return None
    entry = payload.get(f"ISBN:{isbn}")
    if not isinstance(entry, dict) or not entry:
        return None
    if not isinstance(entry.get("title"), str) or not entry["title"].strip():
        return None
    for key in ("publish_date", "url"):
        if key in entry and not _valid_optional_string(entry[key]):
            return None

    raw_authors = entry.get("authors")
    if raw_authors is not None:
        if not isinstance(raw_authors, list):
            return None
        for author in raw_authors:
            if not isinstance(author, dict) or (
                "name" in author and not _valid_optional_string(author["name"])
            ):
                return None

    raw_publishers = entry.get("publishers")
    if raw_publishers is not None:
        if not isinstance(raw_publishers, list):
            return None
        for publisher in raw_publishers:
            if not isinstance(publisher, dict) or (
                "name" in publisher
                and not _valid_optional_string(publisher["name"])
            ):
                return None

    authors: list[str] = []
    if isinstance(raw_authors, list):
        for author in raw_authors:
            if isinstance(author, dict):
                name = _text(author.get("name"))
                if name:
                    authors.append(name)

    year = None
    published = _text(entry.get("publish_date"))
    if published:
        match = re.search(r"\d{4}", published)
        if match:
            year = int(match.group(0))

    publisher = None
    if isinstance(raw_publishers, list):
        for item in raw_publishers:
            if isinstance(item, dict):
                publisher = _text(item.get("name"))
                if publisher:
                    break

    return FetchedMetadata(
        source="openlibrary",
        title=_text(entry.get("title")),
        authors=authors,
        year=year,
        publisher=publisher,
        type="book",
        url=_text(entry.get("url")),
        isbn=isbn,
    )


def _content_length(response: httpx.Response) -> int | None:
    value = response.headers.get("content-length")
    if value is None:
        return None
    try:
        length = int(value)
    except (TypeError, ValueError):
        return None
    return length if length >= 0 else None


def _read_bounded(response: httpx.Response, max_bytes: int) -> bytes | None:
    """Read a response body without ever accumulating more than the cap."""
    length = _content_length(response)
    if length is not None and length > max_bytes:
        return None
    body = bytearray()
    try:
        for chunk in response.iter_bytes():
            if not chunk:
                continue
            if len(body) + len(chunk) > max_bytes:
                return None
            body.extend(chunk)
    except (httpx.HTTPError, OSError):
        return None
    return bytes(body)


def _bounded_get(
    url: str,
    *,
    client: httpx.Client,
    headers: dict[str, str],
    timeout: float,
    max_bytes: int,
) -> httpx.Response | None:
    """GET one URL, validating each redirect and bounding its final body."""
    current = url
    for hop in range(MAX_REDIRECTS + 1):
        try:
            validate_url(current)
            with client.stream(
                "GET", current, headers=headers, timeout=timeout,
                follow_redirects=False,
            ) as response:
                if is_redirect(response.status_code):
                    if hop >= MAX_REDIRECTS:
                        return None
                    current = redirect_target(
                        current, response.headers.get("location")
                    )
                    continue
                body = _read_bounded(response, max_bytes)
                if body is None:
                    return None
                # ``body`` is what ``iter_bytes()`` yielded, i.e. already
                # transparently decompressed by httpx.  The provider's
                # ``Content-Encoding`` (and its now-stale ``Content-Length``)
                # no longer describe it, so drop both before handing the
                # headers back to a reconstructed ``Response`` — otherwise
                # ``httpx.Response.__init__`` re-applies the decoder to plain
                # bytes and raises ``DecodingError``, which the caller would
                # swallow as a lookup miss.
                out_headers = httpx.Headers(response.headers)
                out_headers.pop("content-encoding", None)
                out_headers.pop("content-length", None)
                return httpx.Response(
                    response.status_code,
                    headers=out_headers,
                    content=body,
                    request=response.request,
                    extensions=response.extensions,
                )
        except (httpx.HTTPError, OSError):
            return None
    return None


def _get(
    url: str,
    *,
    client: httpx.Client,
    timeout: float = REQUEST_TIMEOUT,
) -> httpx.Response | None:
    """GET with one retry on timeout or 5xx; ``None`` on give-up.

    Redirects are manual so every target receives the SSRF policy.  The body is
    bounded before a parser sees it.  Network failures and unsafe destinations
    are lookup misses, allowing callers to fall back to local metadata.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, application/atom+xml"}
    for _ in range(2):
        try:
            response = _bounded_get(
                url,
                client=client,
                headers=headers,
                timeout=timeout,
                max_bytes=MAX_METADATA_BYTES,
            )
        except UnsafeURL:
            # A blocked redirect is deterministic; do not repeat the provider
            # request just because the selected URL was unsafe.
            return None
        if response is None:
            continue
        if response.status_code >= 500:
            continue
        return response
    return None


def fetch_crossref(doi: str, *, client: httpx.Client | None = None) -> FetchedMetadata | None:
    """Fetch and map Crossref metadata for ``doi`` (``None`` on failure/404)."""
    owns = client is None
    client = client or httpx.Client()
    try:
        response = _get(CROSSREF_URL.format(doi=doi), client=client)
        if response is None or response.status_code != 200:
            return None
        try:
            payload = json.loads(response.content)
            return parse_crossref(payload)
        except (TypeError, ValueError, UnicodeError, KeyError, IndexError, AttributeError):
            return None
    finally:
        if owns:
            client.close()


def fetch_arxiv(arxiv_id: str, *, client: httpx.Client | None = None) -> FetchedMetadata | None:
    """Fetch and map arXiv metadata for ``arxiv_id`` (``None`` on failure)."""
    owns = client is None
    client = client or httpx.Client()
    try:
        response = _get(ARXIV_URL.format(id=arxiv_id), client=client)
        if response is None or response.status_code != 200:
            return None
        try:
            meta = parse_arxiv(response.text)
        except (TypeError, ValueError, UnicodeError, AttributeError):
            return None
        if meta and not meta.arxiv_id:
            meta.arxiv_id = arxiv_id
        return meta
    finally:
        if owns:
            client.close()


def fetch_isbn(isbn: str, *, client: httpx.Client | None = None) -> FetchedMetadata | None:
    """Fetch and map Open Library metadata for ``isbn`` (``None`` on failure)."""
    owns = client is None
    client = client or httpx.Client()
    try:
        response = _get(OPENLIBRARY_URL.format(isbn=isbn), client=client)
        if response is None or response.status_code != 200:
            return None
        try:
            payload = json.loads(response.content)
            return parse_openlibrary(payload, isbn)
        except (TypeError, ValueError, UnicodeError, KeyError, IndexError, AttributeError):
            return None
    finally:
        if owns:
            client.close()


def fetch_for_identifier(identifier, *, client: httpx.Client | None = None) -> FetchedMetadata | None:
    """Dispatch to Crossref, arXiv, or Open Library by the identifier's kind."""
    if identifier is None:
        return None
    if identifier.kind == "doi":
        return fetch_crossref(identifier.value, client=client)
    if identifier.kind == "arxiv":
        return fetch_arxiv(identifier.value, client=client)
    if identifier.kind == "isbn":
        return fetch_isbn(identifier.value, client=client)
    return None
