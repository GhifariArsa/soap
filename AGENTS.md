# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Architecture invariants

- **Disk is the source of truth.** Every mutation rewrites `documents/<id>/info.yaml`
  first, then re-syncs the SQLite index (rebuildable). Write through the helpers in
  `soap/library.py` (`save_document`, `set_review_status`, `set_read_status`,
  `edit_document`, `delete_document`), never the DB directly. Those helpers validate
  document IDs and file references at the library boundary, and metadata YAML
  replacement is atomic; `resolve_file_ref_path` is also the TUI open boundary.
- **Library paths are owner-private; shell exports are quoted.** Anything a
  library owns is forced to `0700`/`0600` after it is created or copied via
  `soap/permissions.py:make_private` (source files keep their original mode
  only until they land inside the library) — never rely on umask or an atomic
  rename to set the mode. Generated shell startup code must go through
  `soap/shell.py:export_line`, which shell-quotes `SOAP_DIR` per shell
  (POSIX/`shlex` for bash/zsh/unknown-fallback, single-quote escaping for
  fish) and rejects NUL bytes and the reserved `# >>> soap >>>` block markers;
  never `f"...{path}..."` a path straight into a config line. Regression
  coverage: `tests/test_security.py`.
- **Link adds download the PDF.** A URL/bare-arXiv-id add resolves metadata *and*
  best-effort downloads the paper's PDF (`soap/ingest/download.py:download_pdf`,
  driven from `soap/ingest/url.py:resolve_url(download=...)`): arXiv's canonical
  `/pdf/<id>.pdf`, a direct `.pdf` URL, or an open-access PDF a DOI exposes
  (Crossref `link`, else the doi.org redirect if it lands on a real PDF). It streams
  to a temp file and keeps it only if the first bytes are the `%PDF` magic marker —
  content-type is advisory, so even `application/pdf` must start with `%PDF` — caps
  size, and hands the temp to `_add_body` which stores+attaches it via the normal
  local-file path (sha256, `attach_file`); the temp is always cleaned up in
  `_add_inner`'s finally. Download is gated `fetch and not dry_run`; a failed/paywalled
  download degrades to metadata-only with a warning — never crashes. The PDF is still
  never parsed (the abstract comes from the metadata API, not the file).
- **Outbound HTTP is SSRF-hardened.** Both metadata fetches (`soap/ingest/fetch.py`)
  and the PDF download (`soap/ingest/download.py`) follow redirects manually one hop at
  a time and route every hop — including the explicit user URL — through
  `soap/ingest/network.py:validate_url`, which rejects non-HTTP(S) schemes, embedded
  credentials, and any host that is or resolves to a loopback/private/link-local/reserved
  address (`MAX_REDIRECTS` cap). Response bodies are size-bounded before a parser sees
  them (`MAX_METADATA_BYTES` for metadata, `MAX_PDF_BYTES` for the PDF). Parsers
  (`parse_crossref`/`parse_arxiv`/`parse_openlibrary`) validate payload shape at the
  boundary and return `None` on malformed/wrong-type provider data, so `add` degrades
  with a warning instead of crashing. Regression-tested in `tests/test_ingest.py` — keep
  these seams injectable and never weaken the address checks. `_bounded_get`
  reconstructs a fresh `httpx.Response` from the *decoded* streamed body, so it
  first strips the provider's now-inaccurate `Content-Encoding`/`Content-Length`
  headers — leaving them makes httpx re-run the gzip decoder on plain bytes and
  raise `DecodingError`, silently turning every gzipped metadata lookup into a
  miss (Open Library/Crossref gzip by default).
- **CLI and TUI share the review core.** The interactive walk lives in
  `soap/library.py:review_inbox` (IO fully injected: `render`/`ask_action`/
  `confirm_delete`/`report`/`prompt_field`) so it is unit-tested without a terminal
  (`tests/test_inbox_review.py`). CLI (`soap/cli/inbox.py`) and TUI
  (`soap/tui/review.py`) are thin shims — keep their review semantics consistent.
- **Inline correction walk:** `soap/library.py:prompt_fields` walks the core fields
  (`CORE_REVIEW_FIELDS`: title/authors/year/type/venue), prefilled, Enter-keeps /
  type-overrides. It **pins the citekey/id** on a review-edit (never renames the
  folder); only a brand-new `add()` derives a fresh key. Shared by the CLI `[c]orrect`
  action, the TUI review form, the TUI browser's `E` in-app edit form
  (`soap/tui/edit.py`), and `soap add --confirm`.
- **Browser edit/delete reuse the review core.** The main browser
  (`soap/tui/app.py`) exposes `E` (in-app core-field edit → `EditScreen` in
  `soap/tui/edit.py`, which drives `prompt_fields`+`save_document`, id pinned) and
  `d` (delete → `ConfirmDeleteScreen` in `soap/tui/confirm.py` → `delete_document`).
  `e` stays the full-YAML `$EDITOR` power option. Both go through the library helpers
  (never the DB); delete is confirm-gated. Pilot coverage:
  `tests/test_tui_browser_edit_delete.py`.
