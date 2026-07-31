"""Themes for the soap TUI — a first-class, user-extensible subsystem.

A soap theme is a Textual :class:`~textual.theme.Theme`: a role→color palette
(``primary``/``secondary``/``accent``/``success``/``error``/``background``/…)
plus custom ``variables``. Because the whole app draws from theme slots — CSS
uses ``$primary``/``$panel``/``$border`` and widgets emit ``$accent``/``$success``
markup, never hardcoded hex — registering a theme reskins the entire app.

Three tiers of themes are available:

* **Bundled** (:data:`BUNDLED_THEMES`) — ship with soap. ``aqua-slate`` is the
  default ("Aqua Slate": teal focus/selection, amber attention/inbox, semantic
  green/red). ``one-dark`` and ``catppuccin-mocha`` prove the mechanism.
* **User** — discovered from ``$SOAP_DIR/themes/*.yaml`` at startup via
  :func:`load_user_themes`. A user drops a small YAML palette in that folder and
  it shows up in the ``Ctrl-P`` command palette's theme picker and the ``Ctrl-T``
  cycle. Malformed or partial files are skipped with a warning (never crash),
  mirroring how :mod:`soap.config` tolerates a broken ``config.yaml``.

YAML (not TOML) is used for theme files to stay consistent with
``soap/config.py``, which already reads YAML and adds no TOML dependency. See
``AGENTS.md`` and ``$SOAP_DIR/themes/example.yaml`` for the format.
"""

from pathlib import Path

import yaml
from textual.theme import Theme

THEMES_DIRNAME = "themes"

# The palette roles a theme may set. Anything omitted falls back to the
# corresponding Aqua Slate value so a minimal user theme still loads cleanly.
_ROLE_KEYS = (
    "primary",
    "secondary",
    "accent",
    "warning",
    "error",
    "success",
    "foreground",
    "background",
    "surface",
    "panel",
)


# --- bundled themes -----------------------------------------------------------

# Recommended default: "Aqua Slate". Two accents with distinct jobs — TEAL for
# focus/selection/brand, AMBER for attention (inbox / needs-review / low
# confidence) — over a slate palette, plus semantic success/error.
aqua_slate = Theme(
    name="aqua-slate",
    primary="#37b3a6",  # teal — focus ring, selection, active pane, brand
    secondary="#6fb7d8",  # soft blue — identifiers / links / years
    accent="#e0a54a",  # amber — inbox, needs-review, attention
    warning="#e0a54a",
    error="#e0685f",
    success="#57c08a",  # green — filed / read / high confidence
    foreground="#e6e9ee",
    background="#0d1117",
    surface="#161b22",
    panel="#1a2130",
    dark=True,
    variables={
        "text-muted": "#8b95a5",
        "border": "#2b3444",  # visible but quiet pane border
        "border-blurred": "#222b39",
        "block-cursor-background": "#1f4f4a",
        "block-cursor-foreground": "#eafaf7",
        "block-cursor-text-style": "bold",
        "block-cursor-blurred-background": "#17323a",
        "block-cursor-blurred-foreground": "#cfe6e2",
        "block-cursor-blurred-text-style": "none",
        "footer-key-foreground": "#37b3a6",
        "footer-description-foreground": "#c3cad6",
        "input-selection-background": "#2f6f68",
        "datatable--header-background": "#161b22",
        "datatable--header-foreground": "#8b95a5",
    },
)

# One Dark (Atom) — a bundled example theme.
one_dark = Theme(
    name="one-dark",
    primary="#61afef",  # blue — focus/selection
    secondary="#56b6c2",  # cyan — identifiers
    accent="#e5c07b",  # yellow — attention/inbox
    warning="#e5c07b",
    error="#e06c75",
    success="#98c379",
    foreground="#abb2bf",
    background="#282c34",
    surface="#21252b",
    panel="#2c313a",
    dark=True,
    variables={
        "text-muted": "#7f8796",
        "border": "#3b4048",
        "border-blurred": "#31363e",
        "block-cursor-background": "#3e4a5c",
        "block-cursor-foreground": "#eaf1fb",
        "block-cursor-text-style": "bold",
        "block-cursor-blurred-background": "#2f3743",
        "footer-key-foreground": "#61afef",
    },
)

