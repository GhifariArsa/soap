import os
import re
from dataclasses import dataclass
from pathlib import Path


BLOCK_START = "# >>> soap >>>"
BLOCK_END = "# <<< soap <<<"

# Recognized shells: config file (relative to home) and how to export a var.
SHELLS = {
    "zsh": {
        "config": "~/.zshrc",
        "export": lambda path: f'export SOAP_DIR="{path}"',
    },
    "bash": {
        "config": "~/.bashrc",
        "export": lambda path: f'export SOAP_DIR="{path}"',
    },
    "fish": {
        "config": "~/.config/fish/config.fish",
        "export": lambda path: f'set -gx SOAP_DIR "{path}"',
    },
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
    duplicate. Failures (permissions, missing dir) are captured in the result
    rather than raised, so the caller can warn and continue.
    """
    spec = SHELLS[shell]
    export_line = spec["export"](str(soap_dir))
    config_path = Path(os.path.expanduser(spec["config"]))
    block = _guarded_block(export_line)

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        existing = config_path.read_text() if config_path.exists() else ""

        pattern = re.compile(
            re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END),
            re.DOTALL,
        )
        match = pattern.search(existing)
        if match:
            if match.group(0) == block:
                return ShellResult(shell, config_path, export_line, "unchanged")
            # Use a replacement function so backslashes in the path are treated
            # literally, not as regex group/escape references.
            new_content = pattern.sub(lambda _match: block, existing)
            config_path.write_text(new_content)
            return ShellResult(shell, config_path, export_line, "updated")

        # No existing block: append, ensuring a clean separating newline.
        prefix = existing
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix and not prefix.endswith("\n\n"):
            prefix += "\n"
        config_path.write_text(prefix + block + "\n")
        return ShellResult(shell, config_path, export_line, "added")
    except OSError as exc:
        return ShellResult(shell, config_path, export_line, "failed", error=str(exc))


def source_command(config_path: Path) -> str:
    """The command a user runs to load the export into the current shell."""
    return f"source {config_path}"
