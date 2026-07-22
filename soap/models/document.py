from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReadStatus(str, Enum):
    UNREAD = "unread"
    READING = "reading"
    READ = "read"


class Source(str, Enum):
    """How a document's metadata was ultimately resolved."""

    CROSSREF = "crossref"
    ARXIV = "arxiv"
    OPENLIBRARY = "openlibrary"
    MANUAL = "manual"
    LOCAL = "local"


class ReviewStatus(str, Enum):
    """Whether a filed document still needs a human to look at it."""

    FILED = "filed"
    NEEDS_REVIEW = "needs_review"


class FileRef(BaseModel):
    """One file belonging to a document, stored relative to ``$SOAP_DIR``."""

    path: str
    mime: str | None = None
    sha256: str | None = None


class Document(BaseModel):
    """A single library entry.

    The on-disk ``info.yaml`` is authoritative: every field here round-trips
    through ``model_dump``/validation without loss, and the SQLite index is
    fully reconstructible from it (acceptance criteria 11). Field order mirrors
    Appendix A so serialized YAML reads the way the PRD documents it.
    """

    model_config = ConfigDict(use_enum_values=True)

    id: str
    type: str = "article"
    title: str
    year: int | None = None
    authors: list[str] = Field(default_factory=list)
    venue: str | None = None
    publisher: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    isbn: str | None = None
    url: str | None = None
    abstract: str | None = None
    language: str | None = None
    added_at: str | None = None
    read_status: ReadStatus = ReadStatus.UNREAD
    source: Source = Source.LOCAL
    confidence: float | None = None
    review_status: ReviewStatus = ReviewStatus.FILED
    tags: list[str] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=list)
    files: list[FileRef] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, v: list[str]) -> list[str]:
        return sorted({t.lower().strip() for t in v if t.strip()})

    @field_validator("collections")
    @classmethod
    def dedupe_collections(cls, v: list[str]) -> list[str]:
        # Collections are user-facing names: preserve case and order, drop dups.
        seen: set[str] = set()
        out: list[str] = []
        for c in v:
            c = c.strip()
            if c and c not in seen:
                seen.add(c)
                out.append(c)
        return out