# Catppuccin Mocha — a bundled example theme.
catppuccin_mocha = Theme(
    name="catppuccin-mocha",
    primary="#cba6f7",  # mauve — focus/selection
    secondary="#89dceb",  # sky — identifiers
    accent="#fab387",  # peach — attention/inbox
    warning="#f9e2af",
    error="#f38ba8",
    success="#a6e3a1",
    foreground="#cdd6f4",
    background="#1e1e2e",
    surface="#181825",
    panel="#313244",
    dark=True,
    variables={
        "text-muted": "#9399b2",
        "border": "#45475a",
        "border-blurred": "#313244",
        "block-cursor-background": "#463a5e",
        "block-cursor-foreground": "#f5e0ff",
        "block-cursor-text-style": "bold",
        "block-cursor-blurred-background": "#302a44",
        "footer-key-foreground": "#cba6f7",
    },
)

# Order the app registers and cycles with `ctrl+t`. The first entry is the
# shipped default. Append here to bundle another theme.
BUNDLED_THEMES = [aqua_slate, one_dark, catppuccin_mocha]

DEFAULT_THEME = aqua_slate.name

# Back-compat alias — older code referenced ``THEMES``.
THEMES = BUNDLED_THEMES

_AQUA_DEFAULTS = {key: getattr(aqua_slate, key) for key in _ROLE_KEYS}


# --- user-theme loading -------------------------------------------------------


def themes_dir(soap_dir: Path) -> Path:
    """The directory user theme files live in (may not exist)."""
    return soap_dir / THEMES_DIRNAME


def _is_hex_color(value: str) -> bool:
    """True if ``value`` is a ``#rgb``/``#rrggbb``/``#rrggbbaa`` hex string."""
    if not value.startswith("#"):
        return False
    body = value[1:]
    return len(body) in (3, 4, 6, 8) and all(
        c in "0123456789abcdefABCDEF" for c in body
    )


def theme_from_mapping(data: dict) -> Theme:
    """Build a :class:`Theme` from a parsed theme-file mapping.

    Requires a non-empty ``name``. Every color role is optional — a missing one
    falls back to the Aqua Slate value, so a minimal file (just a name and a
    couple of colors) still yields a usable theme. A *present but invalid* color
    is an error (raises ``ValueError``) so genuinely broken files are caught and
    skipped by :func:`load_user_themes` rather than rendering garbage.
    """
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("theme needs a non-empty 'name'")

    colors: dict[str, str] = {}
    for key in _ROLE_KEYS:
        raw = data.get(key)
        if raw is None:
            colors[key] = _AQUA_DEFAULTS[key]
            continue
        value = str(raw).strip()
        if not _is_hex_color(value):
            raise ValueError(f"{key!r} is not a hex color: {raw!r}")
        colors[key] = value

    variables_raw = data.get("variables")
    variables: dict[str, str] = {}
    if isinstance(variables_raw, dict):
        variables = {str(k): str(v) for k, v in variables_raw.items()}
    # Ensure a quiet, readable muted text + border even for a colors-only file.
    variables.setdefault("text-muted", aqua_slate.variables["text-muted"])
    variables.setdefault("border", aqua_slate.variables["border"])

    return Theme(
        name=name.strip(),
        dark=bool(data.get("dark", True)),
        variables=variables,
        **colors,
    )


def load_user_themes(soap_dir: Path) -> tuple[list[Theme], list[str]]:
    """Discover and parse ``$SOAP_DIR/themes/*.yaml`` (and ``*.yml``).

    Returns ``(themes, warnings)``. A file that is unreadable, unparseable, not
    a mapping, missing its ``name``, or carrying an invalid color is skipped and
    described in ``warnings`` — never raised — so one broken theme file can never
    stop the TUI from launching. Files are processed in sorted order.
    """
    directory = themes_dir(soap_dir)
    if not directory.is_dir():
        return [], []

    themes: list[Theme] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.y*ml")):
        if path.suffix.lower() not in (".yaml", ".yml"):
            continue
        try:
            data = yaml.safe_load(path.read_text())
        except (yaml.YAMLError, OSError) as exc:
            warnings.append(f"{path.name}: unreadable ({exc})")
            continue
        if not isinstance(data, dict):
            warnings.append(f"{path.name}: not a theme mapping — skipped")
            continue
        try:
            theme = theme_from_mapping(data)
        except (ValueError, TypeError) as exc:
            warnings.append(f"{path.name}: {exc}")
            continue
        if theme.name in seen:
            warnings.append(f"{path.name}: duplicate theme name {theme.name!r} — skipped")
            continue
        seen.add(theme.name)
        themes.append(theme)
    return themes, warnings
