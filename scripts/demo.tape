# soap — browse/overview demo tape (charmbracelet/vhs).
#
# Regenerates docs/demo.gif: a tour of moving around and organizing a library in
# the soap TUI, over a seeded, fully-offline throwaway library. Do NOT run this
# tape directly — it expects HOME/SOAP_DIR to point at a seeded temp library and
# `soap` on PATH. Drive it via:
#
#     uv run python scripts/demo.py
#
# Keybindings below are soap's real ones (see soap/tui/app.py BINDINGS and
# soap/tui/widgets.py): j/k or arrows move · h/l focus panes · `/` search ·
# `t` edit tags · `q` quit.

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
# Sync on the sidebar's "BROWSE" border title — width-invariant, unlike the list
# title "All documents · N", which truncates to "All do…" on a narrow pane.
Wait+Screen /BROWSE/
Sleep 1200ms

# --- tour the sidebar first: the LIBRARY filters, then the TAGS below ------
# `h` focuses the sidebar; `j` walks down through the filter views (All, Inbox,
# To read, Reading) and on into the TAGS list, so the viewer sees the tags exist.
# The document list re-filters live as the highlight moves onto each row.
Type "h"
Sleep 800ms
Type "j"
Sleep 500ms
Type "j"
Sleep 500ms
Type "j"
Sleep 500ms
Type "j"
Sleep 700ms
Type "j"
Sleep 700ms
Type "j"
Sleep 700ms
Type "j"
Sleep 700ms
Type "j"
Sleep 1100ms
# walk back up to "All" (the cursor clamps at the top filter), then focus the list.
Type "k"
Sleep 200ms
Type "k"
Sleep 200ms
Type "k"
Sleep 200ms
Type "k"
Sleep 200ms
Type "k"
Sleep 200ms
Type "k"
Sleep 200ms
Type "k"
Sleep 200ms
Type "k"
Sleep 800ms
Type "l"
Sleep 800ms

# --- scroll through the document list; the detail pane tracks the selection -
Down
Sleep 600ms
Down
Sleep 600ms
Down
Sleep 800ms
Up
Sleep 800ms

# --- move focus across the panes (list → detail → back) --------------------
Type "l"
Sleep 1000ms
Type "h"
Sleep 800ms

# --- `/` search: filter the library live, then clear it --------------------
Type "/"
Sleep 400ms
Type "transformer"
# The list filters on every keystroke; a beat lets it settle.
Sleep 1400ms
# Escape (from the search box) clears the query and refocuses the list.
Escape
Sleep 900ms

# --- organize: add a tag to the selected document --------------------------
# `t` opens the tag editor; type a tag, Enter adds it as a chip, Ctrl+S saves
# (rewrites info.yaml + refreshes the sidebar tag counts). Match the editor's own
# "type a tag" placeholder — the sidebar "TAGS" header is also on screen now.
Type "t"
Wait+Screen /type a tag/
Sleep 500ms
Type "ml"
Enter
Sleep 900ms
Ctrl+S
# Longer pause so the new #ml chip and sidebar tag count clearly land.
Sleep 2500ms

# --- ...and remove it again ------------------------------------------------
# `t` again; Backspace on an empty field drops the last chip, Ctrl+S saves.
Type "t"
Wait+Screen /type a tag/
Sleep 500ms
Backspace
Sleep 900ms
Ctrl+S
# Longer pause so the viewer sees the tag is gone again.
Sleep 2500ms

# --- quit -------------------------------------------------------------------
Type "q"
Sleep 1200ms
