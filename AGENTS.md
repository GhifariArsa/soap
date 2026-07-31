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
  to a temp file, verifies `%PDF`/content-type, caps size, and hands the temp to
  `_add_body` which stores+attaches it via the normal local-file path (sha256,
  `attach_file`); the temp is always cleaned up in `_add_inner`'s finally. Download
  is gated `fetch and not dry_run`; a failed/paywalled download degrades to
  metadata-only with a warning — never crashes. The PDF is still never parsed (the
  abstract comes from the metadata API, not the file).
- **CLI and TUI share the review core.** The interactive walk lives in
  `soap/library.py:review_inbox` (IO fully injected: `render`/`ask_action`/
  `confirm_delete`/`report`/`prompt_field`) so it is unit-tested without a terminal
  (`tests/test_inbox_review.py`). CLI (`soap/cli/inbox.py`) and TUI
  (`soap/tui/review.py`) are thin shims — keep their review semantics consistent.
- **Inline correction walk:** `soap/library.py:prompt_fields` walks the core fields
  (`CORE_REVIEW_FIELDS`: title/authors/year/type/venue), prefilled, Enter-keeps /
  type-overrides. It **pins the citekey/id** on a review-edit (never renames the
  folder); only a brand-new `add()` derives a fresh key. Shared by the CLI `[c]orrect`
  action, the TUI review form, and `soap add --confirm`.
- **`always_review: true`** is the shipped default (`soap/cli/init.py`), so the review
  queue is the primary add path — weight review UX accordingly.
- **TUI is view-only over theme tokens.** The "Aqua Slate" redesign lives entirely in
  the view layer: `soap/tui/themes.py` (palettes), `app.tcss` (structure/borders),
  `soap/tui/widgets.py` + `widgets_detail.py` (list `DataTable` + detail), and
  `soap/tui/review.py`. Widgets emit theme slots (`$primary`/`$accent`/`$success`/…)
  and shared markup helpers (`soap/tui/_markup.py`: `sep`/`key`/`confidence_meter`),
  **never hardcoded hex** — so a theme change reskins everything. `sep()` also fixes
  Textual's span-boundary whitespace stripping (the old `sourcearxiv`/`movej/k` mash);
  use it for every `label<space>value`. The list feed adds display-only columns to
  `DocumentService.list_documents` (venue/read_status/author summary) — read-only, no
  schema change. Regenerate the reference screenshots with
  `uv run python scripts/shoot_tui.py` (writes SVGs to `docs/screens/`).
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
  from `.github/workflows/ci.yml`, then builds PyApp standalone binaries across 4
  native runners (macOS arm64/x86_64 + Linux x86_64/arm64 — no Windows, no signing),
  publishes to PyPI via **Trusted Publishing** (`pypi` environment, OIDC, no tokens),
  and cuts a GitHub Release. Actions and toolchain/build inputs are pinned in the
  workflows; the publish/release jobs are tag-gated (skipped on dispatch).
  PyApp knobs, the one-time PyPI pending-publisher setup, and the captain-gated
  public steps are documented in `docs/releasing.md`. The frozen TUI is smoke-tested
  under a pty by `scripts/tui_smoke.py`. Homebrew bump is intentionally NOT here
  (separate task).

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
