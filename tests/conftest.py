"""Shared fixtures: a fresh library and a helper for building test files."""

from pathlib import Path

import httpx
import pytest

from soap.library import Library


@pytest.fixture
def library(tmp_path: Path) -> Library:
    """An initialized, empty library rooted in a temp directory."""
    lib = Library(tmp_path / "lib")
    lib.create_directories()
    lib.initialize_database()
    return lib


@pytest.fixture
def make_pdf(tmp_path: Path):
    """Factory that writes a placeholder file. soap never parses it, so the
    bytes only need to be unique enough to give a distinct sha256."""

    def _make(name: str) -> Path:
        path = tmp_path / name
        path.write_bytes(b"%PDF-1.4 placeholder for " + name.encode())
        return path

    return _make


def mock_client(routes: dict[str, httpx.Response]) -> httpx.Client:
    """Build an httpx.Client whose responses are keyed by URL substring.

    ``routes`` maps a substring of the request URL to the response to return; a
    request matching no route yields a 404.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for needle, response in routes.items():
            if needle in url:
                return response
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))
