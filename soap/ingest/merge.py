"""Metadata merge and citekey generation — pure, no I/O.

The merge implements the precedence (flags > fetched > filename) and decides
``source`` / ``confidence`` / ``review_status``. Metadata is never scraped from
the PDF, so there is no embedded-metadata layer: a local file with no identifier
falls back to a filename-derived title and is flagged for review.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from soap.ingest.fetch import FetchedMetadata
from soap.ingest.identifiers import normalize_isbn
from soap.models.document import ReviewStatus, Source

# Title words too generic to anchor a citekey. Kept small on purpose: the goal
# is to skip leading articles, not to strip every common word.
_STOPWORDS = {
    "a", "an", "the", "on", "in", "of", "for", "to", "and", "or", "with",
    "is", "are", "at", "by", "from", "into", "over", "as",
}

# Confidence by resolution source. Fetched identifiers are trustworthy; a
# local-only guess is not.
_CONFIDENCE = {
    Source.CROSSREF: 0.95,
    Source.ARXIV: 0.9,
    Source.OPENLIBRARY: 0.9,
    Source.MANUAL: 0.8,
    Source.LOCAL: 0.3,
}


@dataclass
class Overrides:
    """Explicit CLI flags. Any set field wins over everything detected."""

    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    type: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    isbn: str | None = None
    tags: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)

    @property
    def has_any(self) -> bool:
        return any(
            [
                self.title,
                self.authors,
                self.year is not None,
                self.type,
                self.doi,
                self.arxiv_id,
                self.isbn,
            ]
        )


@dataclass
class MergedMetadata:
    """The resolved metadata fields, ready to build a ``Document`` from."""

    title: str | None
    authors: list[str]
    year: int | None
    type: str
    venue: str | None
    publisher: str | None
    doi: str | None
    arxiv_id: str | None
    isbn: str | None
    url: str | None
    abstract: str | None
    language: str | None
    source: Source
    confidence: float
    review_status: ReviewStatus


def _fold(text: str) -> str:
    """ASCII-fold and strip to lowercase alphanumerics only."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]", "", ascii_only).lower()


def _lastname(author: str) -> str:
    """Best-effort surname from a display name.

    Handles both ``"Ashish Vaswani"`` (last token) and ``"Vaswani, Ashish"``
    (part before the comma).
    """
    author = author.strip()
    if "," in author:
        return author.split(",", 1)[0].strip()
    parts = author.split()
    return parts[-1] if parts else ""


def _first_title_word(title: str) -> str:
    for raw in title.split():
        word = _fold(raw)
        if word and word not in _STOPWORDS:
            return word
    # Everything was a stopword: fall back to the first non-empty folded token.
    for raw in title.split():
        word = _fold(raw)
        if word:
            return word
    return ""


def generate_citekey(
    authors: list[str], year: int | None, title: str | None
) -> str:
    """Build ``{lastname}{year}{titleword}``, folded and filesystem-safe.

    Falls back gracefully: no author uses the title as the leading part, no year
    is omitted. Guaranteed to return a non-empty, alphanumeric string.
    """
    title_word = _first_title_word(title or "")
    year_part = str(year) if year else ""

    if authors:
        lead = _fold(_lastname(authors[0]))
        key = f"{lead}{year_part}{title_word}"
    else:
        # No author: the title stands in for the author slot; do not also append
        # the title word, which would duplicate it.
        key = f"{title_word}{year_part}"

    return key or "untitled"


def unique_citekey(base: str, exists) -> str:
    """Return ``base`` or the first free ``base``+a/b/c… per ``exists(key)``."""
    if not exists(base):
        return base
    # a, b, …, z, then aa, ab, … — effectively unbounded.
    suffix_len = 1
    while True:
        for n in range(26**suffix_len):
            suffix = _to_alpha(n, suffix_len)
            candidate = base + suffix
            if not exists(candidate):
                return candidate
        suffix_len += 1


def _to_alpha(n: int, width: int) -> str:
    chars = []
    for _ in range(width):
        chars.append(chr(ord("a") + n % 26))
        n //= 26
    return "".join(reversed(chars))


def _first(*values):
    for v in values:
        if v:
            return v
    return None


def merge_metadata(
    overrides: Overrides,
    fetched: FetchedMetadata | None,
    filename_title: str | None,
) -> MergedMetadata:
    """Merge all sources by precedence and classify the result.

    Precedence per field, highest first: explicit flags, fetched metadata
    (Crossref/arXiv/Open Library), filename guess (title only). There is no
    PDF-derived layer.
    """
    title = _first(
        overrides.title,
        fetched.title if fetched else None,
        filename_title,
    )

    authors = _first(
        overrides.authors or None,
        (fetched.authors if fetched else None) or None,
    ) or []

    year = _first(
        overrides.year,
        fetched.year if fetched else None,
    )

    doc_type = _first(
        overrides.type,
        fetched.type if fetched else None,
    ) or "article"

    doi = _first(overrides.doi, fetched.doi if fetched else None)
    arxiv_id = _first(overrides.arxiv_id, fetched.arxiv_id if fetched else None)
    isbn = _first(overrides.isbn, fetched.isbn if fetched else None)
    if isbn:
        isbn = normalize_isbn(isbn)  # canonicalize so hyphen variants match

    venue = fetched.venue if fetched else None
    publisher = fetched.publisher if fetched else None
    url = fetched.url if fetched else None
    abstract = fetched.abstract if fetched else None
    language = None

    # How was this resolved? Authoritative APIs (Crossref/arXiv/Open Library) are
    # trusted and filed; a user-driven manual add is trusted too. Anything left
    # is a filename-only guess that needs review.
    _API_SOURCE = {
        "crossref": Source.CROSSREF,
        "arxiv": Source.ARXIV,
        "openlibrary": Source.OPENLIBRARY,
    }
    if fetched is not None and fetched.source in _API_SOURCE:
        source = _API_SOURCE[fetched.source]
        review = ReviewStatus.FILED
    elif overrides.has_any:
        source = Source.MANUAL
        review = ReviewStatus.FILED
    else:
        source = Source.LOCAL
        review = ReviewStatus.NEEDS_REVIEW

    return MergedMetadata(
        title=title,
        authors=authors,
        year=year,
        type=doc_type,
        venue=venue,
        publisher=publisher,
        doi=doi,
        arxiv_id=arxiv_id,
        isbn=isbn,
        url=url,
        abstract=abstract,
        language=language,
        source=source,
        confidence=_CONFIDENCE[source],
        review_status=review,
    )
