"""Unit tests for `soap self update` (`soap.cli.selfupdate`).

All network and IO is mocked — the suite makes no real network calls. The GitHub
API and asset downloads are served by an ``httpx.MockTransport`` client so we can
assert on the exact bytes, checksums, and version comparisons the flow uses.
"""

import hashlib
from pathlib import Path

import httpx
import pytest

from soap.cli import selfupdate
from soap.cli.selfupdate import (
    Channel,
    UPGRADE_COMMANDS,
    atomic_replace,
    detect_channel,
    expected_hash,
    is_newer,
    maybe_nudge,
    perform_update,
)

REPO = "GhifariArsa/soap"


# --- Helpers ----------------------------------------------------------------


def _release(tag: str, assets: dict[str, str]) -> dict:
    """A minimal GitHub 'latest release' payload.

    ``assets`` maps asset name -> download URL.
    """
    return {
        "tag_name": tag,
        "assets": [
            {"name": name, "browser_download_url": url}
            for name, url in assets.items()
        ],
    }


def _client(routes: dict[str, httpx.Response]) -> httpx.Client:
    """httpx.Client whose responses are keyed by a URL substring (404 default)."""

    def handler(request: httpx.Request) -> httpx.Response:
        for needle, response in routes.items():
            if needle in str(request.url):
                return response
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _capture():
    """An echo sink plus the list it appends to."""
    lines: list[str] = []
    return lines.append, lines


# --- Version comparison -----------------------------------------------------


@pytest.mark.parametrize(
    "latest, current, newer",
    [
        ("v0.2.0", "0.1.0", True),
        ("v0.2.0", "0.1.dev16+g58a05ec5e", True),  # dev build is "behind" a release
        ("v0.1.0", "0.1.0", False),  # equal is not newer
        ("v0.1.0", "0.2.0", False),  # older is not newer
        ("0.10.0", "0.9.0", True),  # numeric, not lexical, compare
        ("v1.0", "v1.0.0", False),  # zero-padding
    ],
)
def test_is_newer(latest: str, current: str, newer: bool):
    assert is_newer(latest, current) is newer


# --- Channel detection ------------------------------------------------------


@pytest.mark.parametrize(
    "path, channel",
    [
        ("/opt/homebrew/Cellar/soap/0.1.0/bin/soap", Channel.HOMEBREW),
        ("/usr/local/Cellar/soap/0.1.0/bin/soap", Channel.HOMEBREW),
        ("/opt/homebrew/bin/soap", Channel.HOMEBREW),
        ("/home/user/.local/pipx/venvs/soap-tui/bin/soap", Channel.PIPX),
        ("/home/user/.local/share/uv/tools/soap-tui/bin/soap", Channel.UV_TOOL),
        ("/usr/lib/python3.12/site-packages/soap/__main__.py", Channel.PIP),
        ("/home/user/.local/bin/soap", Channel.BINARY),  # installer default
        ("/usr/local/bin/soap", Channel.BINARY),
    ],
)
def test_detect_channel_by_path(path: str, channel: Channel):
    # No PYAPP marker in the env → pure path-based classification.
    assert detect_channel(path, env={}) is channel


def test_detect_channel_pyapp_env_wins():
    # Even a site-packages path is the binary channel when PyApp marks the env.
    assert (
        detect_channel("/usr/lib/python3/site-packages/soap", env={"PYAPP": "1"})
        is Channel.BINARY
    )


@pytest.mark.parametrize(
    "channel, expected",
    [
        (Channel.HOMEBREW, "brew upgrade soap-tui"),
        (Channel.UV_TOOL, "uv tool upgrade soap-tui"),
        (Channel.PIPX, "pipx upgrade soap-tui"),
        (Channel.PIP, "pip install -U soap-tui"),
    ],
)
def test_upgrade_command_text(channel: Channel, expected: str):
    assert UPGRADE_COMMANDS[channel] == expected


def test_perform_update_non_binary_prints_command(monkeypatch):
    # A package-manager install is never touched: print its upgrade line, exit 0.
    monkeypatch.setattr(
        selfupdate, "detect_channel", lambda exe_path=None: Channel.HOMEBREW
    )
    echo, lines = _capture()
    code = perform_update(
        client=_client({}), current="0.1.0", echo=echo, exe_path=Path("/x")
    )
    assert code == 0
    assert any("brew upgrade soap-tui" in line for line in lines)


