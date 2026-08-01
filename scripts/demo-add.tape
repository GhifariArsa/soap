# soap — add/ingest demo tape (charmbracelet/vhs).
#
# Regenerates docs/add.gif: the full ingest story — three ways to add, then
# working the review queue from the CLI and finishing in the TUI. Do NOT run
# this tape directly — it expects HOME/SOAP_DIR to point at a fresh temp library
# (with placeholder PDFs under ~/Downloads) and `soap` on PATH. Drive it via:
#
#     uv run python scripts/demo.py
#
# NETWORK: unlike the browse tape, this one is NOT fully offline. Adds #1 (ISBN
# → Open Library) and #2 (arXiv link) hit the live network to show real fetched
# metadata; add #3 is offline (--no-fetch). See docs/releasing.md.
#
# Review keys are soap's real ones: the CLI walk (soap/cli/inbox.py) reads one
# line per action — [a]ccept/[c]orrect/[s]kip/[q]uit; the TUI review screen
# (soap/tui/review.py, opened with `r`) uses a=file, s=skip, q=done.

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
# Live fetches (Open Library, arXiv + PDF) can be slower than a local render.
Set WaitTimeout 45s

# --- hidden setup: start in HOME (temp) with a clean prompt -----------------
Hide
Type "cd ~"
Enter
Type "clear"
Enter
Sleep 500ms
Show

# --- 1) local file, book metadata pulled from its ISBN (live: Open Library) -
# `~/` expands (HOME is the throwaway dir); the rest is quoted for the spaces.
Type 'soap add ~/"Downloads/TinyML with TensorFlow Lite.pdf" --isbn 1492052043'
Sleep 800ms
Enter
# Wait on the resulting citekey — it appears only after the add completes, and
# (unlike "TinyML" or "needs review") isn't in the typed command or a prior add,
# so each add fully finishes before the next is typed.
Wait+Screen /warden2020tinyml/
# Generous settle so the summary line + prompt fully return before the next
# command types (no output/command overlap even on a slow network).
Sleep 3000ms

# --- 2) straight from an arXiv link (live: metadata + best-effort PDF) ------
Type "soap add https://arxiv.org/abs/2509.26507"
Sleep 800ms
Enter
Wait+Screen /Dragon Hatchling/
# Longest settle — this add also downloads the PDF, so its tail output lands a
# beat after the result row.
Sleep 4000ms

# --- 3) fully manual, offline: every field typed in (--no-fetch) -----------
Type 'soap add ~/"Downloads/Functional Programming in Scala.pdf" --no-fetch --type book --title "Functional Programming in Scala" --author "Paul Chiusano" --author "Rúnar Bjarnason" --year 2014 --isbn 9781617290657 --tag scala'
Sleep 800ms
Enter
Wait+Screen /chiusano2014functional/
Sleep 3000ms

# --- 4) work the inbox from the CLI: accept the first, skip the other two ---
Type "soap inbox review"
Enter
# The first item renders its fields (a plain "citekey" label — the menu itself
# reads "[a]ccept · [c]orrect …", whose bracketed keys wouldn't match a word).
Wait+Screen /citekey/
Sleep 1600ms
Type "a"
Enter
Sleep 1600ms
Type "s"
Enter
Sleep 1300ms
Type "s"
Enter
Sleep 2000ms

# --- 5) finish the rest in the TUI review screen (`r`) ----------------------
Type "soap"
Enter
# Sync on the sidebar's "BROWSE" border title — width-invariant, unlike the list
# title "All documents · N", which truncates to "All do…" on a narrow pane.
Wait+Screen /BROWSE/
Sleep 1500ms
Type "r"
Wait+Screen /reviewing document/
Sleep 1500ms
Type "a"
Sleep 1600ms
# Filing the last item empties the queue and auto-closes the review screen.
Type "a"
Sleep 2000ms
Type "q"
Sleep 1200ms
