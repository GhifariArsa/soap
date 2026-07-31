"""Small, defensive helpers for outbound HTTP requests.

The ingest pipeline follows redirects itself rather than delegating that work to
httpx.  That lets it validate every hop before a request is made and apply the
same SSRF policy to provider-selected PDF URLs and explicit user URLs.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_LOCAL_HOSTNAMES = frozenset({"localhost", "localhost."})


class UnsafeURL(ValueError):
    """Raised when a URL is not a permitted network destination."""


def _blocked_ip(value: str) -> bool:
    """Return whether an address is not a globally routable destination."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    # ``is_global`` is intentionally included: it covers unspecified,
    # documentation, benchmarking, multicast and other reserved ranges in
    # addition to the explicit private/loopback/link-local cases.
    return not address.is_global or any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_reserved,
            address.is_multicast,
            address.is_unspecified,
        )
    )


def _resolved_addresses(host: str, port: int) -> set[str]:
    """Resolve a hostname and return all addresses it can reach.

    Checking every result matters for dual-stack hosts: allowing one public
    address while ignoring a private IPv4/IPv6 result still leaves an SSRF
    path.  Resolution errors are unsafe because the eventual HTTP resolver
    could produce a different answer.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror) as exc:
        raise UnsafeURL(f"hostname cannot be resolved: {host}") from exc
    addresses = {info[4][0] for info in infos if info[4]}
    if not addresses:
        raise UnsafeURL(f"hostname has no network addresses: {host}")
    return addresses


def validate_url(url: str, *, resolve_host: bool = True) -> str:
    """Validate and return an HTTP(S) URL safe to request.

    All literal non-global addresses are rejected.  Hostnames are resolved and
    every returned address is checked as well, preventing the common DNS-based
    SSRF bypass.  ``resolve_host=False`` is intended only for a non-networking
    mock transport; production requests always use the default.
    """
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURL("URL is empty or not a string")
    value = url.strip()
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURL(f"malformed URL: {value}") from exc
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURL(f"unsupported URL scheme: {parsed.scheme or 'missing'}")
    if not host:
        raise UnsafeURL("URL has no hostname")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURL("URL credentials are not allowed")

    normalized_host = host.rstrip(".").lower()
    if (
        normalized_host in _LOCAL_HOSTNAMES
        or normalized_host.endswith(".localhost")
        or normalized_host.endswith(".local")
    ):
        raise UnsafeURL(f"private hostname is not allowed: {host}")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _blocked_ip(host):
            raise UnsafeURL(f"private or reserved address is not allowed: {host}")
    elif resolve_host:
        for address in _resolved_addresses(host, port or (443 if scheme == "https" else 80)):
            if _blocked_ip(address):
                raise UnsafeURL(
                    f"hostname resolves to a private or reserved address: {host}"
                )
    return value


def redirect_target(current_url: str, location: str | None) -> str:
    """Resolve a redirect location and validate its syntax before requesting."""
    if not location:
        raise UnsafeURL("redirect has no Location header")
    target = urljoin(current_url, location)
    return validate_url(target)


def is_redirect(status_code: int) -> bool:
    return status_code in _REDIRECT_STATUSES
