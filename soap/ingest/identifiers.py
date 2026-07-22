"""The identifier type shared by URL resolution and metadata fetch.

soap does not scrape identifiers out of PDF contents — metadata comes from an
explicit ``--doi``/``--arxiv``/``--isbn`` flag or an arXiv/DOI/Open Library URL.
This module holds the small value type those paths pass around.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Identifier:
    kind: str  # "doi", "arxiv", or "isbn"
    value: str


def normalize_isbn(isbn: str) -> str:
    """Strip hyphens/spaces so ISBN variants dedupe against each other.

    ``978-0-13-468599-1`` and ``9780134685991`` are the same book; keep only the
    digits and a trailing ISBN-10 check ``X``.
    """
    return re.sub(r"[^0-9Xx]", "", isbn).upper()
