import hashlib
import mimetypes
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

from soap.db import sqlite
from soap.db.documents import DocumentService
from soap.ingest import url as url_mod
from soap.ingest.fetch import fetch_for_identifier
from soap.ingest.identifiers import Identifier, normalize_isbn
from soap.ingest.merge import Overrides, generate_citekey, merge_metadata, unique_citekey
from soap.models.document import Document, FileRef, ReviewStatus

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
        :meth:`soap.db.sqlite.SqliteDatabase.create`), so the original data
        survives a failed re-init and is never overwritten in place. Returns the
        backup path in that case, otherwise ``None``.
        """
        database = sqlite.SqliteDatabase(self.db_path)
        if not self.db_path.exists():
            database.create()
            return None

        if not force:
            return None

        backup_path = self._backup_path()
        shutil.copy2(self.db_path, backup_path)
        # create() builds in a temp file and atomically renames over the
        # existing db, so if it fails the original library (and this backup)
        # stay intact instead of being deleted up front.
        database.create()
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

# --- the `add` pipeline ---------------------------------------------------
#
# `add` is the single manual write path into the library and the importable
# core the CLI (and the v3 inbox agent) call. Metadata comes from an explicit
# DOI/arXiv identifier (via Crossref/arXiv) or manual overrides — the PDF is
# never parsed. Fetching and merging live in `soap.ingest` as pure functions;
# this module orchestrates them, resolves duplicates, optionally opens an editor
# to confirm, and writes disk then DB: folder -> info.yaml -> DB row.

SUPPORTED_SUFFIXES = {".pdf"}


@dataclass
class AddOutcome:
    """The result of adding one source, for reporting and programmatic use."""

    status: str  # "added" | "upgraded" | "skipped" | "error"
    document: Document | None = None
    citekey: str | None = None
    matched_id: str | None = None  # duplicate/upgrade target
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.status != "error"

    @property
    def needs_review(self) -> bool:
        return (
            self.document is not None
            and self.document.review_status == ReviewStatus.NEEDS_REVIEW.value
        )


def collect_files(path: Path, recursive: bool = False) -> list[Path]:
    """Collect supported files under a directory (or the file itself).

    A single file is returned as-is regardless of extension (an explicit add may
    carry manual metadata). Directory walks only pick up supported types.
    """
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    globber = path.rglob("*") if recursive else path.glob("*")
    return sorted(
        p for p in globber if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )


def sha256_file(path: Path) -> str:
    """Compute the SHA-256 of a file, streaming so large PDFs stay cheap."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_filename(name: str) -> str:
    """Strip path separators and control characters from a stored filename."""
    name = os.path.basename(name)
    name = "".join(c for c in name if ord(c) >= 32 and c not in "/\\")
    name = name.strip().strip(".")
    return name or "document"


def _filename_title(name: str) -> str | None:
    """A last-resort title guess from a filename stem."""
    stem = Path(name).stem
    cleaned = re.sub(r"[_\-]+", " ", stem).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or None


def _mime_for(path: Path, given: str | None = None) -> str:
    if given:
        return given
    if path.suffix.lower() == ".pdf":
        return "application/pdf"
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add(
    library: "Library",
    source: str,
    *,
    overrides: Overrides | None = None,
    move: bool = False,
    fetch: bool = True,
    force: bool = False,
    dry_run: bool = False,
    edit: bool = False,
    editor_runner=None,
    docs: DocumentService | None = None,
    client: httpx.Client | None = None,
) -> AddOutcome:
    """File one source into the library.

    ``source`` is a local file path or an arXiv/DOI URL. Metadata comes only
    from ``--doi``/``--arxiv`` (or the URL) via Crossref/arXiv, plus manual
    ``overrides``; the PDF is never parsed. A URL always produces a
    metadata-only document — attach the PDF later with ``add(file, doi=...)``.

    ``move`` moves rather than copies the local original (the inbox agent passes
    ``move=True``). ``fetch`` gates the network, ``force`` bypasses duplicate
    detection, ``dry_run`` writes nothing, and ``edit`` opens the generated
    ``info.yaml`` in an editor for confirmation before writing. ``editor_runner``
    (a ``Callable[[Path], None]``) overrides how the editor is launched, for
    tests.

    A ``DocumentService`` and ``httpx.Client`` may be injected for reuse across a
    batch; otherwise they are created and closed here.
    """
    overrides = overrides or Overrides()
    owns_docs = docs is None
    owns_client = client is None
    docs = docs or DocumentService.open(library.db_path)
    client = client or httpx.Client()
    try:
        return _add_inner(
            library, source, overrides,
            move=move, fetch=fetch, force=force, dry_run=dry_run,
            edit=edit, editor_runner=editor_runner, docs=docs, client=client,
        )
    finally:
        if owns_client:
            client.close()
        if owns_docs:
            docs.close()


