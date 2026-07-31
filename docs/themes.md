# soap TUI themes

The soap TUI ("Aqua Slate") is fully themeable. Every widget draws from theme
slots rather than hardcoded colors, so a theme change reskins the whole app —
list, detail, review form, help, footer, borders, and selection.

## Bundled themes

| Name               | Look                                             |
|--------------------|--------------------------------------------------|
| `aqua-slate`       | **Default.** Slate + teal focus, amber attention |
| `one-dark`         | Atom One Dark                                    |
| `catppuccin-mocha` | Catppuccin Mocha                                 |

Switch at runtime with **`Ctrl-T`** (cycle) or **`Ctrl-P`** → *Change theme*
(the command palette's theme picker). Your choice is remembered across restarts.

## Choosing the startup theme

The startup theme is the `theme:` key in `$SOAP_DIR/config.yaml`:

```yaml
theme: catppuccin-mocha
```

An unknown name simply falls back to the default. When you switch themes in the
app, soap writes the new name back to this key for you.

## Writing your own theme

Drop a YAML file into **`$SOAP_DIR/themes/`** (any `*.yaml` / `*.yml` name). soap
discovers it on launch; it then shows up in the `Ctrl-P` picker and `Ctrl-T`
cycle. YAML is used to match soap's existing `config.yaml`.

Only `name` is required. Every color role is optional and falls back to the Aqua
Slate value, so a handful of overrides is enough. See
[`example-theme.yaml`](example-theme.yaml) for a complete, commented starting
point.

```yaml
name: my-theme       # required
dark: true           # optional (default true)

primary:    "#37b3a6"   # focus ring / selection / active-pane border / brand
secondary:  "#6fb7d8"   # identifiers (year, arXiv/DOI)
accent:     "#e0a54a"   # attention only: inbox, needs-review, medium confidence
success:    "#57c08a"   # filed / read / high confidence
warning:    "#e0a54a"
error:      "#e0685f"   # low confidence / errors
foreground: "#e6e9ee"
background: "#0d1117"   # base for derived colors; the app root is transparent so your terminal shows through
surface:    "#161b22"   # top bar / footer
panel:      "#1a2130"   # panes / cards

variables:              # optional fine-tuning
  text-muted: "#8b95a5" # quiet secondary text
  border:     "#2b3444" # quiet pane borders
```

**Colors** are hex strings (`#rgb`, `#rrggbb`, or `#rrggbbaa`).

**Two accents, distinct jobs** (the design discipline behind Aqua Slate): keep
`primary` for *where focus is* (selection, active-pane border) and `accent` for
*attention* (the inbox / needs-review / medium confidence). They should read as
clearly different hues so "my cursor is here" never looks like "this needs
attention".

**Graceful failure.** A theme file that is unparseable, isn't a mapping, is
missing its `name`, or carries an invalid color is skipped with a warning toast
on launch — one broken file never stops the app or hides your other themes.
