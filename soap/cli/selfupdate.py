"""``soap self update`` — replace the standalone binary from GitHub Releases.

The PyApp launcher is built with ``PYAPP_SELF_COMMAND=none`` (PyApp's own
``self`` group is disabled), so ``soap self update`` is *our* Typer subcommand,
not PyApp's. PyApp's built-in updater would only reinstall the pip package into
its managed venv — it would never replace the ~18 MB launcher or its embedded
CPython — so we own the updater and swap the whole artifact.

The design (distribution plan §6) is:

* **Never fight a package manager.** :func:`detect_channel` classifies how soap
  was installed; for Homebrew / pipx / uv-tool / plain-pip we just print the
  right upgrade command and exit 0.
* **Binary channel only** performs the swap: query the GitHub "latest release"
  API, compare tags, download ``soap-<target>``, verify its sha256 against the
  release ``checksums.txt``, and *atomically* replace the running launcher via a
  same-directory temp file + :func:`os.replace` (safe on macOS/Linux — the live
  process keeps the old inode; the next launch is new). The launcher path is the
  OS self-exe (``/proc/self/exe`` / ``_NSGetExecutablePath``), **not**
  ``sys.executable`` (which under PyApp points at the managed CPython).
* A once-per-24h startup **nudge** (:func:`maybe_nudge`) prints a single dim
  line when a newer release exists; it never blocks and swallows every network
  error so a normal command is never slowed or broken when offline.

Everything network- or IO-touching takes an injected ``httpx.Client`` / paths /
clock so the whole flow is unit-tested with no real network (see
``tests/test_selfupdate.py``).

Windows is intentionally *not* a v1 target. The running ``.exe`` cannot be
overwritten in place; the swap path there is rename-aside + replace + delete on
next launch (plan §6.2). :func:`perform_update` refuses with a clear pointer on
Windows — see the ``NotImplemented`` marker below when adding it.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import os
import platform
import re
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
import typer

REPO = "GhifariArsa/soap"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DIST_NAME = "soap-tui"

CHECKSUMS_ASSET = "checksums.txt"
NUDGE_CACHE_FILENAME = "last_update_check"
NUDGE_INTERVAL_SECONDS = 24 * 60 * 60

_HTTP_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": f"{DIST_NAME}-self-update",
}


class Channel(str, Enum):
    """How this soap was installed — decides what ``self update`` does."""

    BINARY = "standalone binary"
    HOMEBREW = "Homebrew"
    PIPX = "pipx"
    UV_TOOL = "uv tool"
    PIP = "pip"


#: The upgrade command to print for each package-manager channel. The binary
#: channel is absent because it is the one we handle ourselves.
UPGRADE_COMMANDS: dict[Channel, str] = {
    Channel.HOMEBREW: "brew upgrade soap",
    Channel.UV_TOOL: f"uv tool upgrade {DIST_NAME}",
    Channel.PIPX: f"pipx upgrade {DIST_NAME}",
    Channel.PIP: f"pip install -U {DIST_NAME}",
}


# --- Version resolution / comparison ----------------------------------------


def current_version() -> str:
    """Best-effort version of the running soap.

    Prefers installed distribution metadata; falls back to the generated
    ``soap/_version.py`` (written by hatch-vcs at build time), then a sentinel.
    This mirrors ``soap --version`` so the two never disagree.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(DIST_NAME)
    except PackageNotFoundError:
        try:
            from soap._version import __version__

            return __version__
        except Exception:
            return "0.0.0+unknown"


def _release_tuple(v: str) -> tuple[int, ...]:
    """Parse the leading numeric release segment of a version/tag string.

    ``v0.2.0`` → ``(0, 2, 0)``; ``0.1.dev16+g58a05ec`` → ``(0, 1)``. Anything
    after the first non-``[0-9.]`` character (pre-release/dev/local segments) is
    dropped — a coarse comparison that is intentionally conservative: a ``.devN``
    build only counts as "behind" the same release, never ahead.
    """
    head = re.split(r"[^0-9.]", v.strip().lstrip("vV"), maxsplit=1)[0]
    parts: list[int] = []
    for piece in head.split("."):
        if piece.isdigit():
            parts.append(int(piece))
        else:
            break
    return tuple(parts) or (0,)


def is_newer(latest: str, current: str) -> bool:
    """True when ``latest`` names a strictly newer release than ``current``."""
    a, b = _release_tuple(latest), _release_tuple(current)
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return a > b


# --- Platform / launcher resolution -----------------------------------------


def current_target() -> str | None:
    """Map this host to a release asset target, or ``None`` if unsupported.

    Targets mirror the release matrix and the installer: ``macos-arm64``,
    ``linux-arm64``, ``linux-x86_64``.
    """
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin":
        if machine in ("arm64", "aarch64"):
            return "macos-arm64"
    elif system == "Linux":
        if machine in ("aarch64", "arm64"):
            return "linux-arm64"
        if machine in ("x86_64", "amd64"):
            return "linux-x86_64"
    return None