def _add_inner(
    library, source, overrides, *,
    move, fetch, force, dry_run, edit, editor_runner, docs, client,
):
    warnings: list[str] = []

    # 1. Resolve the source. A URL yields metadata only; a local path yields a
    #    file. Identifiers come from flags or the URL — never the PDF.
    from_url = url_mod.is_url(source)
    file_path: Path | None = None
    file_mime: str | None = None
    original_name: str | None = None
    identifier: Identifier | None = None
    fetched = None
    fallback_title: str | None = None

    if from_url:
        resolution = url_mod.resolve_url(source, fetch=fetch, client=client)
        warnings.extend(resolution.warnings)
        identifier = resolution.identifier
        fetched = resolution.fetched
        fallback_title = resolution.fallback_title
    else:
        path = Path(source).expanduser()
        if not path.exists():
            return AddOutcome("error", message=f"path does not exist: {source}")
        if not path.is_file():
            return AddOutcome("error", message=f"not a file: {source}")
        try:
            path.stat()
        except OSError as exc:
            return AddOutcome("error", message=f"unreadable: {source} ({exc})")
        file_path = path
        original_name = path.name
        file_mime = _mime_for(path)

    # Explicit flags always win over a URL-derived identifier.
    if overrides.doi:
        identifier = Identifier("doi", overrides.doi)
    elif overrides.arxiv_id:
        identifier = Identifier("arxiv", overrides.arxiv_id)
    elif overrides.isbn:
        identifier = Identifier("isbn", normalize_isbn(overrides.isbn))

    # 2. Content-hash duplicate check (only when a file is present).
    sha = None
    if file_path is not None:
        sha = sha256_file(file_path)
        existing = None if force else docs.find_by_sha256(sha)
        if existing is not None:
            return AddOutcome(
                "skipped", matched_id=existing,
                message=f"duplicate of {existing}", warnings=warnings,
            )

    # 3. Identifier-based duplicate check (before any fetch, so re-adds are
    #    cheap). A metadata-only match that we can complete with a file is an
    #    upgrade, not a duplicate.
    ident_doi = identifier.value if identifier and identifier.kind == "doi" else None
    ident_arxiv = identifier.value if identifier and identifier.kind == "arxiv" else None
    ident_isbn = identifier.value if identifier and identifier.kind == "isbn" else None
    if not force and (ident_doi or ident_arxiv or ident_isbn):
        match = docs.find_by_identifier(ident_doi, ident_arxiv, ident_isbn)
        if match is not None:
            if file_path is not None and not docs.has_file(match):
                return _upgrade_existing(
                    library, docs, match, file_path, file_mime, sha,
                    original_name, move, dry_run, warnings,
                )
            return AddOutcome(
                "skipped", matched_id=match,
                message=f"duplicate of {match}", warnings=warnings,
            )

    # 4. Metadata fetch for the identifier (a URL add fetched already).
    if fetch and identifier is not None and fetched is None:
        fetched = fetch_for_identifier(identifier, client=client)
        if fetched is None:
            warnings.append(
                f"metadata lookup failed for {identifier.kind}:{identifier.value}; "
                "using manual/filename metadata"
            )

    # 5. Merge by precedence (flags > fetched > filename) and validate.
    filename_title = _filename_title(original_name) if original_name else fallback_title
    merged = merge_metadata(overrides, fetched, filename_title)
    if not merged.title:
        return AddOutcome(
            "error",
            message=f"no title for {source}; supply --title",
            warnings=warnings,
        )

    doc_url = merged.url or (source if from_url else None)
    # A document with no file is valid but always needs a human to attach one.
    review = merged.review_status if file_path is not None else ReviewStatus.NEEDS_REVIEW

    def taken(key: str) -> bool:
        return docs.id_exists(key) or (library.documents / key).exists()

    citekey = unique_citekey(
        generate_citekey(merged.authors, merged.year, merged.title), taken
    )
    stored_name = sanitize_filename(original_name) if original_name else None

    files: list[FileRef] = []
    if file_path is not None and stored_name is not None:
        rel = Path("documents") / citekey / stored_name
        files.append(FileRef(path=rel.as_posix(), mime=file_mime, sha256=sha))

    document = Document(
        id=citekey,
        type=merged.type,
        title=merged.title,
        year=merged.year,
        authors=merged.authors,
        venue=merged.venue,
        publisher=merged.publisher,
        # The identifier is recorded even without a fetch (offline/--no-fetch),
        # so it round-trips and drives future identifier dedup.
        doi=merged.doi or ident_doi,
        arxiv_id=merged.arxiv_id or ident_arxiv,
        isbn=merged.isbn or ident_isbn,
        url=doc_url,
        abstract=merged.abstract,
        language=merged.language,
        added_at=_now_iso(),
        source=merged.source,
        confidence=merged.confidence,
        review_status=review,
        tags=overrides.tags,
        collections=overrides.collections,
        files=files,
    )

    # 6. Optional confirmation step: let the user fix fields in an editor. soap
    #    re-derives the citekey and file paths from the edited metadata.
    if edit and not dry_run:
        try:
            document = _open_in_editor(document, editor_runner or _default_editor_runner)
        except (yaml.YAMLError, ValueError) as exc:
            return AddOutcome(
                "error", message=f"edit produced invalid metadata: {exc}",
                warnings=warnings,
            )
        citekey = unique_citekey(
            generate_citekey(document.authors, document.year, document.title), taken
        )
        document.id = citekey
        if file_path is not None and stored_name is not None:
            rel = Path("documents") / citekey / stored_name
            document.files = [FileRef(path=rel.as_posix(), mime=file_mime, sha256=sha)]
        else:
            document.files = []

    if dry_run:
        return AddOutcome(
            "added", document=document, citekey=citekey,
            warnings=warnings, dry_run=True,
        )

    # 7. Write to disk: folder -> file -> info.yaml. Roll back the folder on any
    #    failure so a half-written document never lingers.
    folder = library.documents / citekey
    try:
        folder.mkdir(parents=True, exist_ok=False)
        if file_path is not None and stored_name is not None:
            dest = folder / stored_name
            if move:
                shutil.move(str(file_path), dest)
            else:
                shutil.copy2(file_path, dest)
        _write_info_yaml(folder / "info.yaml", document)
    except OSError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        return AddOutcome(
            "error", message=f"failed to write {citekey}: {exc}", warnings=warnings,
        )

    # 8. Index in SQLite. Disk is the source of truth, so a DB failure here is a
    #    warning, not a rollback: a future reindex recovers the row.
    try:
        docs.index(document)
    except Exception as exc:  # noqa: BLE001 - report any DB failure, keep going
        warnings.append(
            f"{citekey} written to disk but not indexed ({exc}); "
            "run a reindex to recover it"
        )

    return AddOutcome(
        "added", document=document, citekey=citekey, warnings=warnings,
    )