# --- Update flow (binary channel) -------------------------------------------


@pytest.fixture
def binary_env(monkeypatch, tmp_path):
    """Force the binary channel and a known target/self-exe for the flow tests."""
    monkeypatch.setattr(
        selfupdate, "detect_channel", lambda exe_path=None: Channel.BINARY
    )
    monkeypatch.setattr(selfupdate, "current_target", lambda: "linux-x86_64")
    monkeypatch.setattr(selfupdate.platform, "system", lambda: "Linux")
    # A pre-existing "old" binary we own (writable dir), to be replaced.
    target = tmp_path / "bin" / "soap"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"OLD BINARY")
    return target


def _flow_client(target_name: str, payload: bytes, tag: str = "v0.2.0"):
    """Client serving the latest-release API, checksums, and the asset bytes."""
    digest = hashlib.sha256(payload).hexdigest()
    checksums = f"{digest}  {target_name}\n"
    release = _release(
        tag,
        {
            target_name: f"https://example.test/dl/{target_name}",
            "checksums.txt": "https://example.test/dl/checksums.txt",
        },
    )
    return _client(
        {
            "releases/latest": httpx.Response(200, json=release),
            "dl/checksums.txt": httpx.Response(200, text=checksums),
            f"dl/{target_name}": httpx.Response(200, content=payload),
        }
    ), checksums


def test_perform_update_up_to_date(binary_env):
    client, _ = _flow_client("soap-linux-x86_64", b"NEW", tag="v0.1.0")
    echo, lines = _capture()
    code = perform_update(
        client=client, current="0.1.0", echo=echo, exe_path=binary_env
    )
    assert code == 0
    assert any("up to date" in line for line in lines)
    assert binary_env.read_bytes() == b"OLD BINARY"  # untouched


def test_perform_update_check_only_reports(binary_env):
    client, _ = _flow_client("soap-linux-x86_64", b"NEW", tag="v0.2.0")
    echo, lines = _capture()
    code = perform_update(
        client=client,
        check_only=True,
        current="0.1.0",
        echo=echo,
        exe_path=binary_env,
    )
    assert code == 0
    assert any("v0.2.0" in line for line in lines)
    assert binary_env.read_bytes() == b"OLD BINARY"  # --check never installs


def test_perform_update_installs_and_replaces(binary_env):
    payload = b"NEW BINARY BYTES"
    client, _ = _flow_client("soap-linux-x86_64", payload, tag="v0.2.0")
    echo, lines = _capture()
    code = perform_update(
        client=client, current="0.1.0", echo=echo, exe_path=binary_env
    )
    assert code == 0
    assert binary_env.read_bytes() == payload
    # chmod 0755 applied by the atomic replace.
    assert (binary_env.stat().st_mode & 0o777) == 0o755
    assert any("Updated" in line for line in lines)


def test_perform_update_checksum_mismatch_aborts(binary_env):
    payload = b"NEW BINARY BYTES"
    # Serve the right asset bytes but a checksum for *different* content.
    wrong = hashlib.sha256(b"SOMETHING ELSE").hexdigest()
    release = _release(
        "v0.2.0",
        {
            "soap-linux-x86_64": "https://example.test/dl/soap-linux-x86_64",
            "checksums.txt": "https://example.test/dl/checksums.txt",
        },
    )
    client = _client(
        {
            "releases/latest": httpx.Response(200, json=release),
            "dl/checksums.txt": httpx.Response(
                200, text=f"{wrong}  soap-linux-x86_64\n"
            ),
            "dl/soap-linux-x86_64": httpx.Response(200, content=payload),
        }
    )
    echo, lines = _capture()
    code = perform_update(
        client=client, current="0.1.0", echo=echo, exe_path=binary_env
    )
    assert code == 1
    assert any("mismatch" in line.lower() for line in lines)
    assert binary_env.read_bytes() == b"OLD BINARY"  # refused → untouched


def test_perform_update_unwritable_prefix(monkeypatch, tmp_path):
    monkeypatch.setattr(
        selfupdate, "detect_channel", lambda exe_path=None: Channel.BINARY
    )
    monkeypatch.setattr(selfupdate.platform, "system", lambda: "Linux")
    monkeypatch.setattr(selfupdate.os, "access", lambda *a, **k: False)
    target = tmp_path / "soap"
    target.write_bytes(b"OLD")
    client, _ = _flow_client("soap-linux-x86_64", b"NEW", tag="v0.2.0")
    echo, lines = _capture()
    code = perform_update(
        client=client, current="0.1.0", echo=echo, exe_path=target
    )
    assert code == 1
    assert any("sudo" in line.lower() or "permission" in line.lower() for line in lines)


