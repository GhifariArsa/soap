"""Document persistence — the database service layer for the ``add`` path.

``DocumentService`` is the single place that knows how a ``Document`` maps onto
the SQLite tables. The ``add`` pipeline (and, later, the inbox agent, ``serve``,
and reindex) go through this service rather than issuing SQL directly, so the
row-linking logic lives in one place. It wraps a connection and is usable as a
context manager::

    with DocumentService.open(library.db_path) as docs:
        if docs.find_by_sha256(sha) is None:
            docs.index(document)
"""

import sqlite3
from pathlib import Path

from soap.db.sqlite import SqliteDatabase
from soap.models.document import Document, FileRef


class DocumentService:
    """Read/write access to documents and their linked rows."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @classmethod
    def open(cls, db_path: Path) -> "DocumentService":
        """Open a service over a fresh connection to ``db_path``."""
        return cls(SqliteDatabase(db_path).connect())

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "DocumentService":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # -- duplicate / existence checks -------------------------------------

    def find_by_sha256(self, sha256: str) -> str | None:
        """Document id owning a file with this content hash, if any."""
        row = self.conn.execute(
            "SELECT document_id FROM files WHERE sha256 = ? LIMIT 1", (sha256,)
        ).fetchone()
        return row[0] if row else None

    def find_by_identifier(
        self,
        doi: str | None = None,
        arxiv_id: str | None = None,
        isbn: str | None = None,
    ) -> str | None:
        """Document id matching this DOI, arXiv ID, or ISBN, if any is set."""
        if doi:
            row = self.conn.execute(
                "SELECT id FROM documents WHERE doi = ? LIMIT 1", (doi,)
            ).fetchone()
            if row:
                return row[0]
        if arxiv_id:
            row = self.conn.execute(
                "SELECT id FROM documents WHERE arxiv_id = ? LIMIT 1", (arxiv_id,)
            ).fetchone()
            if row:
                return row[0]
        if isbn:
            row = self.conn.execute(
                "SELECT id FROM documents WHERE isbn = ? LIMIT 1", (isbn,)
            ).fetchone()
            if row:
                return row[0]
        return None

    def id_exists(self, doc_id: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM documents WHERE id = ? LIMIT 1", (doc_id,)
            ).fetchone()
            is not None
        )

    def has_file(self, doc_id: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM files WHERE document_id = ? LIMIT 1", (doc_id,)
            ).fetchone()
            is not None
        )

    # -- writes -----------------------------------------------------------

    def index(self, doc: Document) -> None:
        """Insert a fully linked document row set in one transaction.

        Writes ``documents`` plus the author/tag/collection/file link rows. The
        whole set commits or rolls back together. ``doc``'s enum fields are
        already plain strings (the model uses ``use_enum_values``).
        """
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO documents (
                    id, type, title, year, venue, publisher, doi, arxiv_id,
                    isbn, url, abstract, language, added_at, read_status,
                    source, confidence, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc.id, doc.type, doc.title, doc.year, doc.venue,
                    doc.publisher, doc.doi, doc.arxiv_id, doc.isbn, doc.url,
                    doc.abstract, doc.language, doc.added_at, doc.read_status,
                    doc.source, doc.confidence, doc.review_status,
                ),
            )

            for position, name in enumerate(doc.authors):
                author_id = self._upsert_named("authors", name)
                self.conn.execute(
                    "INSERT OR REPLACE INTO document_authors "
                    "(document_id, author_id, position) VALUES (?, ?, ?)",
                    (doc.id, author_id, position),
                )

            for tag in doc.tags:
                tag_id = self._upsert_named("tags", tag)
                self.conn.execute(
                    "INSERT OR IGNORE INTO document_tags "
                    "(document_id, tag_id) VALUES (?, ?)",
                    (doc.id, tag_id),
                )

            for collection in doc.collections:
                collection_id = self._upsert_named("collections", collection)
                self.conn.execute(
                    "INSERT OR IGNORE INTO document_collections "
                    "(document_id, collection_id) VALUES (?, ?)",
                    (doc.id, collection_id),
                )

            for f in doc.files:
                self.conn.execute(
                    "INSERT INTO files (document_id, path, mime, sha256) "
                    "VALUES (?, ?, ?, ?)",
                    (doc.id, f.path, f.mime, f.sha256),
                )

    def attach_file(self, doc_id: str, file_ref: FileRef) -> None:
        """Attach a file to an existing document and clear its review flag.

        The identifier-upgrade path: a metadata-only entry gains its PDF.
        """
        with self.conn:
            self.conn.execute(
                "INSERT INTO files (document_id, path, mime, sha256) "
                "VALUES (?, ?, ?, ?)",
                (doc_id, file_ref.path, file_ref.mime, file_ref.sha256),
            )
            self.conn.execute(
                "UPDATE documents SET review_status = 'filed' WHERE id = ?",
                (doc_id,),
            )

    def _upsert_named(self, table: str, name: str) -> int:
        """Insert ``name`` into a ``(id, name UNIQUE)`` table, return its id."""
        self.conn.execute(
            f"INSERT OR IGNORE INTO {table} (name) VALUES (?)", (name,)
        )
        row = self.conn.execute(
            f"SELECT id FROM {table} WHERE name = ?", (name,)
        ).fetchone()
        return row[0]
