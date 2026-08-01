"""Regenerate the README demo GIFs from a seeded, throwaway soap library.

Run:

    uv run python scripts/demo.py

This is the canonical regenerator for ``docs/demo.gif`` (the TUI overview) and
``docs/add.gif`` (the ``soap add`` flow). It:

  1. builds a throwaway library under a temp ``HOME`` / ``SOAP_DIR`` and seeds it
     with *invented* documents (the same fake-library idea as
     ``scripts/shoot_tui.py``) plus a placeholder ``sample.pdf`` for the add flow,
  2. drives the real ``soap`` binary with VHS (charmbracelet/vhs) to record the
     two GIFs from ``scripts/demo.tape`` and ``scripts/demo-add.tape``,
  3. removes the temp dir afterward.

Nothing from your real library, home directory, or the network is touched — the
add flow runs ``--no-fetch`` and the update nudge is pre-cached offline. Re-runs
are deterministic (fixed ids, titles, and ``added_at``).

Prerequisites: ``vhs``, ``ttyd``, and ``ffmpeg`` on ``PATH`` (e.g.
``brew install vhs``, which pulls in ttyd + ffmpeg). See docs/releasing.md.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from soap.cli.init import _write_starter_config
from soap.cli.selfupdate import NUDGE_CACHE_FILENAME, _write_cache_ts
from soap.db.documents import DocumentService
from soap.library import Library, save_document
from soap.models.document import Document, FileRef

REPO = Path(__file__).resolve().parents[1]
TAPES = [REPO / "scripts" / "demo.tape", REPO / "scripts" / "demo-add.tape"]

# A minimal but valid PDF so attachments are faithful (starts with the %PDF magic
# marker soap's ingest checks for). Content is irrelevant — nothing is parsed.
MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF\n"
)

# Invented library contents — famous public papers/books, NONE from a real user
# library. Fields mirror scripts/shoot_tui.py's seeding scheme.
# (id, title, authors, venue, year, type, arxiv, tags, read, review, conf, abstract)
DOCS = [
    (
        "vaswani2017attention",
        "Attention Is All You Need",
        ["Vaswani, Ashish", "Shazeer, Noam", "Parmar, Niki", "Uszkoreit, Jakob"],
        "NeurIPS",
        2017,
        "inproceedings",
        "1706.03762",
        ["transformers", "nlp"],
        "read",
        "filed",
        0.98,
        "The dominant sequence transduction models are based on complex recurrent or "
        "convolutional neural networks. We propose the Transformer, based solely on "
        "attention mechanisms, dispensing with recurrence and convolutions entirely.",
    ),
    (
        "dosovitskiy2021image",
        "An Image Is Worth 16x16 Words: Transformers for Image Recognition",
        ["Dosovitskiy, Alexey", "Beyer, Lucas", "Kolesnikov, Alexander"],
        "ICLR",
        2021,
        "inproceedings",
        "2010.11929",
        ["transformers", "vision"],
        "unread",
        "needs_review",
        0.64,
        "While the Transformer architecture has become the de-facto standard for NLP "
        "tasks, its applications to computer vision remain limited. We show a pure "
        "transformer applied to sequences of image patches performs very well.",
    ),
    (
        "devlin2019bert",
        "BERT: Pre-training of Deep Bidirectional Transformers",
        ["Devlin, Jacob", "Chang, Ming-Wei", "Lee, Kenton", "Toutanova, Kristina"],
        "NAACL",
        2019,
        "inproceedings",
        "1810.04805",
        ["transformers", "nlp"],
        "reading",
        "filed",
        0.95,
        None,
    ),
    (
        "he2016deep",
        "Deep Residual Learning for Image Recognition",
        ["He, Kaiming", "Zhang, Xiangyu", "Ren, Shaoqing", "Sun, Jian"],
        "CVPR",
        2016,
        "inproceedings",
        "1512.03385",
        ["vision"],
        "read",
        "filed",
        0.97,
        None,
    ),
    (
        "brown2020language",
        "Language Models Are Few-Shot Learners",
        ["Brown, Tom", "Mann, Benjamin", "Ryder, Nick"],
        "NeurIPS",
        2020,
        "inproceedings",
        "2005.14165",
        ["nlp"],
        "unread",
        "needs_review",
        0.71,
        None,
    ),
    (
        "kingma2015adam",
        "Adam: A Method for Stochastic Optimization",
        ["Kingma, Diederik", "Ba, Jimmy"],
        "ICLR",
        2015,
        "inproceedings",
        "1412.6980",
        ["optimization"],
        "read",
        "filed",
        0.96,
        None,
    ),
    (
        "sutton2018reinforcement",
        "Reinforcement Learning: An Introduction",
        ["Sutton, Richard", "Barto, Andrew"],
        "MIT Press",
        2018,
        "book",
        None,
        ["rl"],
        "reading",
        "filed",
        0.9,
        None,
    ),
]


def seed(library: Library) -> None:
    """Write the invented documents to disk (info.yaml + placeholder PDF) and index."""
    with DocumentService.open(library.db_path) as docs:
        for (
            did,
            title,
            authors,
            venue,
            year,
            typ,
            arxiv,
            tags,
            read,
            review,
            conf,
            abstract,
        ) in DOCS:
            folder = library.documents / did
            folder.mkdir(parents=True, exist_ok=True)
            fname = f"{did}.pdf"
            (folder / fname).write_bytes(MINIMAL_PDF)
            doc = Document(
                id=did,
                type=typ,
                title=title,
                authors=authors,
                venue=venue,
                year=year,
                arxiv_id=arxiv,
                tags=tags,
                abstract=abstract,
                read_status=read,
                review_status=review,
                confidence=conf,
                source="arxiv" if arxiv else "local",
                added_at="2026-07-20",
                files=[FileRef(path=f"documents/{did}/{fname}")],
            )
            # Disk-first via the library helper (never the DB directly).
            save_document(library, doc, docs)


def require_tools() -> None:
    missing = [t for t in ("vhs", "ttyd", "ffmpeg") if shutil.which(t) is None]
    if missing:
        sys.exit(
            f"missing required tool(s): {', '.join(missing)}.\n"
            "Install VHS and its deps, e.g. `brew install vhs` (pulls in ttyd + ffmpeg).\n"
            "See docs/releasing.md § Regenerating the demo GIFs."
        )


def main() -> None:
    require_tools()
    tmp = Path(tempfile.mkdtemp(prefix="soap-demo-"))
    try:
        home = tmp
        soap_dir = home / ".soap"
        library = Library(soap_dir)
        library.create_directories()
        library.initialize_database()
        # Match a real `soap init` (ships always_review: true) so the add flow
        # exercises the review queue the same way a first-run user would.
        _write_starter_config(soap_dir)
        seed(library)

        # The add flow adds this file; it lives in HOME so the tape can `cd ~`
        # and type a clean, relative `soap add sample.pdf`.
        (home / "sample.pdf").write_bytes(MINIMAL_PDF)

        # Pre-cache the update nudge so `soap add` makes no network call.
        _write_cache_ts(soap_dir / NUDGE_CACHE_FILENAME, time.time())

        # Environment for VHS: the seeded library, and the venv's `soap` on PATH.
        # (This driver runs under `uv run`, so sys.executable is the project venv
        # and its bin dir holds the `soap` console script.)
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["SOAP_DIR"] = str(soap_dir)
        venv_bin = Path(sys.executable).parent
        env["PATH"] = f"{venv_bin}{os.pathsep}{env['PATH']}"
        # Keep the terminal clean/deterministic inside the recording.
        env["TERM"] = "xterm-256color"
        env["PS1"] = "$ "

        # Warm the import path so the first TUI launch inside the tape is snappy.
        subprocess.run(
            ["soap", "--version"],
            env=env,
            cwd=home,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        for tape in TAPES:
            print(f"→ rendering {tape.relative_to(REPO)} …", flush=True)
            subprocess.run(["vhs", str(tape)], env=env, cwd=REPO, check=True)
        print("done. Wrote docs/demo.gif and docs/add.gif.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
