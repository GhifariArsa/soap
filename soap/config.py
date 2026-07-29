"""Library-level configuration read from ``$SOAP_DIR/config.yaml``.

A pure, side-effect-free loader: it reads one small YAML file and returns a
validated :class:`Config`. The file is optional — a missing file, a missing
key, or even malformed YAML all fall back to defaults rather than raising, so a
library without a ``config.yaml`` behaves exactly as it did before this module
existed.

The only setting today is ``always_review``. The PRD recommends users set::

    always_review: true

while they are still establishing trust in the ingestion pipeline, so that
every ``add`` lands in the ``needs_review`` queue for a human to confirm before
it is considered filed. The default is ``false`` (never force review) so that
an absent config never changes existing behavior.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_FILENAME = "config.yaml"


@dataclass(frozen=True)
class Config:
    """Resolved soap library configuration.

    ``always_review`` — when true, every ``add`` is forced into the
    ``needs_review`` queue regardless of confidence or source. Defaults to
    ``false`` so a missing/empty config leaves ingestion behavior unchanged.
    """

    always_review: bool = False


def config_path(soap_dir: Path) -> Path:
    """The path to a library's ``config.yaml`` (may not exist)."""
    return soap_dir / CONFIG_FILENAME


def load_config(soap_dir: Path) -> Config:
    """Load ``$SOAP_DIR/config.yaml``, defaulting gracefully.

    Returns the default :class:`Config` when the file is absent, empty, not a
    mapping, or unparseable YAML — a broken or missing config never aborts an
    operation; it just means "no overrides". Unknown keys are ignored so the
    schema can grow without breaking older files.
    """
    path = config_path(soap_dir)
    if not path.exists():
        return Config()
    try:
        data = yaml.safe_load(path.read_text())
    except (yaml.YAMLError, OSError):
        return Config()
    if not isinstance(data, dict):
        return Config()
    return Config(always_review=bool(data.get("always_review", False)))
