"""Shared content-markup helpers for the soap TUI.

Textual's content markup strips whitespace at the leading edge of a styled span
and between a styled span and adjacent *plain* text — that is the root cause of
the old ``sourcearxiv`` / ``movej/k`` mashing (report §9-A). The fix is to emit
separators as *whitespace-only styled spans*, which survive. Every label/value
and key/description pair in the TUI goes through :func:`sep` so the spacing is
correct by construction.
"""

from __future__ import annotations


def sep(n: int = 1) -> str:
    """A separator that survives markup whitespace-stripping: a styled space."""
    return f"[$text-muted]{' ' * n}[/]"


def key(label: str, desc: str, key_color: str = "$primary") -> str:
    """A ``key<sep>description`` fragment for footers/legends (keys colored)."""
    return f"[b {key_color}]{label}[/]" + sep(1) + f"[$text-muted]{desc}[/]"


def confidence_meter(conf: float, width: int = 20) -> tuple[str, str, str]:
    """Return ``(band, color_token, meter_markup)`` for a confidence value.

    Green ≥ 0.70, amber 0.40–0.69, red < 0.40 — the at-a-glance triage color.
    """
    if conf >= 0.70:
        band, color = "high", "$success"
    elif conf >= 0.40:
        band, color = "medium", "$accent"
    else:
        band, color = "low", "$error"
    filled = max(1, min(width, round(conf * width)))
    meter = f"[{color}]{'█' * filled}[/][$panel-lighten-2]{'█' * (width - filled)}[/]"
    return band, color, meter
