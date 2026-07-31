"""Private permissions for files and directories owned by a soap library."""

import os
from pathlib import Path

PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def make_private(path: Path, *, directory: bool) -> None:
    """Restrict an existing library path to its owner.

    Source files are allowed to carry their original metadata while they are
    being copied into a library, but stored files must not inherit permissive
    mode bits.  Callers deliberately invoke this after creating or copying a
    path so the resulting mode is independent of the process umask and source
    permissions.
    """
    os.chmod(path, PRIVATE_DIRECTORY_MODE if directory else PRIVATE_FILE_MODE)