def test_perform_update_windows_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(
        selfupdate, "detect_channel", lambda exe_path=None: Channel.BINARY
    )
    monkeypatch.setattr(selfupdate.platform, "system", lambda: "Windows")
    target = tmp_path / "soap.exe"
    target.write_bytes(b"OLD")
    client, _ = _flow_client("soap-linux-x86_64", b"NEW", tag="v0.2.0")
    echo, lines = _capture()
    code = perform_update(
        client=client, current="0.1.0", echo=echo, exe_path=target
    )
    assert code == 1
    assert any("Windows" in line for line in lines)


# --- checksum / asset helpers ----------------------------------------------


def test_expected_hash_matches_bare_name():
    body = (
        "aaaa  soap-linux-arm64\n"
        "bbbb  soap-linux-x86_64\n"
        "cccc  soap-macos-arm64\n"
    )
    assert expected_hash(body, "soap-linux-x86_64") == "bbbb"
    assert expected_hash(body, "soap-macos-x86_64") is None


def test_atomic_replace_same_dir(tmp_path):
    target = tmp_path / "soap"
    target.write_bytes(b"old")
    atomic_replace(target, b"brand new")
    assert target.read_bytes() == b"brand new"
    assert (target.stat().st_mode & 0o777) == 0o755
    # No temp turds left behind in the directory.
    assert [p.name for p in tmp_path.iterdir()] == ["soap"]


# --- Startup nudge ----------------------------------------------------------


def test_nudge_prints_when_behind(tmp_path):
    client = _client(
        {"releases/latest": httpx.Response(200, json=_release("v0.2.0", {}))}
    )
    echo, lines = _capture()
    maybe_nudge(tmp_path, current="0.1.0", client=client, now=1000.0, echo=echo)
    assert any("v0.2.0" in line and "soap self update" in line for line in lines)
    # Timestamp cached for rate-limiting.
    assert (tmp_path / selfupdate.NUDGE_CACHE_FILENAME).exists()


def test_nudge_silent_when_up_to_date(tmp_path):
    client = _client(
        {"releases/latest": httpx.Response(200, json=_release("v0.1.0", {}))}
    )
    echo, lines = _capture()
    maybe_nudge(tmp_path, current="0.1.0", client=client, now=1000.0, echo=echo)
    assert lines == []


def test_nudge_respects_24h_cache(tmp_path):
    cache = tmp_path / selfupdate.NUDGE_CACHE_FILENAME
    cache.write_text("1000")
    # A client that would explode if actually called — proves no network happens.
    called = {"n": 0}

    def handler(request):
        called["n"] += 1
        return httpx.Response(200, json=_release("v9.9.9", {}))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    echo, lines = _capture()
    # 12h later — still inside the 24h window.
    maybe_nudge(
        tmp_path,
        current="0.1.0",
        client=client,
        now=1000.0 + 12 * 3600,
        echo=echo,
    )
    assert called["n"] == 0
    assert lines == []


def test_nudge_checks_again_after_interval(tmp_path):
    cache = tmp_path / selfupdate.NUDGE_CACHE_FILENAME
    cache.write_text("1000")
    client = _client(
        {"releases/latest": httpx.Response(200, json=_release("v0.2.0", {}))}
    )
    echo, lines = _capture()
    # 25h later — outside the window, so it checks and prints.
    maybe_nudge(
        tmp_path,
        current="0.1.0",
        client=client,
        now=1000.0 + 25 * 3600,
        echo=echo,
    )
    assert any("v0.2.0" in line for line in lines)


def test_nudge_offline_safe(tmp_path):
    # A transport that raises like a real offline failure.
    def handler(request):
        raise httpx.ConnectError("offline")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    echo, lines = _capture()
    # Must not raise, must print nothing.
    maybe_nudge(tmp_path, current="0.1.0", client=client, now=1000.0, echo=echo)
    assert lines == []
    # And it still recorded the attempt so it won't hammer the network.
    assert (tmp_path / selfupdate.NUDGE_CACHE_FILENAME).exists()
