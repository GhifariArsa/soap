import os
import shutil
from pathlib import Path

from soap.db import sqlite

DEFAULT_SOAP_DIR = "~/.soap"


def resolve_soap_dir(path_arg: str | None = None) -> Path:
    """Resolve the soap library directory to an absolute path.

    Precedence (first match wins):
      1. ``path_arg`` (the ``--path`` option).
      2. ``$SOAP_DIR`` from the environment, if set and non-empty.
      3. The default, ``~/.soap``.

    ``~`` and environment variables are expanded and the result is resolved to
    an absolute path.
    """
    if path_arg is not None:
        if not path_arg.strip():
            raise ValueError("--path was provided but is empty")
        raw = path_arg
    elif os.environ.get("SOAP_DIR"):
        raw = os.environ["SOAP_DIR"]
    else:
        raw = DEFAULT_SOAP_DIR

    expanded = os.path.expanduser(os.path.expandvars(raw))
    return Path(expanded).resolve()


class Library:
    """The soap library on disk: its directory tree and database."""

    def __init__(self, path: Path):
        self.path = path
        self.inbox = path / "inbox"
        self.documents = path / "documents"
        self.db_path = path / "soap.db"

    @property
    def is_initialized(self) -> bool:
        return self.db_path.exists()

    def create_directories(self) -> None:
        """Create the library directory and its subfolders (idempotent)."""
        self.path.mkdir(parents=True, exist_ok=True)
        self.inbox.mkdir(exist_ok=True)
        self.documents.mkdir(exist_ok=True)

    def initialize_database(self, force: bool = False) -> Path | None:
        """Create and initialize ``soap.db``.

        When ``force`` replaces an existing database, the old file is backed up
        first and the replacement is written atomically (see
        :func:`soap.db.sqlite.create_database`), so the original data survives a
        failed re-init and is never overwritten in place. Returns the backup
        path in that case, otherwise ``None``.
        """
        if not self.db_path.exists():
            sqlite.create_database(self.db_path)
            return None

        if not force:
            return None

        backup_path = self._backup_path()
        shutil.copy2(self.db_path, backup_path)
        # create_database builds in a temp file and atomically renames over the
        # existing db, so if it fails the original library (and this backup)
        # stay intact instead of being deleted up front.
        sqlite.create_database(self.db_path)
        return backup_path

    def _backup_path(self) -> Path:
        """A backup path that never overwrites an earlier backup.

        Prefers ``soap.db.bak`` (the name the spec documents); if that already
        exists from a previous ``--force`` run, falls back to ``soap.db.bak.1``,
        ``soap.db.bak.2``, ... so an original library backup is never destroyed.
        """
        candidate = self.db_path.with_name(self.db_path.name + ".bak")
        if not candidate.exists():
            return candidate
        counter = 1
        while True:
            candidate = self.db_path.with_name(f"{self.db_path.name}.bak.{counter}")
            if not candidate.exists():
                return candidate
            counter += 1