- **BibTeX export is read-only and pure.** `soap/bibtex.py` serializes
  `Document`s to a deterministic `.bib` (entries ordered by citekey, entry key =
  `id`, `type`→BibTeX type via `_TYPE_MAP` fallback `misc`, `venue`→`journal` or
  `booktitle` by type, char-by-char LaTeX escaping — never sequential
  `str.replace`, which re-escapes inserted braces). `serialize_documents` returns
  a `BibtexResult` partitioning inputs into `exported_ids`/`skipped_ids` so
  callers report drops instead of silently losing records. The TUI wires it via
  `space` (mark rows on `DocumentList.marked`) + `x` (`soap/tui/export.py`:
  `ExportScopeScreen`→`ExportDestinationScreen`; also in the `^p` palette). The
  app hydrates chosen docs through `DocumentService.get_document` and writes the
  file — it never mutates the library or hits the network. Coverage:
  `tests/test_bibtex.py` + `tests/test_tui_export.py`.
- **`always_review: true`** is the shipped default (`soap/cli/init.py`), so the review
  queue is the primary add path — weight review UX accordingly.
- **TUI is view-only over theme tokens.** The "Aqua Slate" redesign lives entirely in
  the view layer: `soap/tui/themes.py` (palettes), `app.tcss` (structure/borders),
  `soap/tui/widgets.py` + `widgets_detail.py` (list `DataTable` + detail), and
  `soap/tui/review.py`. Widgets emit theme slots (`$primary`/`$accent`/`$success`/…)
  and shared markup helpers (`soap/tui/_markup.py`: `sep`/`key`/`confidence_meter`),
  **never hardcoded hex** — so a theme change reskins everything. The app root
  (`App, Screen` in `app.tcss`) is `background: ansi_default` (terminal-default
  background = SGR 49) so the terminal's own, possibly transparent, background
  shows through — like lazygit/Claude Code. This is terminal-background
  passthrough, **not** emulator opacity control soap can set. Two pieces are load-
  bearing and must stay together: `ansi_default` (NOT `transparent` — `App.render`
  paints a `Blank` of the root color with nothing beneath it, so plain
  `transparent` flattens to solid black), and `ansi_color=True` on `SoapApp`
  (`soap/tui/app.py`) which keeps Textual's ANSI→truecolor filter off so
  `ansi_default` survives to the terminal instead of being re-flattened to an
  opaque RGB fill. Truecolor theme colors (panes/borders/text) are unaffected;
  the opaque pane/card rules keep the UI readable and always seat text on an
  opaque surface. Don't reintroduce an opaque or `transparent` `App`/`Screen`
  background, and don't drop `ansi_color=True` (regression: the
  `test_app_root_is_terminal_default_but_theme_surfaces_are_not` PTY-verified
  invariant in `tests/test_themes.py`). `sep()` also fixes
  Textual's span-boundary whitespace stripping (the old `sourcearxiv`/`movej/k` mash);
  use it for every `label<space>value`. The list feed adds display-only columns to
  `DocumentService.list_documents` (venue/read_status/author summary) — read-only, no
  schema change. Regenerate the reference screenshots with
  `uv run python scripts/shoot_tui.py` (writes SVGs to `docs/screens/`).
- **README demo GIFs are generated, not hand-recorded.** `docs/demo.gif` (TUI
  browse/organize tour) and `docs/add.gif` (ingest + review story) are
  regenerated by `uv run python scripts/demo.py`, which renders each tape
  (`scripts/demo.tape` + `scripts/demo-add.tape`) from its own throwaway
  `HOME`/`SOAP_DIR` (browse: seeded invented papers, same idea as `shoot_tui.py`;
  add: fresh library + placeholder PDFs under a throwaway `~/Downloads`) driving
  the real `soap` binary via VHS (`charmbracelet/vhs`). Needs `vhs`+`ttyd`+`ffmpeg`
  on PATH. Leaks nothing real. The browse tape is fully offline; the **add tape
  hits the live network** (Open Library ISBN + arXiv) for two of its three adds —
  so re-rendering `add.gif` needs those services up. Sync tapes on width-invariant
  on-screen text (e.g. `BROWSE`, a citekey), never the truncation-prone
  `All documents · N` title. Details: `docs/releasing.md` § Regenerating the demo GIFs.