def _macos_executable_path() -> Path | None:
    """The running executable via ``_NSGetExecutablePath`` (macOS)."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.dylib")
        size = ctypes.c_uint32(1024)
        buf = ctypes.create_string_buffer(size.value)
        if libc._NSGetExecutablePath(buf, ctypes.byref(size)) != 0:
            # Buffer too small: ``size`` now holds the required length.
            buf = ctypes.create_string_buffer(size.value)
            libc._NSGetExecutablePath(buf, ctypes.byref(size))
        return Path(os.fsdecode(buf.value)).resolve()
    except Exception:
        return None


def resolve_self_exe() -> Path:
    """Absolute path of the on-PATH launcher to replace.

    Uses the OS self-exe (``/proc/self/exe`` on Linux, ``_NSGetExecutablePath``
    on macOS) rather than ``sys.executable`` — under PyApp the latter is the
    managed CPython, not the launcher the user runs. Falls back to ``argv[0]``.
    """
    system = platform.system()
    if system == "Linux":
        try:
            return Path("/proc/self/exe").resolve()
        except OSError:
            pass
    elif system == "Darwin":
        path = _macos_executable_path()
        if path is not None:
            return path
    return Path(sys.argv[0]).resolve()


# --- Install-channel detection ----------------------------------------------


def detect_channel(
    exe_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> Channel:
    """Classify how soap was installed.

    Order matters: PyApp sets a ``PYAPP`` marker in the process environment, so
    that wins. Otherwise the launcher path is matched against the well-known
    prefixes of each package manager; anything else is assumed to be a bare
    standalone binary (installed by ``install.sh`` outside a package prefix).
    """
    env = os.environ if env is None else env
    if env.get("PYAPP"):
        return Channel.BINARY

    raw = exe_path if exe_path is not None else resolve_self_exe()
    low = str(raw).replace("\\", "/").lower()

    if "/cellar/" in low or "/opt/homebrew/" in low or "/linuxbrew/" in low:
        return Channel.HOMEBREW
    if "/pipx/" in low:
        return Channel.PIPX
    if "/uv/tools/" in low or "/uv/tool/" in low:
        return Channel.UV_TOOL
    if (
        "/site-packages/" in low
        or "/dist-packages/" in low
        or os.path.basename(low).startswith("python")
    ):
        return Channel.PIP
    return Channel.BINARY


# --- GitHub release access --------------------------------------------------


def fetch_latest_release(client: httpx.Client) -> dict[str, Any]:
    """The GitHub "latest release" payload (raises on HTTP error)."""
    resp = client.get(
        LATEST_RELEASE_URL, headers=_HTTP_HEADERS, follow_redirects=True
    )
    resp.raise_for_status()
    return resp.json()


def _asset_url(release: Mapping[str, Any], name: str) -> str | None:
    """The download URL of the release asset named exactly ``name``."""
    for asset in release.get("assets", []):
        if asset.get("name") == name:
            return asset.get("browser_download_url")
    return None


def expected_hash(checksums: str, asset_name: str) -> str | None:
    """Find ``asset_name``'s sha256 in a ``checksums.txt`` body.

    Each line is ``<hex>  <filename>`` (the combined ``cat *.sha256`` manifest);
    the filename is the bare asset name. Returns the lowercased hex, or ``None``.
    """
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) >= 2 and os.path.basename(parts[-1]) == asset_name:
            return parts[0].lower()
    return None


# --- Atomic replace ---------------------------------------------------------


def atomic_replace(target: Path, data: bytes) -> None:
    """Replace ``target`` with ``data`` atomically on macOS/Linux.

    Writes to a temp file in the *same directory* (same filesystem → atomic
    rename), ``chmod 0755``, then :func:`os.replace`. The running process keeps
    executing the old, now-unlinked inode; the next launch runs the new binary.
    """
    directory = target.parent
    fd, tmp_name = tempfile.mkstemp(prefix=".soap-update-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp_name, 0o755)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# --- Orchestration ----------------------------------------------------------


def perform_update(
    *,
    client: httpx.Client,
    check_only: bool = False,
    exe_path: Path | None = None,
    current: str | None = None,
    echo: Callable[[str], None] = print,
) -> int:
    """Run the update flow and return a process exit code.

    Fully injectable for testing: ``client`` supplies the network, ``exe_path``
    the launcher to (would-)replace, ``current`` the running version, ``echo``
    the sink for user-facing lines.
    """
    channel = detect_channel(exe_path)
    cur = current if current is not None else current_version()

    # Never fight a package manager: point at the right command and stop.
    if channel is not Channel.BINARY:
        echo(f"soap was installed via {channel.value}. Upgrade with:")
        echo(f"  {UPGRADE_COMMANDS[channel]}")
        return 0

    try:
        release = fetch_latest_release(client)
    except Exception as exc:  # noqa: BLE001 - surface a clean message, not a stack
        echo(f"Could not reach GitHub to check for updates: {exc}")
        return 1

    latest = str(release.get("tag_name") or "")
    if not latest:
        echo("Latest release has no tag — cannot determine a version to install.")
        return 1

    if not is_newer(latest, cur):
        echo(f"soap is up to date ({cur}).")
        return 0

    if check_only:
        echo(f"A new soap is available: {latest} (current {cur}).")
        echo("Run `soap self update` to install it.")
        return 0

    target = exe_path if exe_path is not None else resolve_self_exe()

    if platform.system() == "Windows":
        # NotImplemented (v1): a running .exe can't be overwritten in place. The
        # swap is rename-aside + move-new + delete-old-on-next-launch (§6.2).
        echo("Automatic self-update isn't supported on Windows yet.")
        echo(f"Reinstall {latest} from https://github.com/{REPO}/releases/latest")
        return 1

    if not os.access(target.parent, os.W_OK):
        echo(f"Cannot write to {target.parent} (permission denied).")
        echo("Re-run the installer, or use sudo, to update a root-owned install.")
        return 1

    tname = current_target()
    if tname is None:
        echo(
            f"Unsupported platform {platform.system()}/{platform.machine()} — "
            "no matching release asset."
        )
        return 1
    asset_name = f"soap-{tname}"

    asset_url = _asset_url(release, asset_name)
    checksums_url = _asset_url(release, CHECKSUMS_ASSET)
    if asset_url is None or checksums_url is None:
        echo(f"Release {latest} is missing {asset_name} or {CHECKSUMS_ASSET}.")
        return 1

    try:
        checksums = client.get(checksums_url, follow_redirects=True).raise_for_status().text
    except Exception as exc:  # noqa: BLE001
        echo(f"Could not download checksums: {exc}")
        return 1

    want = expected_hash(checksums, asset_name)
    if want is None:
        echo(f"{CHECKSUMS_ASSET} has no entry for {asset_name} — refusing to install.")
        return 1

    echo(f"Downloading {asset_name} ({latest})...")
    try:
        resp = client.get(asset_url, follow_redirects=True)
        resp.raise_for_status()
        data = resp.content
    except Exception as exc:  # noqa: BLE001
        echo(f"Download failed: {exc}")
        return 1

    got = hashlib.sha256(data).hexdigest()
    if got != want:
        echo("Checksum mismatch — refusing to install.")
        echo(f"  expected {want}")
        echo(f"  got      {got}")
        return 1

    try:
        atomic_replace(target, data)
    except OSError as exc:
        echo(f"Failed to replace {target}: {exc}")
        return 1

    echo(f"Updated soap {cur} -> {latest}. Re-run `soap`.")
    return 0


# --- Startup nudge ----------------------------------------------------------


def _read_cache_ts(cache: Path) -> float | None:
    try:
        return float(cache.read_text().strip())
    except Exception:
        return None


def _write_cache_ts(cache: Path, ts: float) -> None:
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(f"{ts:.0f}\n")
    except OSError:
        pass


def _default_nudge_echo(message: str) -> None:
    """Print the nudge dimmed, to stderr, so it never pollutes stdout."""
    typer.secho(message, fg=typer.colors.BRIGHT_BLACK, err=True)


def maybe_nudge(
    soap_dir: Path | str,
    *,
    current: str | None = None,
    client: httpx.Client | None = None,
    now: float | None = None,
    echo: Callable[[str], None] | None = None,
    interval: int = NUDGE_INTERVAL_SECONDS,
) -> None:
    """At most once per ``interval`` seconds, hint that a newer soap exists.

    Rate-limited via a timestamp file in ``$SOAP_DIR``; the timestamp is written
    *before* the network call so a hang or failure still counts as "checked" and
    the next command won't retry for another day. Every error — offline, DNS,
    bad JSON, unwritable dir — is swallowed: this must never block or break a
    normal command.
    """
    try:
        stamp = now if now is not None else time.time()
        cache = Path(soap_dir) / NUDGE_CACHE_FILENAME
        last = _read_cache_ts(cache)
        if last is not None and (stamp - last) < interval:
            return
        _write_cache_ts(cache, stamp)

        owned = client is None
        active = client if client is not None else httpx.Client(timeout=2.0)
        try:
            release = fetch_latest_release(active)
        finally:
            if owned:
                active.close()

        latest = str(release.get("tag_name") or "")
        cur = current if current is not None else current_version()
        if latest and is_newer(latest, cur):
            (echo or _default_nudge_echo)(
                f"A new soap is available: {latest} (run: soap self update)"
            )
    except Exception:
        # Offline / rate-limited / malformed — silently do nothing.
        return


# --- Typer wiring -----------------------------------------------------------

app = typer.Typer(help="Manage the soap binary itself.")


@app.command("update")
def update(
    check: bool = typer.Option(
        False, "--check", help="Report update status only; don't install."
    ),
) -> None:
    """Update the standalone soap binary from GitHub Releases.

    If soap was installed via a package manager, prints the right upgrade
    command instead of touching the install.
    """
    with httpx.Client(timeout=30.0) as client:
        code = perform_update(
            client=client, check_only=check, echo=typer.echo
        )
    raise typer.Exit(code)
