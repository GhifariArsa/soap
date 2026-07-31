import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


BLOCK_START = "# >>> soap >>>"
BLOCK_END = "# <<< soap <<<"


def _quote_posix(value: str) -> str:
    """Quote a value for a POSIX shell assignment or command argument."""
    if "\x00" in value:
        raise ValueError("SOAP_DIR contains a NUL byte")
    return shlex.quote(value)


def _quote_fish(value: str) -> str:
    """Quote a value as a fish single-quoted string.

    Fish supports ``\\'`` inside single quotes.  Keeping the whole value in a
    single-quoted string means command substitutions, separators, expansions,
    and newlines remain data rather than fish syntax.
    """
    if "\x00" in value:
        raise ValueError("SOAP_DIR contains a NUL byte")
    return "'" + value.replace("'", "\\'") + "'"


def export_line(shell: str, soap_dir: Path) -> str:
    """Render a safe startup assignment for a supported or unknown shell."""
    value = str(soap_dir)
    if BLOCK_START in value or BLOCK_END in value:
        raise ValueError("SOAP_DIR contains a reserved soap shell-block marker")
    if shell in {"bash", "zsh"}:
        return f"export SOAP_DIR={_quote_posix(value)}"
    if shell == "fish":
        return f"set -gx SOAP_DIR={_quote_fish(value)}"
    # The fallback is intentionally POSIX syntax: it is the only syntax we
    # can safely offer when $SHELL is unknown.  It is also safe to source in
    # bash, zsh, and the other POSIX-compatible shells.
    return f"export SOAP_DIR={_quote_posix(value)}"


# Recognized shells: config file (relative to home) and how to export a var.
SHELLS = {
    "zsh": {"config": "~/.zshrc"},
    "bash": {"config": "~/.bashrc"},
    "fish": {"config": "~/.config/fish/config.fish"},
}


@dataclass
class ShellResult:
    shell: str
    config_path: Path
    export_line: str
    # One of: "added" (block newly written), "updated" (block replaced),
    # "unchanged" (block already correct), "failed" (write failed).
    status: str
    error: str | None = None


def detect_shell() -> str | None:
    """Detect the shell from ``$SHELL``; return its name or ``None``."""
    shell_env = os.environ.get("SHELL", "")
    name = os.path.basename(shell_env)
    return name if name in SHELLS else None


def _guarded_block(export_line: str) -> str:
    return f"{BLOCK_START}\n{export_line}\n{BLOCK_END}"


def write_shell_export(shell: str, soap_dir: Path) -> ShellResult:
    """Write (or refresh) the guarded ``SOAP_DIR`` block in the shell config.

    The block is replaced in place when present so re-runs never append a
    duplicate. Values are shell-quoted before they enter generated startup
    code, including paths containing newlines or command syntax. Failures
    (including an unrepresentable NUL byte) are captured in the result rather
    than raised, so the caller can warn and continue.
    """
    spec = SHELLS[shell]
    config_path = Path(os.path.expanduser(spec["config"]))
    try:
        line = export_line(shell, soap_dir)
    except ValueError as exc:
        return ShellResult(shell, config_path, "", "failed", error=str(exc))
    block = _guarded_block(line)

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        existing = config_path.read_text() if config_path.exists() else ""

        pattern = re.compile(
            r"^" + re.escape(BLOCK_START) + r"\n.*?^" + re.escape(BLOCK_END) + r"$",
            re.MULTILINE | re.DOTALL,
        )
        match = pattern.search(existing)
        if match:
            if match.group(0) == block:
                return ShellResult(shell, config_path, line, "unchanged")
            # Use a replacement function so backslashes in the path are treated
            # literally, not as regex group/escape references.
            new_content = pattern.sub(lambda _match: block, existing)
            config_path.write_text(new_content)
            return ShellResult(shell, config_path, line, "updated")

        # No existing block: append, ensuring a clean separating newline.
        prefix = existing
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix and not prefix.endswith("\n\n"):
            prefix += "\n"
        config_path.write_text(prefix + block + "\n")
        return ShellResult(shell, config_path, line, "added")
    except OSError as exc:
        return ShellResult(shell, config_path, line, "failed", error=str(exc))


def source_command(config_path: Path) -> str:
    """The command a user runs to load the export into the current shell."""
    return f"source {_quote_posix(str(config_path))}"
