from enum import Enum

from pydantic import BaseModel, Field, field_validator


class DocumentType(str, Enum):
    ARTICLE = "article"
    BOOK = "book"
    THESIS = "thesis"
    REPORT = "report"
    PAPER = "paper"
    OTHER = "other"


class ReadStatus(str, Enum):
    UNREAD = "unread"
    READING = "reading"
    READ = "read"


class DocumentStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Document(BaseModel):
    id: str
    DocumentType: DocumentType
    title: str
    year: int | None = None
    authors: list[str] = Field(default_factory=list)
    doi: str | None = None
    arxiv_id: str | None = None
    abstract: str | None = None
    tags: list[str] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=list)
    read_status: ReadStatus = ReadStatus.UNREAD
    document_status: DocumentStatus = DocumentStatus.ACTIVE

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, v: list[str]) -> list[str]:
        return sorted({t.lower().strip() for t in v})
