# soap — overview demo tape (charmbracelet/vhs).
#
# Regenerates docs/demo.gif: a guided tour of the soap TUI over a seeded,
# throwaway library. Do NOT run this tape directly — it expects HOME/SOAP_DIR to
# point at a seeded temp library and `soap` on PATH. Drive it via:
#
#     uv run python scripts/demo.py
#
# which seeds the fake library and renders both this tape and demo-add.tape.
# Keybindings below are soap's real ones (see soap/tui/app.py BINDINGS):
#   arrows/j-k move · `/` search · enter opens · `?` help · `q` quit.

Output docs/demo.gif

Require soap

# --- terminal look ---------------------------------------------------------
Set Shell "bash"
Set FontSize 16
Set Width 1200
Set Height 720
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 55ms
Set WaitTimeout 30s

# --- hidden setup: a clean prompt, nothing recorded ------------------------
Hide
Type "clear"
Enter
Sleep 500ms
Show

# --- launch the TUI over the seeded library --------------------------------
Type "soap"
Enter
# Wait for the browser to paint (list border title is "All documents · N").
Wait+Screen /All documents/
Sleep 1500ms

# --- move through the list; the detail pane inspects each row as you go ----
Down
Sleep 800ms
Down
Sleep 800ms
Down
Sleep 1200ms

# --- `/` search: filter the library live -----------------------------------
# `/` focuses the search box (the key itself is consumed by the binding), then
# the query filters the list on title/author/tag/doi as you type.
Type "/"
Sleep 500ms
Type "transformer"
# The filter is instant on each keystroke; a short beat lets it settle. (We
# Sleep rather than Wait+Screen here because "transformer" already appears in
# unfiltered titles, so a screen match wouldn't prove the filter applied.)
Sleep 1500ms
# Enter accepts the query and hands focus back to the list (filter stays on).
Enter
Sleep 700ms
Down
Sleep 1500ms

# --- `?` opens the keyboard reference card ----------------------------------
Type "?"
Wait+Screen /keyboard reference/
Sleep 2500ms
Escape
Sleep 700ms

# --- quit -------------------------------------------------------------------
Type "q"
Sleep 1200ms
