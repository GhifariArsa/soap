import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# The canonical v1 schema (see PRD Appendix B). This is the single source of
# truth for the database shape; `init` calls into it rather than duplicating DDL.
SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE documents (
    id            TEXT PRIMARY KEY,
    type          TEXT NOT NULL,
    title         TEXT NOT NULL,
    year          INTEGER,
    venue         TEXT,
    publisher     TEXT,
    doi           TEXT,
    arxiv_id      TEXT,
    isbn          TEXT,
    url           TEXT,
    abstract      TEXT,
    language      TEXT,
    added_at      TEXT,
    read_status   TEXT DEFAULT 'unread',
    source        TEXT,
    confidence    REAL,
    review_status TEXT DEFAULT 'filed'
);

CREATE TABLE authors (
    id   INTEGER PRIMARY KEY,
    name TEXT UNIQUE
);

CREATE TABLE document_authors (
    document_id TEXT REFERENCES documents(id),
    author_id   INTEGER REFERENCES authors(id),
    position    INTEGER,
    PRIMARY KEY (document_id, author_id)
);

CREATE TABLE tags (
    id   INTEGER PRIMARY KEY,
    name TEXT UNIQUE
);

CREATE TABLE document_tags (
    document_id TEXT REFERENCES documents(id),
    tag_id      INTEGER REFERENCES tags(id),
    PRIMARY KEY (document_id, tag_id)
);

CREATE TABLE collections (
    id   INTEGER PRIMARY KEY,
    name TEXT UNIQUE
);

CREATE TABLE document_collections (
    document_id   TEXT REFERENCES documents(id),
    collection_id INTEGER REFERENCES collections(id),
    PRIMARY KEY (document_id, collection_id)
);

CREATE TABLE files (
    id          INTEGER PRIMARY KEY,
    document_id TEXT REFERENCES documents(id),
    path        TEXT,
    mime        TEXT,
    sha256      TEXT
);
"""


class SqliteDatabase:
    """Owns the soap SQLite file: schema, atomic creation, and connections.

    A cheap wrapper around a database path — construct one per library. Every
    connection is opened through :meth:`connect` so foreign keys are always
    enforced (SQLite ignores ``REFERENCES`` constraints otherwise). The v1
    schema and version marker live here as the single source of truth.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    SCHEMA_SQL = _SCHEMA_SQL

    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        """Open a connection to this database with foreign keys enforced."""
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def create(self) -> None:
        """Create the database file and apply the v1 schema atomically.

        The database is built in a temporary file in the same directory and
        only renamed into place once the schema and the seed ``meta`` rows
        (``schema_version``/``created_at``) are fully written. An interrupted or
        failing init therefore never leaves a partial ``soap.db`` behind, and an
        existing database is not overwritten until the replacement is complete.
        """
        created_at = datetime.now(timezone.utc).isoformat()
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=".soap.db.", suffix=".tmp"
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            connection = sqlite3.connect(tmp_path)
            try:
                with connection:
                    connection.executescript(self.SCHEMA_SQL)
                    connection.executemany(
                        "INSERT INTO meta (key, value) VALUES (?, ?)",
                        [
                            ("schema_version", str(self.SCHEMA_VERSION)),
                            ("created_at", created_at),
                        ],
                    )
            finally:
                connection.close()
            os.replace(tmp_path, self.path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
