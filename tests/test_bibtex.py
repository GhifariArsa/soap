"""Focused tests for the deterministic BibTeX serializer (``soap/bibtex.py``).

Covers author joining, special-character escaping, meaning-based optional-field
mapping, deterministic ordering, and records with incomplete metadata. Pure model
in → text out; no library, DB, or network involved.
"""

from soap.bibtex import document_to_entry, serialize_documents
from soap.models.document import Document


def _doc(**kw) -> Document:
    base: dict = dict(id="key2020word", title="A Title", type="article")
    base.update(kw)
    return Document(**base)


def _entry(doc: Document) -> str:
    """document_to_entry with a non-None assertion, for terse test assertions."""
    entry = document_to_entry(doc)
    assert entry is not None
    return entry


def test_entry_uses_citekey_and_maps_type():
    entry = _entry(_doc(id="smith2020deep", type="article"))
    assert entry.startswith("@article{smith2020deep,")
    assert entry.rstrip().endswith("}")


def test_unknown_type_degrades_to_misc():
    entry = _entry(_doc(type="blogpost"))
    assert entry.startswith("@misc{")


def test_authors_joined_with_and():
    entry = _entry(
        _doc(authors=["Smith, Jane", "Doe, John", "Roe, Rex"])
    )
    assert "author = {Smith, Jane and Doe, John and Roe, Rex}" in entry


def test_special_characters_are_escaped():
    entry = _entry(
        _doc(title="Cost & Effect: 50% of $5 #1 a_b {x} ~ ^", id="k")
    )
    assert r"\&" in entry
    assert r"\%" in entry
    assert r"\$" in entry
    assert r"\#" in entry
    assert r"\_" in entry
    assert r"\{" in entry and r"\}" in entry
    assert r"\textasciitilde{}" in entry
    assert r"\textasciicircum{}" in entry
    # A literal backslash becomes a textbackslash command, not a raw escape.
    entry2 = _entry(_doc(title=r"a\b", id="k2"))
    assert r"\textbackslash{}" in entry2


def test_venue_maps_to_journal_for_article():
    entry = _entry(_doc(type="article", venue="Nature"))
    assert "journal = {Nature}" in entry
    assert "booktitle" not in entry


def test_venue_maps_to_booktitle_for_proceedings():
    entry = _entry(_doc(type="inproceedings", venue="NeurIPS"))
    assert "booktitle = {NeurIPS}" in entry
    assert "journal" not in entry


def test_optional_fields_included_only_when_present():
    entry = _entry(
        _doc(
            year=2021,
            doi="10.1/x",
            isbn="978-3",
            url="https://e.org",
            publisher="ACME",
            arxiv_id="2101.00001",
            language="en",
        )
    )
    assert "year = {2021}" in entry
    assert "doi = {10.1/x}" in entry
    assert "isbn = {978-3}" in entry
    assert "url = {https://e.org}" in entry
    assert "publisher = {ACME}" in entry
    assert "eprint = {2101.00001}" in entry
    assert "archivePrefix = {arXiv}" in entry
    assert "language = {en}" in entry
    # A sparse document omits the absent fields entirely.
    sparse = _entry(_doc(id="k", title="Only Title"))
    assert "doi" not in sparse
    assert "year" not in sparse
    assert "journal" not in sparse


def test_deterministic_ordering_by_citekey():
    a = _doc(id="alpha2020", title="Alpha")
    b = _doc(id="beta2019", title="Beta")
    c = _doc(id="gamma2021", title="Gamma")
    r1 = serialize_documents([c, a, b])
    r2 = serialize_documents([b, c, a])
    assert r1.text == r2.text
    assert r1.exported_ids == ["alpha2020", "beta2019", "gamma2021"]
    # And entries appear in that order in the text.
    assert r1.text.index("alpha2020") < r1.text.index("beta2019") < r1.text.index(
        "gamma2021"
    )


def test_incomplete_metadata_is_reported_not_dropped_silently():
    # A document with a title always yields an entry (title is a field).
    good = _doc(id="good2020", title="Has Title")
    # An id-less record cannot produce a valid entry.
    no_id = _doc(id="", title="No Key")
    result = serialize_documents([good, no_id])
    assert result.exported_ids == ["good2020"]
    assert result.skipped_ids == [""]
    assert document_to_entry(no_id) is None


def test_empty_input_yields_empty_text():
    result = serialize_documents([])
    assert result.text == ""
    assert result.count == 0


def test_output_ends_with_newline_and_separates_entries():
    result = serialize_documents(
        [_doc(id="a2020", title="A"), _doc(id="b2020", title="B")]
    )
    assert result.text.endswith("\n")
    # Entries separated by a blank line.
    assert "\n\n@" in result.text
