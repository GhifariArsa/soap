#!/usr/bin/env python3
"""Pseudo-tty smoke test for the built ``soap`` binary (CI, plan §3.6).

The standalone binary launches the Textual TUI when run with no subcommand, and
Textual demands a real terminal. This driver:

  1. Prepares a throwaway library under a temp ``$SOAP_DIR`` (``soap init``).
  2. Spawns the binary under a pseudo-tty via the stdlib :mod:`pty`.
  3. Waits for the TUI to draw, sends ``q`` to quit, and asserts a clean exit
     within a short deadline.

A Python ``pty`` driver is used in preference to ``unbuffer``/``script`` because
those tools' flags differ across macOS and Linux runners; :mod:`pty` behaves
identically on both. Runs on the runner's own ``python3`` (present on every
GitHub-hosted macOS/Linux image) — it drives the *frozen* binary, so no project
env is required.

Usage:  python3 scripts/tui_smoke.py ./soap-<target>
Exit:   0 on a clean launch + quit, non-zero (with diagnostics) otherwise.
"""

from __future__ import annotations

import os
import pty
import select
import subprocess
import sys
import tempfile
import time

# Keep the whole smoke well under any job timeout: the TUI should draw in well
# under a second and quit promptly once it receives 'q'.
DRAW_TIMEOUT = 20.0  # seconds to see the first frame of output
QUIT_TIMEOUT = 20.0  # seconds to exit after we send 'q'


def _run_init(binary: str, soap_dir: str) -> None:
    """Create a library so the TUI has something to open (non-TUI codepath)."""
    env = dict(os.environ, SOAP_DIR=soap_dir)
    proc = subprocess.run(
        [binary, "init", "--path", soap_dir, "--shell", "bash"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        sys.stderr.write("soap init failed during TUI smoke setup:\n")
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(1)


def _drive_tui(binary: str, soap_dir: str) -> None:
    """Launch the TUI under a pty, confirm it draws, then quit with 'q'."""
    env = dict(os.environ, SOAP_DIR=soap_dir, TERM="xterm-256color")
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [binary],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
    )
    os.close(slave)

    captured = bytearray()

    def _pump(deadline: float) -> None:
        """Drain pty output until the deadline (keeps the child unblocked)."""
        while time.monotonic() < deadline:
            timeout = max(0.0, deadline - time.monotonic())
            rlist, _, _ = select.select([master], [], [], min(timeout, 0.25))
            if master in rlist:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                captured.extend(chunk)

    try:
        # 1. Wait for the first frame of output = the TUI is up and rendering.
        drew = False
        draw_deadline = time.monotonic() + DRAW_TIMEOUT
        while time.monotonic() < draw_deadline:
            rlist, _, _ = select.select([master], [], [], 0.25)
            if master in rlist:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if chunk:
                    captured.extend(chunk)
                    drew = True
                    break
            if proc.poll() is not None:
                break

        if proc.poll() is not None:
            sys.stderr.write(
                f"TUI exited before drawing (code {proc.returncode}).\n"
            )
            _dump(captured)
            raise SystemExit(1)

        if not drew:
            sys.stderr.write("TUI produced no output within the draw timeout.\n")
            proc.kill()
            _dump(captured)
            raise SystemExit(1)

        # 2. Ask it to quit and confirm a clean, prompt exit.
        os.write(master, b"q")
        quit_deadline = time.monotonic() + QUIT_TIMEOUT
        while time.monotonic() < quit_deadline:
            _pump(time.monotonic() + 0.25)
            if proc.poll() is not None:
                break

        if proc.poll() is None:
            sys.stderr.write("TUI did not exit after 'q'; killing.\n")
            proc.kill()
            _dump(captured)
            raise SystemExit(1)

        if proc.returncode != 0:
            sys.stderr.write(
                f"TUI exited non-zero after 'q' (code {proc.returncode}).\n"
            )
            _dump(captured)
            raise SystemExit(1)
    finally:
        try:
            os.close(master)
        except OSError:
            pass
        if proc.poll() is None:
            proc.kill()

    print("TUI pty smoke: launched and clean-exited OK.")


def _dump(captured: bytes | bytearray) -> None:
    sys.stderr.write("--- captured pty output (tail) ---\n")
    tail = bytes(captured[-2000:])
    sys.stderr.write(tail.decode("utf-8", "replace"))
    sys.stderr.write("\n--- end ---\n")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: tui_smoke.py <path-to-soap-binary>\n")
        return 2
    binary = os.path.abspath(argv[1])
    if not os.path.isfile(binary):
        sys.stderr.write(f"binary not found: {binary}\n")
        return 2
    with tempfile.TemporaryDirectory(prefix="soap-tui-smoke-") as soap_dir:
        _run_init(binary, soap_dir)
        _drive_tui(binary, soap_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
