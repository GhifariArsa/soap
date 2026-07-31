# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Architecture invariants

- **Disk is the source of truth.** Every mutation rewrites `documents/<id>/info.yaml`
  first, then re-syncs the SQLite index (rebuildable). Write through the helpers in
  `soap/library.py` (`save_document`, `set_review_status`, `edit_document`,
  `delete_document`), never the DB directly.
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
  `workflow_dispatch` for a build-only dry run). It builds PyApp standalone binaries
  across 4 native runners (macOS arm64/x86_64 + Linux x86_64/arm64 — no Windows, no
  signing), publishes to PyPI via **Trusted Publishing** (`pypi` environment, OIDC,
  no tokens), and cuts a GitHub Release. `cargo install pyapp --locked` is mandatory.
  The publish/release jobs are tag-gated (skipped on dispatch). PyApp knobs, the
  one-time PyPI pending-publisher setup, and the captain-gated public steps are
  documented in `docs/releasing.md`. The frozen TUI is smoke-tested under a pty by
  `scripts/tui_smoke.py`. Homebrew bump is intentionally NOT here (separate task).

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
