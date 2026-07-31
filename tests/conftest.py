"""Shared fixtures: a fresh library and a helper for building test files."""

import socket
from pathlib import Path

import httpx
import pytest

from soap.library import Library

# A genuinely global address every stubbed hostname resolves to, so the SSRF
# validation runs its real ``_blocked_ip`` logic and treats the host as public.
_PUBLIC_STUB_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _deterministic_dns(monkeypatch):
    """Keep mocked network tests hermetic: never touch live DNS.

    Production still resolves hostnames and blocks non-global destinations; the
    tests just must not depend on a resolver. Any hostname that reaches
    resolution maps to a fixed public address, so the resolution branch of
    ``validate_url`` runs without a network lookup. IP literals and blocked
    hostnames (loopback/private/link-local/reserved, ``localhost``, ``.local``)
    are rejected *before* resolution, so those assertions are unaffected.
    """

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_STUB_IP, port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


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
