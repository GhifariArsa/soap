"""Capture 'after' screenshots of the real integrated soap TUI.

Builds a throwaway library seeded with realistic documents, then drives the
actual ``SoapApp`` / ``ReviewScreen`` / ``HelpScreen`` via Textual's test pilot
and writes SVGs into ``docs/screens/``. Run: ``uv run python scripts/shoot_tui.py``.
"""

import asyncio
import tempfile
from pathlib import Path

from soap.db.documents import DocumentService
from soap.library import Library
from soap.models.document import Document, FileRef
from soap.tui.app import SoapApp
from soap.tui.review import ReviewScreen

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "screens"

# (id, title, authors, venue, year, type, arxiv, tags, read, review, conf, abstract, file)
DOCS = [
    ("dosovitskiy2021image", "An Image is Worth 16x16 Words: Transformers for Image Recognition",
     ["Dosovitskiy, Alexey", "Beyer, Lucas", "Kolesnikov, Alexander"], "ICLR", 2021,
     "inproceedings", "2010.11929", ["transformers", "vision"], "unread", "needs_review", 0.64,
     "While the Transformer architecture has become the de-facto standard for NLP tasks, its "
     "applications to computer vision remain limited. We show that a pure transformer applied "
     "directly to sequences of image patches can perform very well on image classification.",
     "vit.pdf"),
    ("vaswani2017attention", "Attention Is All You Need",
     ["Vaswani, Ashish", "Shazeer, Noam"] + [f"X{i}, Y" for i in range(6)], "NeurIPS", 2017,
     "inproceedings", "1706.03762", ["nlp", "transformers"], "read", "filed", 0.98, None, "attn.pdf"),
    ("devlin2019bert", "BERT: Pre-training of Deep Bidirectional Transformers",
     ["Devlin, Jacob", "Chang, Ming-Wei", "Lee, Kenton", "Toutanova, Kristina"], "NAACL", 2019,
     "inproceedings", "1810.04805", ["nlp"], "reading", "filed", 0.95, None, "bert.pdf"),
    ("he2016deep", "Deep Residual Learning for Image Recognition",
     ["He, Kaiming", "Zhang, Xiangyu", "Ren, Shaoqing", "Sun, Jian"], "CVPR", 2016,
     "inproceedings", "1512.03385", ["vision"], "read", "filed", 0.97, None, "resnet.pdf"),
    ("goodfellow2014gan", "Generative Adversarial Networks",
     ["Goodfellow, Ian"] + [f"A{i}, B" for i in range(5)], "NeurIPS", 2014,
     "inproceedings", "1406.2661", ["vision"], "read", "filed", 0.99, None, "gan.pdf"),
    ("brown2020language", "Language Models are Few-Shot Learners",
     ["Brown, Tom"] + [f"C{i}, D" for i in range(30)], "NeurIPS", 2020,
     "inproceedings", "2005.14165", ["nlp"], "unread", "needs_review", 0.71, None, "gpt3.pdf"),
    ("kingma2015adam", "Adam: A Method for Stochastic Optimization",
     ["Kingma, Diederik", "Ba, Jimmy"], "ICLR", 2015, "inproceedings", "1412.6980",
     ["nlp"], "read", "filed", 0.96, None, "adam.pdf"),
    ("sutton2018reinforcement", "Reinforcement Learning: An Introduction",
     ["Sutton, Richard", "Barto, Andrew"], "MIT Press", 2018, "book", None,
     ["rl"], "reading", "filed", 0.9, None, "rl.pdf"),
    ("untitled-scan-0412", "scan_0412.pdf", [], None, None, "article", None,
     [], "unread", "needs_review", 0.12, None, "scan_0412.pdf"),
]


def seed(library: Library) -> None:
    with DocumentService.open(library.db_path) as docs:
        for (did, title, authors, venue, year, typ, arxiv, tags, read, review,
             conf, abstract, fname) in DOCS:
            doc = Document(
                id=did, type=typ, title=title, authors=authors, venue=venue,
                year=year, arxiv_id=arxiv, tags=tags, abstract=abstract,
                read_status=read, review_status=review, confidence=conf,
                source="arxiv" if arxiv else "local", added_at="2026-07-20",
                files=[FileRef(path=f"documents/{did}/{fname}")],
            )
            docs.index(doc)


async def shoot() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        library = Library(Path(tmp) / "lib")
        library.create_directories()
        library.initialize_database()
        seed(library)

        # Library view
        app = SoapApp(library)
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause()
            app.save_screenshot(str(OUT / "after-01-library.svg"))
            print("wrote after-01-library.svg")

        # Review — command mode (medium confidence, doc 1)
        app2 = SoapApp(library)
        async with app2.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app2.docs = DocumentService.open(library.db_path)
            ids = [r.id for r in app2.docs.list_documents(filter_kind="inbox")]
            screen = ReviewScreen(app2.library, app2.docs, ids)
            await app2.push_screen(screen)
            await pilot.pause()
            app2.set_focus(None)
            await pilot.pause()
            app2.save_screenshot(str(OUT / "after-02-review-command.svg"))
            print("wrote after-02-review-command.svg")
            # Low-confidence item: walk to the scan (last in the queue)
            screen.pos = len(ids) - 1
            screen._render_current()
            app2.set_focus(None)
            await pilot.pause()
            app2.save_screenshot(str(OUT / "after-07-review-lowconf.svg"))
            print("wrote after-07-review-lowconf.svg")
            app2.docs.close()

        # Help
        app3 = SoapApp(library)
        async with app3.run_test(size=(120, 24)) as pilot:
            await pilot.pause()
            app3.action_help()
            await pilot.pause()
            app3.save_screenshot(str(OUT / "after-04-help.svg"))
            print("wrote after-04-help.svg")


if __name__ == "__main__":
    asyncio.run(shoot())
