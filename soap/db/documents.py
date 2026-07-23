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
from dataclasses import dataclass
from pathlib import Path

from soap.db.sqlite import SqliteDatabase
from soap.models.document import Document, FileRef


@dataclass
class DocumentRow:
    """A lightweight document row for list rendering — no linked tables joined.

    The TUI draws hundreds of these, so it deliberately avoids hydrating authors
    / tags / files for every row. Full hydration happens only for the selected
    document via :meth:`DocumentService.get_document`.
    """

    id: str
    title: str
    year: int | None
    review_status: str
    source: str


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

    # -- reads (TUI / browse) ---------------------------------------------

    def library_counts(self) -> dict[str, int]:
        """Counts for the sidebar's LIBRARY section: all / inbox / to-read."""
        one = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "all": one("SELECT COUNT(*) FROM documents"),
            "inbox": one(
                "SELECT COUNT(*) FROM documents WHERE review_status = 'needs_review'"
            ),
            "toread": one(
                "SELECT COUNT(*) FROM documents WHERE read_status = 'unread'"
            ),
        }

    def inbox_count(self) -> int:
        return self.library_counts()["inbox"]

    def tag_counts(self) -> list[tuple[str, int]]:
        """`(tag, count)` ordered by count desc — for the sidebar TAGS section."""
        rows = self.conn.execute(
            "SELECT t.name, COUNT(dt.document_id) AS c "
            "FROM tags t JOIN document_tags dt ON dt.tag_id = t.id "
            "GROUP BY t.id ORDER BY c DESC, t.name"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def collection_counts(self) -> list[tuple[str, int]]:
        """`(collection, count)` ordered by name — for the COLLECTIONS section."""
        rows = self.conn.execute(
            "SELECT c.name, COUNT(dc.document_id) AS n "
            "FROM collections c JOIN document_collections dc "
            "ON dc.collection_id = c.id "
            "GROUP BY c.id ORDER BY c.name COLLATE NOCASE"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def list_documents(
        self,
        *,
        filter_kind: str = "all",
        filter_value: str | None = None,
        search: str | None = None,
    ) -> list[DocumentRow]:
        """Rows matching a sidebar filter, optionally narrowed by search text.

        ``filter_kind`` is one of ``all``/``inbox``/``toread``/``tag``/
        ``collection``; ``filter_value`` names the tag or collection. ``search``
        is a case-insensitive substring matched across title, venue, author
        names, and tags. Filter and search combine with AND.
        """
        where: list[str] = []
        params: list[object] = []

        if filter_kind == "inbox":
            where.append("d.review_status = 'needs_review'")
        elif filter_kind == "toread":
            where.append("d.read_status = 'unread'")
        elif filter_kind == "tag":
            where.append(
                "EXISTS (SELECT 1 FROM document_tags dt JOIN tags t "
                "ON t.id = dt.tag_id WHERE dt.document_id = d.id AND t.name = ?)"
            )
            params.append(filter_value)
        elif filter_kind == "collection":
            where.append(
                "EXISTS (SELECT 1 FROM document_collections dc JOIN collections c "
                "ON c.id = dc.collection_id "
                "WHERE dc.document_id = d.id AND c.name = ?)"
            )
            params.append(filter_value)

        if search:
            q = f"%{search}%"
            where.append(
                "(d.title LIKE ? OR d.venue LIKE ? "
                "OR EXISTS (SELECT 1 FROM document_authors da JOIN authors a "
                "ON a.id = da.author_id WHERE da.document_id = d.id AND a.name LIKE ?) "
                "OR EXISTS (SELECT 1 FROM document_tags dt2 JOIN tags t2 "
                "ON t2.id = dt2.tag_id WHERE dt2.document_id = d.id AND t2.name LIKE ?))"
            )
            params.extend([q, q, q, q])

        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self.conn.execute(
            "SELECT d.id, d.title, d.year, d.review_status, d.source "
            f"FROM documents d{clause} ORDER BY d.title COLLATE NOCASE",
            params,
        ).fetchall()
        return [DocumentRow(*r) for r in rows]

    def get_document(self, doc_id: str) -> Document | None:
        """Fully hydrate one document (authors ordered, tags, collections, files)."""
        cols = (
            "id, type, title, year, venue, publisher, doi, arxiv_id, isbn, url, "
            "abstract, language, added_at, read_status, source, confidence, "
            "review_status"
        )
        row = self.conn.execute(
            f"SELECT {cols} FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            return None
        data = dict(zip(cols.replace(" ", "").split(","), row))

        data["authors"] = [
            r[0]
            for r in self.conn.execute(
                "SELECT a.name FROM authors a JOIN document_authors da "
                "ON da.author_id = a.id WHERE da.document_id = ? "
                "ORDER BY da.position",
                (doc_id,),
            )
        ]
        data["tags"] = [
            r[0]
            for r in self.conn.execute(
                "SELECT t.name FROM tags t JOIN document_tags dt "
                "ON dt.tag_id = t.id WHERE dt.document_id = ? ORDER BY t.name",
                (doc_id,),
            )
        ]
        data["collections"] = [
            r[0]
            for r in self.conn.execute(
                "SELECT c.name FROM collections c JOIN document_collections dc "
                "ON dc.collection_id = c.id WHERE dc.document_id = ?",
                (doc_id,),
            )
        ]
        data["files"] = [
            FileRef(path=r[0], mime=r[1], sha256=r[2])
            for r in self.conn.execute(
                "SELECT path, mime, sha256 FROM files WHERE document_id = ?",
                (doc_id,),
            )
        ]
        return Document(**data)

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

    def set_review_status(self, doc_id: str, status: str) -> None:
        """Update just the review flag on an indexed document.

        The on-disk ``info.yaml`` is authoritative; callers rewrite it first, so
        this only keeps the index in sync (see :func:`soap.library.set_review_status`).
        """
        with self.conn:
            self.conn.execute(
                "UPDATE documents SET review_status = ? WHERE id = ?",
                (status, doc_id),
            )

    def remove(self, doc_id: str) -> None:
        """Delete a document and all its link rows in one transaction.

        Used to re-index a document after its metadata is edited on disk:
        ``remove`` then ``index`` rebuilds the rows from the fresh ``info.yaml``.
        """
        with self.conn:
            for table in (
                "document_authors",
                "document_tags",
                "document_collections",
                "files",
            ):
                self.conn.execute(
                    f"DELETE FROM {table} WHERE document_id = ?", (doc_id,)
                )
            self.conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    def _upsert_named(self, table: str, name: str) -> int:
        """Insert ``name`` into a ``(id, name UNIQUE)`` table, return its id."""
        self.conn.execute(
            f"INSERT OR IGNORE INTO {table} (name) VALUES (?)", (name,)
        )
        row = self.conn.execute(
            f"SELECT id FROM {table} WHERE name = ?", (name,)
        ).fetchone()
        return row[0]
