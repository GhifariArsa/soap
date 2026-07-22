"""Local extraction from a PDF: embedded metadata and page text.

This is the only module that touches PyMuPDF. Extraction is best-effort: a
corrupt or non-PDF file yields ``None`` rather than raising, so the caller can
fall back to manual/filename metadata and keep going.
"""

from dataclasses import dataclass, field
from pathlib import Path

# Embedded-metadata values that carry no real information. PDF producers stamp
# these into the title/author dictionary constantly, so we treat them as absent
# rather than as a weak title hint.
_JUNK_TITLES = {
    "",
    "untitled",
    "untitled document",
    "microsoft word",
    "pdflatex",
    "latex",
    "dvips",
    "no title",
    "title",
}


@dataclass
class PdfExtraction:
    """What we could pull out of a PDF locally, all fields best-effort."""

    embedded: dict[str, str] = field(default_factory=dict)  # title/author/date
    text: str = ""  # first 2 + last 2 pages, concatenated
    page_count: int | None = None
    language: str | None = None


def _clean_embedded(meta: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    title = (meta.get("title") or "").strip()
    if title and title.lower() not in _JUNK_TITLES:
        out["title"] = title
    author = (meta.get("author") or "").strip()
    if author:
        out["author"] = author
    date = (meta.get("creationDate") or "").strip()
    if date:
        out["creation_date"] = date
    return out


def _first_and_last_pages_text(doc, n: int = 2) -> str:
    count = doc.page_count
    # Union of the first n and last n page indices, so short PDFs are not read
    # twice and pages never overlap.
    indices = sorted(set(range(min(n, count))) | set(range(max(0, count - n), count)))
    chunks = []
    for i in indices:
        try:
            chunks.append(doc.load_page(i).get_text())
        except Exception:
            continue
    return "\n".join(chunks)


def extract_pdf(path: Path) -> PdfExtraction | None:
    """Extract embedded metadata and edge-page text from a PDF.

    Returns ``None`` when the file is not a readable PDF. Importing PyMuPDF is
    deferred to call time so the rest of the library imports without it.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None

    try:
        doc = fitz.open(path)
    except Exception:
        return None

    try:
        if not doc.is_pdf:
            return None
        embedded = _clean_embedded(doc.metadata or {})
        text = _first_and_last_pages_text(doc)
        page_count = doc.page_count
        language = _detect_language(doc)
        return PdfExtraction(
            embedded=embedded,
            text=text,
            page_count=page_count,
            language=language,
        )
    finally:
        doc.close()


def _detect_language(doc) -> str | None:
    """Cheap language read from the document catalog, if the producer set it."""
    try:
        lang = doc.language
    except Exception:
        return None
    if not lang:
        return None
    # PyMuPDF returns something like "en-US"; keep just the primary subtag.
    return str(lang).split("-")[0].lower() or None
