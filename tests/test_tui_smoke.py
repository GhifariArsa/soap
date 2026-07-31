"""Regression coverage for the frozen-binary TUI smoke readiness handshake."""

from types import SimpleNamespace

from scripts.tui_smoke import _wait_for_marker


def _ready_proc():
    return SimpleNamespace(poll=lambda: None)


def test_smoke_waits_for_mounted_tui_marker_not_terminal_setup():
    """Initial terminal control bytes must not trigger the q handoff."""
    master = 7
    chunks = iter([b"\x1b[?1049h\x1b[?25l", b"... BRO", b"WSE ..."])
    captured = bytearray()

    def read(_fd, _size):
        return next(chunks)

    def select_ready(_fds, _write, _error, _timeout):
        return ([master], [], [])

    assert _wait_for_marker(
        master,
        _ready_proc(),
        captured,
        deadline=1.0,
        select_fn=select_ready,
        read_fn=read,
        clock=lambda: 0.0,
    )
    assert b"BROWSE" in captured


def test_smoke_does_not_ready_on_setup_without_rendered_marker():
    master = 7
    captured = bytearray()
    chunks = iter([b"\x1b[?1049h\x1b[?25l"])
    times = iter([0.0, 2.0])

    def read(_fd, _size):
        return next(chunks)

    def select_ready(_fds, _write, _error, _timeout):
        return ([master], [], [])

    assert not _wait_for_marker(
        master,
        _ready_proc(),
        captured,
        deadline=1.0,
        select_fn=select_ready,
        read_fn=read,
        clock=lambda: next(times),
    )
    assert b"BROWSE" not in captured