- **Tags are first-class in the TUI.** Edit via the `t` key → `soap/tui/tags.py`
  `TagEditScreen` (keyboard-first: enter/comma adds, `tab` completes the top
  suggestion from `DocumentService.tag_counts()`, empty-`backspace` drops the last
  chip, `^s` saves, `esc` cancels). It persists the whole document through
  `save_document` (rewrites `info.yaml` + reindex — never raw DB) so chips and the
  sidebar tag counts refresh live. Filtering is the existing sidebar
  `filter_kind="tag"` path (`app.py:_sidebar_moved` → `list_documents`); the list
  border-title shows `# <tag>` (plus `· /<search>` when a `/` search is ANDed on).
- **Themes are user-extensible** (`soap/tui/themes.py`). `BUNDLED_THEMES` ships
  aqua-slate (default) + one-dark + catppuccin-mocha; `load_user_themes` discovers
  `$SOAP_DIR/themes/*.yaml` (YAML, matching `soap/config.py`) and degrades gracefully
  on a broken file (skip + warn, never crash). The startup theme is the `theme:` key
  in `config.yaml`; `SoapApp` persists any runtime switch back via
  `soap.config.save_theme`. Format + example: `docs/themes.md`, `docs/example-theme.yaml`.
- **Testing:** `uv run pytest`. TUI is covered with Textual's pilot via `asyncio.run`
  (`tests/test_tui_review.py`, `tests/test_themes.py`) — no pytest-asyncio plugin.

- **Packaging:** PyPI dist name is **`soap-tui`** (plain `soap` is taken) but the
  installed command stays **`soap`** via `[project.scripts]` — never conflate them.
  Version is **dynamic via hatch-vcs** from `v*` git tags, written to the gitignored
  `soap/_version.py` at build time; no tag → a `0.1.devN+...` version (expected).
  `soap --version` (`soap/main.py`) resolves `importlib.metadata.version("soap-tui")`
  then falls back to `soap/_version.py` then `"0.0.0+unknown"`. Build/verify with
  `uv build` (emits `soap_tui-*` wheel + sdist).

- **Release CI:** `.github/workflows/release.yml` fires on a `v*` tag push (or
  `workflow_dispatch` for a build-only dry run). It first reuses the required checks
  from `.github/workflows/ci.yml`, then builds PyApp standalone binaries across 3
  native runners (macOS arm64 + Linux x86_64/arm64 — no Windows, no signing),
  publishes to PyPI via **Trusted Publishing** (`pypi` environment, OIDC, no tokens),
  and cuts a GitHub Release. Actions and toolchain/build inputs are pinned in the
  workflows; the publish/release jobs are tag-gated (skipped on dispatch).
  PyApp knobs, the one-time PyPI pending-publisher setup, and the captain-gated
  public steps are documented in `docs/releasing.md`. The frozen TUI is smoke-tested
  under a pty by `scripts/tui_smoke.py`.

- **Homebrew distribution:** the public tap is a SEPARATE repo,
  `GhifariArsa/homebrew-soap` (`brew install GhifariArsa/soap/soap-tui`). Its
  `Formula/soap-tui.rb` is a **binary** formula — downloads the prebuilt release
  binaries, no Python/source build — with per-platform `on_macos`/`on_linux` +
  `on_arm`/`on_intel` `url`/`sha256` pairs and NO `version` stanza (Homebrew
  scans it from the URL; adding one fails `brew audit`). There is deliberately no
  Intel-macOS binary, so the Intel-mac branch `odie`s. The `homebrew-bump` job in
  `release.yml` keeps it current: it runs `scripts/bump_homebrew_formula.py`
  (stdlib-only regex rewrite of the three url/sha256 pairs from the release
  `checksums.txt` — NOT `brew bump-formula-pr`, which can't model the multi-block
  binary formula) and pushes to the tap using the `HOMEBREW_TAP_TOKEN` repo secret
  (fine-grained PAT, Contents:rw on the tap; job no-ops if unset). Setup +
  rationale: `docs/releasing.md`; regression coverage in `tests/test_ci_workflows.py`
  + `tests/test_bump_homebrew.py`.

- **Self-update:** `soap self update` is OUR Typer subcommand (PyApp's `self` group is
  off via `PYAPP_SELF_COMMAND=none`), all in `soap/cli/selfupdate.py`, wired in
  `soap/main.py` as `app.add_typer(selfupdate.app, name="self")`. `detect_channel`
  no-ops with an upgrade-command pointer for brew/pipx/uv-tool/pip and only swaps the
  binary channel; the swap resolves the launcher via OS self-exe (NOT `sys.executable`),
  verifies sha256 against the release `checksums.txt`, and `os.replace`s a same-dir temp.
  Windows is a marked future path (`perform_update` refuses). `maybe_nudge` (called from
  `main.py` for non-`self` subcommands) is the 24h-cached, offline-safe startup hint.
  `current_version()` is the single version resolver `soap --version` also uses. Every
  network/IO seam is injectable → `tests/test_selfupdate.py` mocks it all (no real net).

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
