"""Disk-first persistence and library-boundary regressions."""

import errno

import pytest

import soap.library as library_mod
from soap.db.documents import DocumentService
from soap.ingest.merge import Overrides
from soap.library import (
    add,
    delete_document,
    info_yaml_path,
    load_document,
    save_document,
)
from soap.models.document import Document, FileRef


def _seed(library, make_pdf):
    outcome = add(library, str(make_pdf("seed.pdf")), fetch=False)
    assert outcome.status == "added"
    return outcome.citekey


@pytest.mark.parametrize(
    "bad_id",
    ["", ".", "..", "../outside", "/tmp/outside", "nested/id", r"..\outside", "C:outside"],
)
def test_invalid_document_ids_are_rejected_before_boundary_operations(library, bad_id):
    with pytest.raises(ValueError):
        info_yaml_path(library, bad_id)


def test_document_directory_symlink_escape_is_rejected(library, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (library.documents / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        info_yaml_path(library, "escape")
    with DocumentService.open(library.db_path) as docs:
        with pytest.raises(ValueError):
            delete_document(library, "escape", docs)
    assert outside.exists()


def test_info_yaml_symlink_escape_is_rejected_before_open_or_save(library, make_pdf, tmp_path):
    doc_id = _seed(library, make_pdf)
    outside = tmp_path / "outside.yaml"
    outside.write_text("title: outside\n")
    info = info_yaml_path(library, doc_id)
    info.unlink()
    info.symlink_to(outside)

    with pytest.raises(ValueError):
        load_document(library, doc_id)
    doc = Document(id=doc_id, title="replacement")
    with DocumentService.open(library.db_path) as docs:
        with pytest.raises(ValueError):
            save_document(library, doc, docs)
    assert outside.read_text() == "title: outside\n"


@pytest.mark.parametrize(
    "unsafe_path",
    ["../outside", "/tmp/outside", "documents/../outside", r"..\outside"],
)
def test_file_references_reject_traversal_and_absolute_paths(
    library, make_pdf, unsafe_path
):
    doc_id = _seed(library, make_pdf)
    doc = load_document(library, doc_id)
    original = doc.model_copy(deep=True)
    doc.files = [FileRef(path=unsafe_path)]

    with DocumentService.open(library.db_path) as docs:
        with pytest.raises(ValueError):
            save_document(library, doc, docs)
    assert load_document(library, doc_id).model_dump() == original.model_dump()


def test_file_reference_symlink_escape_is_rejected_before_save(library, make_pdf, tmp_path):
    doc_id = _seed(library, make_pdf)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    (library.path / "redirect.pdf").symlink_to(outside)
    doc = load_document(library, doc_id)
    doc.files = [FileRef(path="redirect.pdf")]

    with DocumentService.open(library.db_path) as docs:
        with pytest.raises(ValueError):
            save_document(library, doc, docs)
    assert outside.read_bytes() == b"outside"
    assert load_document(library, doc_id).files


def test_relative_file_reference_inside_library_remains_supported(library, make_pdf):
    doc_id = _seed(library, make_pdf)
    local = library.path / "paper.pdf"
    local.write_bytes(b"paper")
    doc = load_document(library, doc_id)
    doc.files = [FileRef(path="paper.pdf")]

    with DocumentService.open(library.db_path) as docs:
        save_document(library, doc, docs)
    assert load_document(library, doc_id).files[0].path == "paper.pdf"


def test_atomic_metadata_write_failure_preserves_disk_and_index(
    library, make_pdf, monkeypatch
):
    doc_id = _seed(library, make_pdf)
    info = info_yaml_path(library, doc_id)
    before = info.read_text()
    doc = load_document(library, doc_id)
    doc.title = "new title"

    def fail_replace(*_args, **_kwargs):
        raise OSError("injected replace failure")

    monkeypatch.setattr(library_mod.os, "replace", fail_replace)
    with DocumentService.open(library.db_path) as docs:
        with pytest.raises(OSError, match="injected replace failure"):
            save_document(library, doc, docs)
        assert docs.get_document(doc_id).title != "new title"

    assert info.read_text() == before
    assert not list(info.parent.glob(f".{info.name}.*.tmp"))


def test_index_failure_leaves_new_yaml_authoritative(library, make_pdf, monkeypatch):
    doc_id = _seed(library, make_pdf)
    doc = load_document(library, doc_id)
    doc.title = "disk wins"

    with DocumentService.open(library.db_path) as docs:
        monkeypatch.setattr(docs, "index", lambda _doc: (_ for _ in ()).throw(RuntimeError("index down")))
        with pytest.raises(RuntimeError, match="index down"):
            save_document(library, doc, docs)

    assert load_document(library, doc_id).title == "disk wins"
    with DocumentService.open(library.db_path) as docs:
        assert docs.get_document(doc_id) is None


def test_upgrade_index_failure_reports_and_keeps_disk_state(library, make_pdf, monkeypatch):
    doi = "10.5555/upgrade"
    metadata = add(
        library,
        "https://doi.org/10.5555/upgrade",
        overrides=Overrides(title="Upgrade", doi=doi),
        fetch=False,
    )
    pdf = make_pdf("upgrade.pdf")

    with DocumentService.open(library.db_path) as docs:
        monkeypatch.setattr(docs, "index", lambda _doc: (_ for _ in ()).throw(RuntimeError("index down")))
        outcome = add(
            library, str(pdf), overrides=Overrides(doi=doi), fetch=False, docs=docs
        )

    assert outcome.status == "upgraded"
    assert any("not indexed" in warning for warning in outcome.warnings)
    on_disk = load_document(library, metadata.citekey)
    assert on_disk.files[0].path.endswith("/upgrade.pdf")
    assert on_disk.review_status == "filed"
    with DocumentService.open(library.db_path) as docs:
        assert docs.get_document(metadata.citekey) is None


def _fsync_raising_on_directory(errnum):
    import os
    import stat

    real_fsync = library_mod.os.fsync

    def fake_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errnum, "injected directory fsync failure")
        return real_fsync(fd)

    return fake_fsync


def test_unsupported_directory_fsync_is_nonfatal(library, make_pdf, monkeypatch):
    doc_id = _seed(library, make_pdf)
    doc = load_document(library, doc_id)
    doc.title = "persisted despite fsync"

    monkeypatch.setattr(
        library_mod.os, "fsync", _fsync_raising_on_directory(errno.EINVAL)
    )
    with DocumentService.open(library.db_path) as docs:
        save_document(library, doc, docs)

    assert load_document(library, doc_id).title == "persisted despite fsync"
    info = info_yaml_path(library, doc_id)
    assert not list(info.parent.glob(f".{info.name}.*.tmp"))


def test_other_directory_fsync_error_remains_visible(library, make_pdf, monkeypatch):
    doc_id = _seed(library, make_pdf)
    doc = load_document(library, doc_id)
    doc.title = "should surface"

    monkeypatch.setattr(
        library_mod.os, "fsync", _fsync_raising_on_directory(errno.EIO)
    )
    with DocumentService.open(library.db_path) as docs:
        with pytest.raises(OSError, match="injected directory fsync failure"):
            save_document(library, doc, docs)

    info = info_yaml_path(library, doc_id)
    assert not list(info.parent.glob(f".{info.name}.*.tmp"))


def test_delete_filesystem_failure_is_visible_and_keeps_index(
    library, make_pdf, monkeypatch
):
    doc_id = _seed(library, make_pdf)

    def fail_delete(*_args, **_kwargs):
        raise OSError("injected delete failure")

    monkeypatch.setattr(library_mod.shutil, "rmtree", fail_delete)
    with DocumentService.open(library.db_path) as docs:
        with pytest.raises(OSError, match="injected delete failure"):
            delete_document(library, doc_id, docs)

    assert (library.documents / doc_id).exists()
    with DocumentService.open(library.db_path) as docs:
        assert docs.get_document(doc_id) is not None


def test_delete_index_failure_is_visible_after_disk_first_delete(
    library, make_pdf, monkeypatch
):
    doc_id = _seed(library, make_pdf)

    with DocumentService.open(library.db_path) as docs:
        monkeypatch.setattr(docs, "remove", lambda _doc_id: (_ for _ in ()).throw(RuntimeError("index down")))
        with pytest.raises(RuntimeError, match="index down"):
            delete_document(library, doc_id, docs)

    assert not (library.documents / doc_id).exists()
    with DocumentService.open(library.db_path) as docs:
        assert docs.get_document(doc_id) is not None
