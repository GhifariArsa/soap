"""Registered Textual themes for the soap TUI.

`soap-dark` reproduces the mockup palette. Themes are plain data, so adding
another is just appending a ``Theme`` to ``THEMES`` — the app registers every
entry and they show up in the command palette's theme picker and the `ctrl+t`
cycle for free. Widgets never hardcode hex; colors come from theme slots
(``$primary``/``$accent``/``$surface``/…) and the custom ``variables`` below, so
a new theme actually reskins the whole app.
"""

from textual.theme import Theme

soap_dark = Theme(
    name="soap-dark",
    primary="#4f8fd0",  # blue — selection, links, year
    secondary="#7fb0e0",
    accent="#d99a2b",  # amber — logo, inbox bar
    warning="#d99a2b",
    error="#cf6b6b",
    success="#5aa46a",
    foreground="#d9dbdf",
    background="#0e0f11",
    surface="#17181b",  # top bar / inbox bar
    panel="#1b1d21",  # sidebar / detail
    dark=True,
    variables={
        # Drive the ListView cursor off the theme so selection matches the blue.
        "block-cursor-background": "#24344f",
        "block-cursor-foreground": "#e8ecf2",
        "block-cursor-blurred-background": "#191f2b",
        "block-cursor-blurred-foreground": "#c3c7cd",
        "block-cursor-text-style": "none",
        "block-cursor-blurred-text-style": "none",
        "footer-key-foreground": "#d99a2b",
    },
)

# Ordered list the app registers and cycles through with `ctrl+t`. Append here
# to ship more themes.
THEMES = [soap_dark]

DEFAULT_THEME = soap_dark.name