def _upgrade_existing(
    library, docs, doc_id, file_path, file_mime, sha,
    original_name, move, dry_run, warnings,
):
    """Attach a newly found file to an existing metadata-only document."""
    if dry_run:
        return AddOutcome(
            "upgraded", matched_id=doc_id,
            message=f"would attach file to {doc_id}",
            warnings=warnings, dry_run=True,
        )
    folder = library.documents / doc_id
    stored_name = sanitize_filename(original_name) if original_name else "document.pdf"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / stored_name
        if move:
            shutil.move(str(file_path), dest)
        else:
            shutil.copy2(file_path, dest)
    except OSError as exc:
        return AddOutcome(
            "error", message=f"failed to attach file to {doc_id}: {exc}",
            warnings=warnings,
        )
    rel = Path("documents") / doc_id / stored_name
    file_ref = FileRef(path=rel.as_posix(), mime=file_mime, sha256=sha)
    try:
        docs.attach_file(doc_id, file_ref)
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"file attached to {doc_id} on disk but not indexed ({exc})"
        )
    return AddOutcome(
        "upgraded", matched_id=doc_id,
        message=f"attached file to existing {doc_id}", warnings=warnings,
    )


def _write_info_yaml(path: Path, document: Document) -> None:
    """Serialize the validated model to ``info.yaml`` in Appendix-A order."""
    path.write_text(_dump_yaml(document))


