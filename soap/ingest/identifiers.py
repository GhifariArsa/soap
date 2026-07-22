"""Identifier detection: find a DOI or arXiv ID in text.

These are pure functions with no filesystem or network access so they stay
unit-testable in isolation (see the implementer notes in the PRD). ``v3`` will
plug an LLM extractor in ahead of the same merge step, so keep detection here
free of any I/O.
"""

import re
from dataclasses import dataclass

# A DOI: `10.` then a registrant code, a slash, then the suffix. The suffix is
# greedy but we strip trailing punctuation afterwards, since DOIs frequently
# sit at the end of a sentence or inside parentheses in extracted PDF text.
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")

# Punctuation that is never part of a DOI but routinely trails one in prose.
_DOI_TRAILING = ".,;:)]}>\"'"

# Modern arXiv IDs: `arXiv:2301.12345` or a bare `2301.12345v2`. The `arXiv:`
# prefix is optional, but when absent we still require the YYMM.NNNNN shape so
# we do not match arbitrary decimal numbers in the text.
ARXIV_MODERN_RE = re.compile(
    r"(?:arxiv[:\s]*)?(\d{4}\.\d{4,5}(?:v\d+)?)",
    re.IGNORECASE,
)

# Legacy arXiv IDs: `math.GT/0309136`, `hep-th/9901001v3`. An archive, an
# optional `.subclass`, a slash, then 7 digits.
ARXIV_LEGACY_RE = re.compile(
    r"([a-z][a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Identifier:
    kind: str  # "doi" or "arxiv"
    value: str


def find_doi(text: str) -> str | None:
    """Return the first DOI in ``text``, trailing punctuation stripped."""
    if not text:
        return None
    match = DOI_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(_DOI_TRAILING)


def find_arxiv(text: str) -> str | None:
    """Return the first arXiv ID in ``text`` (modern form preferred)."""
    if not text:
        return None
    modern = ARXIV_MODERN_RE.search(text)
    if modern:
        return modern.group(1)
    legacy = ARXIV_LEGACY_RE.search(text)
    if legacy:
        return legacy.group(1)
    return None


def detect_identifier(*texts: str | None) -> Identifier | None:
    """Detect an identifier across several text sources.

    ``texts`` are searched in the order given (typically extracted body text,
    then embedded metadata, then the filename). A DOI anywhere wins over an
    arXiv ID; only if no DOI is found in any source do we fall back to arXiv.
    First match wins within each kind.
    """
    joined = [t for t in texts if t]
    for text in joined:
        doi = find_doi(text)
        if doi:
            return Identifier("doi", doi)
    for text in joined:
        arxiv = find_arxiv(text)
        if arxiv:
            return Identifier("arxiv", arxiv)
    return None
