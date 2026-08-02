"""Deterministic BibTeX serialization for library documents.

Pure, side-effect-free rendering of :class:`soap.models.document.Document` into a
``.bib`` file. Nothing here touches the database, the filesystem, or the network:
callers hydrate documents through the normal service layer and hand them in, and
the export UI writes the returned text. That keeps the export path within the
project's source-of-truth boundaries (see ``CLAUDE.md``) — it only ever *reads*
the model.

Design points:

- **Entry key** is the document ``id`` — the citekey the ``add`` pipeline already
  derives (``soap/ingest/merge.py:generate_citekey``), which is filesystem- and
  BibTeX-safe by construction.
- **Entry type** maps soap's freeform ``type`` onto a known BibTeX type, falling
  back to ``misc`` so an unusual type never produces an invalid entry.
- **venue** is mapped by meaning, not blindly: a journal for ``article``, a
  ``booktitle`` for conference proceedings, a ``series``-ish note otherwise — we
  never invent a field the metadata cannot justify.
- **Escaping** brace-wraps every value and escapes the LaTeX/BibTeX specials so
  ``&``, ``%``, ``$``, ``#``, ``_``, ``{``, ``}``, ``~``, ``^`` and backslashes
  round-trip as literal text.
- **Deterministic**: entries are emitted in a stable order (by key) and each
  entry's fields in a fixed order, so the same library always yields byte-for-byte
  identical output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from soap.models.document import Document

# soap's ``type`` is a freeform string (default "article"); map the ones we
# recognize onto their BibTeX entry type. Anything unknown degrades to ``misc``
# — always a valid entry — rather than emitting a bogus type.
_TYPE_MAP = {
    "article": "article",
    "journal": "article",
    "journal-article": "article",
    "paper": "article",
    "preprint": "article",
    "book": "book",
    "monograph": "book",
    "inbook": "inbook",
    "bookchapter": "inbook",
    "book-chapter": "inbook",
    "chapter": "incollection",
    "incollection": "incollection",
    "inproceedings": "inproceedings",
    "conference": "inproceedings",
    "conference-paper": "inproceedings",
    "proceedings": "proceedings",
    "thesis": "phdthesis",
    "phdthesis": "phdthesis",
    "mastersthesis": "mastersthesis",
    "techreport": "techreport",
    "report": "techreport",
    "manual": "manual",
    "booklet": "booklet",
    "unpublished": "unpublished",
    "misc": "misc",
}

# The BibTeX entry types for which ``venue`` names a conference/collection rather
# than a journal — its ``booktitle``. Everything else treats ``venue`` as a
# journal title (``journal``), which is the common case (``article``).
_BOOKTITLE_TYPES = {"inproceedings", "proceedings", "incollection", "inbook"}

# LaTeX/BibTeX special characters, each mapped to its literal-text escape. Some
# replacements themselves contain braces, so escaping is a single char-by-char
# pass (see :func:`_escape`) — never a sequence of ``str.replace`` calls, which
# would re-escape the braces the earlier replacements introduced.
_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _escape(value: str) -> str:
    """Escape BibTeX/LaTeX specials in a field value, one character at a time."""
    return "".join(_ESCAPES.get(char, char) for char in value)


def _entry_type(doc: Document) -> str:
    raw = (doc.type or "").strip().lower()
    return _TYPE_MAP.get(raw, "misc")


def _authors_field(doc: Document) -> str | None:
    """Join authors with BibTeX's `` and `` separator.

    Author names are already stored in ``Last, First`` form (the ``add`` pipeline
    and inline-correction contract), which BibTeX parses natively, so we keep them
    verbatim aside from escaping.
    """
    names = [n.strip() for n in doc.authors if n and n.strip()]
    if not names:
        return None
    return " and ".join(_escape(n) for n in names)


def _fields_for(doc: Document, bib_type: str) -> list[tuple[str, str]]:
    """Build the ordered ``(name, escaped-value)`` field list for one document.

    Only fields the metadata actually carries are emitted, mapped by meaning; no
    value is invented. Order is fixed for deterministic output.
    """
    fields: list[tuple[str, str]] = []

    def add(name: str, value: str | None) -> None:
        if value is None:
            return
        text = str(value).strip()
        if text:
            fields.append((name, _escape(text)))

    # title/author/year first — the fields a reader scans for.
    add("title", doc.title)
    authors = _authors_field(doc)  # already escaped
    if authors is not None:
        fields.append(("author", authors))
    add("year", str(doc.year) if doc.year else None)

    # venue mapped by meaning: booktitle for proceedings/collections, else journal.
    if doc.venue and doc.venue.strip():
        venue_field = "booktitle" if bib_type in _BOOKTITLE_TYPES else "journal"
        add(venue_field, doc.venue)

    add("publisher", doc.publisher)
    add("doi", doc.doi)

    # arXiv preprints: the conventional eprint/archivePrefix pair. The value is an
    # id, not free text, but escape it anyway for safety.
    if doc.arxiv_id and doc.arxiv_id.strip():
        add("eprint", doc.arxiv_id)
        fields.append(("archivePrefix", "arXiv"))

    add("isbn", doc.isbn)
    add("url", doc.url)
    add("language", doc.language)
    add("abstract", doc.abstract)

    return fields


def document_to_entry(doc: Document) -> str | None:
    """Render one document as a BibTeX entry, or ``None`` if it cannot be valid.

    A valid entry needs a non-empty key (the citekey/``id``) and at least one
    field; a record missing both a title and every optional field, or lacking an
    id, cannot produce a meaningful entry and returns ``None`` so the caller can
    report it rather than emit a broken stub.
    """
    key = (doc.id or "").strip()
    if not key:
        return None
    bib_type = _entry_type(doc)
    fields = _fields_for(doc, bib_type)
    if not fields:
        return None
    lines = [f"@{bib_type}{{{key},"]
    for name, value in fields:
        lines.append(f"  {name} = {{{value}}},")
    lines.append("}")
    return "\n".join(lines)


@dataclass
class BibtexResult:
    """The outcome of serializing a set of documents.

    ``text`` is the full ``.bib`` file body (empty string if nothing serialized);
    ``exported_ids`` and ``skipped_ids`` partition the input by whether each
    document produced a valid entry, so callers can report both the count written
    and any records dropped.
    """

    text: str
    exported_ids: list[str] = field(default_factory=list)
    skipped_ids: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.exported_ids)


def serialize_documents(docs: list[Document]) -> BibtexResult:
    """Serialize documents to a deterministic BibTeX document.

    Entries are ordered by citekey (case-insensitive, stable) so the same set of
    documents always yields identical bytes regardless of input order. Records
    that cannot produce a valid entry are collected in ``skipped_ids`` instead of
    being silently dropped.
    """
    ordered = sorted(docs, key=lambda d: ((d.id or "").lower(), d.id or ""))
    entries: list[str] = []
    exported: list[str] = []
    skipped: list[str] = []
    for doc in ordered:
        entry = document_to_entry(doc)
        if entry is None:
            skipped.append(doc.id or "")
            continue
        entries.append(entry)
        exported.append(doc.id)
    text = "\n\n".join(entries)
    if text:
        text += "\n"
    return BibtexResult(text=text, exported_ids=exported, skipped_ids=skipped)
