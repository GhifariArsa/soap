"""Metadata fetch from Crossref and arXiv.

Two layers, kept apart so the mapping logic is testable without a network:

* ``parse_crossref`` / ``parse_arxiv`` are pure — bytes in, ``FetchedMetadata``
  out.
* ``fetch_crossref`` / ``fetch_arxiv`` do the HTTP (timeout, one retry on
  timeout/5xx) and delegate to the parsers.

A failed lookup returns ``None``; callers warn and fall back to local metadata.
Nothing here raises on network trouble, so ``soap add`` works fully offline.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

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


def _strip_jats(text: str | None) -> str | None:
    """Crossref abstracts arrive as JATS XML; strip tags to plain text."""
    if not text:
        return None
    cleaned = re.sub(r"<[^>]+>", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def parse_crossref(payload: dict) -> FetchedMetadata:
    """Map a Crossref ``/works/{doi}`` JSON body onto ``FetchedMetadata``."""
    msg = payload.get("message", {})

    titles = msg.get("title") or []
    title = titles[0].strip() if titles else None

    authors: list[str] = []
    for a in msg.get("author", []) or []:
        name = " ".join(p for p in (a.get("given"), a.get("family")) if p).strip()
        if not name:
            name = (a.get("name") or "").strip()
        if name:
            authors.append(name)

    year = None
    for key in ("published", "published-print", "published-online", "issued"):
        parts = (msg.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = int(parts[0][0])
            break

    containers = msg.get("container-title") or []
    venue = containers[0].strip() if containers else None

    cr_type = msg.get("type")
    doc_type = _CROSSREF_TYPE.get(cr_type, cr_type)

    return FetchedMetadata(
        source="crossref",
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        publisher=(msg.get("publisher") or None),
        type=doc_type,
        abstract=_strip_jats(msg.get("abstract")),
        url=msg.get("URL"),
        doi=msg.get("DOI"),
    )


def parse_arxiv(xml_text: str) -> FetchedMetadata | None:
    """Map an arXiv Atom API response onto ``FetchedMetadata``.

    Returns ``None`` when the feed has no entry (unknown ID).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    entry = root.find(f"{_ATOM}entry")
    if entry is None:
        return None

    title_el = entry.find(f"{_ATOM}title")
    title = None
    if title_el is not None and title_el.text:
        title = re.sub(r"\s+", " ", title_el.text).strip()

    authors = []
    for author in entry.findall(f"{_ATOM}author"):
        name_el = author.find(f"{_ATOM}name")
        if name_el is not None and name_el.text:
            authors.append(name_el.text.strip())

    published = entry.find(f"{_ATOM}published")
    year = None
    if published is not None and published.text and len(published.text) >= 4:
        try:
            year = int(published.text[:4])
        except ValueError:
            year = None

    summary_el = entry.find(f"{_ATOM}summary")
    abstract = None
    if summary_el is not None and summary_el.text:
        abstract = re.sub(r"\s+", " ", summary_el.text).strip()

    id_el = entry.find(f"{_ATOM}id")
    url = id_el.text.strip() if id_el is not None and id_el.text else None
    arxiv_id = None
    if url:
        m = re.search(r"arxiv\.org/abs/(.+)$", url)
        if m:
            arxiv_id = m.group(1)

    doi_el = entry.find(f"{_ARXIV_NS}doi")
    doi = doi_el.text.strip() if doi_el is not None and doi_el.text else None

    journal_el = entry.find(f"{_ARXIV_NS}journal_ref")
    venue = journal_el.text.strip() if journal_el is not None and journal_el.text else None

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


def parse_openlibrary(payload: dict, isbn: str) -> FetchedMetadata | None:
    """Map an Open Library ``books?jscmd=data`` body onto ``FetchedMetadata``.

    The response is keyed by ``"ISBN:<isbn>"``; an unknown ISBN yields an empty
    object, which we treat as a miss.
    """
    entry = payload.get(f"ISBN:{isbn}") or {}
    if not entry:
        return None

    authors = [a["name"].strip() for a in entry.get("authors", []) if a.get("name")]

    year = None
    published = entry.get("publish_date")
    if published:
        m = re.search(r"\d{4}", published)
        if m:
            year = int(m.group(0))

    publishers = entry.get("publishers") or []
    publisher = publishers[0].get("name") if publishers and publishers[0] else None

    return FetchedMetadata(
        source="openlibrary",
        title=(entry.get("title") or None),
        authors=authors,
        year=year,
        publisher=publisher,
        type="book",
        url=entry.get("url"),
        isbn=isbn,
    )


def _get(
    url: str,
    *,
    client: httpx.Client,
    timeout: float = REQUEST_TIMEOUT,
) -> httpx.Response | None:
    """GET with one retry on timeout or 5xx; ``None`` on give-up.

    Never raises for network failures — returning ``None`` is how the caller
    learns the lookup failed and should fall back to local metadata.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    for _ in range(2):
        try:
            resp = client.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        except (httpx.TimeoutException, httpx.TransportError):
            continue  # retry once, then fall through to None
        if resp.status_code >= 500:
            continue
        return resp
    return None


def fetch_crossref(doi: str, *, client: httpx.Client | None = None) -> FetchedMetadata | None:
    """Fetch and map Crossref metadata for ``doi`` (``None`` on failure/404)."""
    owns = client is None
    client = client or httpx.Client()
    try:
        resp = _get(CROSSREF_URL.format(doi=doi), client=client)
        if resp is None or resp.status_code != 200:
            return None
        try:
            return parse_crossref(resp.json())
        except (ValueError, KeyError):
            return None
    finally:
        if owns:
            client.close()


def fetch_arxiv(arxiv_id: str, *, client: httpx.Client | None = None) -> FetchedMetadata | None:
    """Fetch and map arXiv metadata for ``arxiv_id`` (``None`` on failure)."""
    owns = client is None
    client = client or httpx.Client()
    try:
        resp = _get(ARXIV_URL.format(id=arxiv_id), client=client)
        if resp is None or resp.status_code != 200:
            return None
        meta = parse_arxiv(resp.text)
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
        resp = _get(OPENLIBRARY_URL.format(isbn=isbn), client=client)
        if resp is None or resp.status_code != 200:
            return None
        try:
            return parse_openlibrary(resp.json(), isbn)
        except (ValueError, KeyError):
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
