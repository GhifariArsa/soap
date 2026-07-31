"""Download a PDF for a link add, so a URL/arXiv add attaches a real file.

Bounded and honest. We keep a file only when it is actually a PDF (an
``application/pdf`` content-type and/or the ``%PDF`` magic bytes), cap the
download size, follow redirects, and use a timeout. Network trouble never
raises: a failure returns a :class:`DownloadResult` carrying an ``error`` string
and the caller falls back to metadata-only. No HTML is scraped and no paywall is
bypassed — a landing page that serves HTML simply fails the PDF check.
"""

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from soap.ingest.fetch import USER_AGENT

# A download is a bigger transfer than a metadata lookup; give it more headroom.
DOWNLOAD_TIMEOUT = 30.0
# Cap a single download so a mislabeled or hostile URL can't fill the disk.
MAX_PDF_BYTES = 100 * 1024 * 1024  # 100 MB
_PDF_MAGIC = b"%PDF"
_PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}


@dataclass
class DownloadResult:
    """Outcome of a PDF download attempt.

    Exactly one of (``path``, ``error``) is meaningful: ``path`` is set on
    success (a temp file the caller must move/copy into the library then remove),
    ``error`` is a short human-readable reason on failure.
    """

    path: Path | None = None
    filename: str | None = None
    mime: str | None = None
    error: str | None = None


def arxiv_pdf_url(arxiv_id: str) -> str:
    """Canonical PDF URL for an arXiv id, preserving any version suffix."""
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"


def is_pdf_url(url: str) -> bool:
    """True if the URL path (ignoring query/fragment) ends in ``.pdf``."""
    path = re.split(r"[?#]", url, maxsplit=1)[0]
    return path.lower().endswith(".pdf")


def _filename_for(url: str) -> str:
    """A stored filename derived from the URL's last path segment."""
    path = re.split(r"[?#]", url, maxsplit=1)[0]
    seg = path.rstrip("/").rsplit("/", 1)[-1]
    if not seg:
        return "document.pdf"
    if not seg.lower().endswith(".pdf"):
        seg = f"{seg}.pdf"
    return seg


def download_pdf(
    url: str,
    *,
    client: httpx.Client,
    max_bytes: int = MAX_PDF_BYTES,
    timeout: float = DOWNLOAD_TIMEOUT,
) -> DownloadResult:
    """Stream ``url`` to a temp file, keeping it only if it is a real PDF.

    Returns a :class:`DownloadResult` — never raises. On any failure (non-200,
    wrong content, oversize, network error) the temp file is removed and the
    result carries an ``error``.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*"}
    tmp: Path | None = None
    ok = False
    try:
        with client.stream(
            "GET", url, headers=headers, timeout=timeout, follow_redirects=True
        ) as resp:
            if resp.status_code != 200:
                return DownloadResult(error=f"HTTP {resp.status_code} from {url}")

            content_type = (
                resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            )
            looks_pdf_ct = content_type in _PDF_CONTENT_TYPES

            fd, name = tempfile.mkstemp(suffix=".pdf", prefix="soap-dl-")
            tmp = Path(name)
            written = 0
            head = b""
            with os.fdopen(fd, "wb") as fh:
                for chunk in resp.iter_bytes():
                    if not chunk:
                        continue
                    if not head:
                        head = chunk[:4]
                        # Reject early, before pulling a big non-PDF body, unless
                        # the server explicitly labels it a PDF.
                        if not looks_pdf_ct and not head.startswith(_PDF_MAGIC):
                            return DownloadResult(
                                error=(
                                    "response is not a PDF "
                                    f"(content-type {content_type or 'unknown'})"
                                )
                            )
                    written += len(chunk)
                    if written > max_bytes:
                        return DownloadResult(
                            error=f"PDF exceeds max size ({max_bytes} bytes)"
                        )
                    fh.write(chunk)

            if written == 0:
                return DownloadResult(error=f"empty response from {url}")
            if not looks_pdf_ct and not head.startswith(_PDF_MAGIC):
                return DownloadResult(error="response is not a PDF")

            ok = True
            return DownloadResult(
                path=tmp,
                filename=_filename_for(url),
                mime="application/pdf",
            )
    except (httpx.HTTPError, OSError) as exc:
        return DownloadResult(error=f"download failed: {exc}")
    finally:
        if tmp is not None and not ok:
            tmp.unlink(missing_ok=True)
