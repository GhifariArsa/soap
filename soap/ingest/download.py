"""Download a PDF for a link add, so a URL/arXiv add attaches a real file.

Bounded and honest.  Redirects are validated one hop at a time, private and
reserved destinations are rejected, and a file is kept only when its first
bytes are the PDF magic marker.  Network trouble never raises: a failure
returns a :class:`DownloadResult` and the caller falls back to metadata-only.
"""

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from soap.ingest.fetch import USER_AGENT
from soap.ingest.network import (
    MAX_REDIRECTS,
    UnsafeURL,
    is_redirect,
    redirect_target,
    validate_url,
)

# A download is a bigger transfer than a metadata lookup; give it more headroom.
DOWNLOAD_TIMEOUT = 30.0
# Cap a single download so a mislabeled or hostile URL can't fill the disk.
MAX_PDF_BYTES = 100 * 1024 * 1024  # 100 MB
_PDF_MAGIC = b"%PDF"


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


def _content_length(response: httpx.Response) -> int | None:
    value = response.headers.get("content-length")
    if value is None:
        return None
    try:
        length = int(value)
    except (TypeError, ValueError):
        return None
    return length if length >= 0 else None


def download_pdf(
    url: str,
    *,
    client: httpx.Client,
    max_bytes: int = MAX_PDF_BYTES,
    timeout: float = DOWNLOAD_TIMEOUT,
) -> DownloadResult:
    """Stream ``url`` to a temp file, keeping it only if it is a real PDF.

    Redirects are followed manually so each target is checked before the next
    request.  The content type is advisory only: even ``application/pdf`` must
    begin with ``%PDF``.  Returns a :class:`DownloadResult` and never raises.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*"}
    tmp: Path | None = None
    ok = False
    current = url
    try:
        for hop in range(MAX_REDIRECTS + 1):
            validate_url(current)
            with client.stream(
                "GET", current, headers=headers, timeout=timeout,
                follow_redirects=False,
            ) as response:
                if is_redirect(response.status_code):
                    if hop >= MAX_REDIRECTS:
                        return DownloadResult(error=f"too many redirects from {url}")
                    current = redirect_target(
                        current, response.headers.get("location")
                    )
                    continue

                if response.status_code != 200:
                    return DownloadResult(error=f"HTTP {response.status_code} from {current}")

                content_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                declared_length = _content_length(response)
                if declared_length is not None and declared_length > max_bytes:
                    return DownloadResult(
                        error=f"PDF exceeds max size ({max_bytes} bytes)"
                    )

                fd, name = tempfile.mkstemp(suffix=".pdf", prefix="soap-dl-")
                tmp = Path(name)
                written = 0
                head = b""
                with os.fdopen(fd, "wb") as fh:
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        if len(head) < len(_PDF_MAGIC):
                            head = (head + chunk)[: len(_PDF_MAGIC)]
                            if len(head) == len(_PDF_MAGIC) and head != _PDF_MAGIC:
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
                    return DownloadResult(error=f"empty response from {current}")
                # This is intentionally unconditional.  A server cannot make
                # HTML acceptable merely by labeling it application/pdf.
                if head != _PDF_MAGIC:
                    return DownloadResult(
                        error=(
                            "response is not a PDF "
                            f"(content-type {content_type or 'unknown'})"
                        )
                    )

                ok = True
                return DownloadResult(
                    path=tmp,
                    filename=_filename_for(current),
                    mime="application/pdf",
                )
        return DownloadResult(error=f"too many redirects from {url}")
    except (httpx.HTTPError, OSError, UnsafeURL, ValueError) as exc:
        return DownloadResult(error=f"download blocked or failed: {exc}")
    finally:
        if tmp is not None and not ok:
            tmp.unlink(missing_ok=True)
