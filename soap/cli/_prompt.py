"""Shared terminal prompt for the inline field-by-field correction walk.

Both ``soap inbox review`` (the ``[c]orrect`` action) and ``soap add --confirm``
drive :func:`soap.library.prompt_fields` with this per-field prompter, so the
walk looks and behaves identically wherever it appears.
"""

import typer


def prompt_field(field: str, current: str) -> str:
    """Prompt for one core field, prefilled with the detected value.

    Enter (empty input) keeps ``current``; typed input overrides it — the
    keep/override semantics live in :func:`soap.library.prompt_fields`; this only
    renders the prompt and reads a line.

    If the ``readline`` module is available the detected value is loaded into the
    editable line buffer (true in-place editing you can arrow through); the hook
    is cleared afterwards so it never bleeds into the next prompt. When
    ``readline`` is missing it degrades to a ``[detected]:`` hint rather than
    crashing. Raises ``EOFError`` on end-of-input so a piped/empty stdin ends the
    walk cleanly instead of hanging.
    """
    try:
        import readline
    except ImportError:  # pragma: no cover - readline ships on macOS/Linux
        readline = None

    label = typer.style(f"  {field:<8}", fg=typer.colors.BRIGHT_BLACK)

    if readline is not None:
        def _prefill() -> None:
            readline.insert_text(current)
            readline.redisplay()

        readline.set_pre_input_hook(_prefill)
        prompt = f"{label} : "
    else:
        hint = f"[{current}]" if current else "[]"
        prompt = f"{label} {hint}: "

    try:
        return input(prompt)
    finally:
        if readline is not None:
            readline.set_pre_input_hook(None)
