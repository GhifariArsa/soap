# soap — add-flow demo tape (charmbracelet/vhs).
#
# Regenerates docs/add.gif: adding a book with `soap add`, fully offline, then
# browsing the result in the TUI. Do NOT run this tape directly — it expects
# HOME/SOAP_DIR to point at a seeded temp library (with a sample.pdf in HOME)
# and `soap` on PATH. Drive it via:
#
#     uv run python scripts/demo.py
#
# `--no-fetch` keeps it fully offline: no Crossref/arXiv lookup, no network.
# Metadata comes entirely from the flags.

Output docs/add.gif

Require soap

# --- terminal look (matches demo.tape) -------------------------------------
Set Shell "bash"
Set FontSize 16
Set Width 1200
Set Height 720
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 55ms
Set WaitTimeout 30s

# --- hidden setup: start in HOME (temp) with a clean prompt -----------------
Hide
Type "cd ~"
Enter
Type "clear"
Enter
Sleep 500ms
Show

# --- add a book, offline; metadata supplied via flags ----------------------
# Backtick-quoted so the inner double quotes reach the shell literally.
Type `soap add sample.pdf --no-fetch --type book --title "The Elements of Statistical Learning" --author "Hastie, Trevor" --author "Tibshirani, Robert"`
Sleep 1000ms
Enter
# With always_review on (the shipped default), the add lands in the review
# queue — the summary row reads "needs review".
Wait+Screen /needs review/
Sleep 2500ms

# --- browse the freshly added book in the TUI ------------------------------
Type "soap"
Enter
Wait+Screen /All documents/
Sleep 1200ms
# Jump to it with `/` search.
Type "/"
Sleep 500ms
Type "Elements"
Sleep 1500ms
Enter
Sleep 2000ms

# --- quit -------------------------------------------------------------------
Type "q"
Sleep 1200ms