def _dump_yaml(document: Document) -> str:
    # mode="json" coerces enums (incl. defaults untouched by use_enum_values)
    # and any nested models to plain YAML-safe scalars.
    data = document.model_dump(mode="json")
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _default_editor_runner(path: Path) -> None:
    """Open ``path`` in the user's editor and block until it exits."""
    import shlex
    import subprocess

    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    subprocess.call([*shlex.split(editor), str(path)])


def _open_in_editor(document: Document, run_editor) -> Document:
    """Round-trip a document through an editor; return the re-validated model.

    Raises ``yaml.YAMLError`` or ``ValueError`` (pydantic ``ValidationError``
    subclasses it) if the user saves something that no longer validates.
    """
    import tempfile

    fd, name = tempfile.mkstemp(suffix=".yaml", prefix="soap-edit-")
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.write_text(_dump_yaml(document))
        run_editor(tmp)
        data = yaml.safe_load(tmp.read_text())
        return Document(**data)
    finally:
        tmp.unlink(missing_ok=True)


# --- browse / review read-write helpers -----------------------------------
#
# The TUI reads through DocumentService but writes through these functions so
# the "info.yaml on disk is authoritative, SQLite is a rebuildable index"
# invariant holds: every mutation rewrites the yaml first, then re-syncs the DB.


def info_yaml_path(library: "Library", doc_id: str) -> Path:
    return library.documents / doc_id / "info.yaml"


def load_document(library: "Library", doc_id: str) -> Document:
    """Read and validate a document's ``info.yaml`` from disk."""
    data = yaml.safe_load(info_yaml_path(library, doc_id).read_text())
    return Document(**data)


def save_document(
    library: "Library", document: Document, docs: DocumentService
) -> None:
    """Persist an edited document: rewrite ``info.yaml`` then re-index it.

    The id (and folder) never change here — only metadata. The DB rows are
    rebuilt from scratch (``remove`` + ``index``) so author/tag/collection
    changes are reflected without stale links.
    """
    _write_info_yaml(info_yaml_path(library, document.id), document)
    docs.remove(document.id)
    docs.index(document)


def set_review_status(
    library: "Library",
    doc_id: str,
    status: str,
    docs: DocumentService,
) -> Document:
    """Flip a document's review flag on disk (source of truth) then in the index."""
    document = load_document(library, doc_id)
    document.review_status = status
    _write_info_yaml(info_yaml_path(library, doc_id), document)
    docs.set_review_status(doc_id, status)
    return document


def edit_document(
    library: "Library",
    doc_id: str,
    docs: DocumentService,
    editor_runner=None,
) -> Document:
    """Open a document's ``info.yaml`` in ``$EDITOR``, then re-validate and re-index.

    Keeps the id fixed (no folder rename on edit). Raises ``yaml.YAMLError`` or
    ``ValueError`` if the edited file no longer validates; the on-disk file is
    left as the user saved it so nothing is lost.
    """
    run_editor = editor_runner or _default_editor_runner
    path = info_yaml_path(library, doc_id)
    run_editor(path)
    data = yaml.safe_load(path.read_text())
    document = Document(**data)
    document.id = doc_id  # never let an edit repoint the folder
    save_document(library, document, docs)
    return document
