# Releasing soap

Every distribution channel is fed by a **single git tag push**. Tagging `v*`
triggers `.github/workflows/release.yml`, which builds the standalone binaries,
publishes to PyPI, and cuts a public GitHub Release — all from one source of
truth (the tag), so the wheel version, `soap --version`, and the Release never
drift (versioning is `hatch-vcs`, see `CLAUDE.md`).

> ⚠️ **These are captain-gated public steps.** Pushing a `v*` tag publishes to
> PyPI (irreversible — a version can be yanked but never re-uploaded) and creates
> a public GitHub Release. Do not tag until the release is approved.

## Required GitHub release controls

These controls must be configured by a repository administrator; a workflow file
cannot create or enforce GitHub repository settings. Verify them in GitHub before
the first release and after any repository migration:

1. Create an **active tag ruleset** for `v*` in Settings → Rules → Rulesets.
   Restrict tag creation, updates, and deletion to the release-captain team (or
   an equivalent explicitly reviewed bypass). Do not rely on the tag pattern in
   `release.yml` as a substitute for this ruleset.
2. Create the `pypi` environment in Settings → Environments with **required
   reviewers enabled** (the release captain/team) and a deployment branch/tag
   rule allowing only `v*`. The workflow declares `environment: pypi`, so this
   review is reached before its OIDC publish step, but the reviewer list and
   rules are GitHub settings and are not represented by YAML.

The workflow itself additionally enforces the `v*` tag condition, requires the
reusable CI validation workflow and three binary builds to pass before publishing,
and grants the publish job only `id-token: write`. Both layers are required.

`.github/workflows/ci.yml` provides the required checks: `tests`, `lint / format`,
`package / lock`, `security / dependencies`, and `workflow / config`. Configure
those check names as required status checks in the default-branch ruleset; that
branch-protection setting, like the tag and environment controls above, must be
configured in GitHub and cannot be claimed from YAML alone.

## Cutting a release

```sh
git tag v0.1.0
git push origin v0.1.0
```

That's it. The tag drives:

| Job (`release.yml`) | Produces |
|---|---|
| `build-binaries` | `soap-<target>` binaries for macOS arm64 + Linux x86_64/arm64, each with a `.sha256` |
| `publish-pypi` | wheel + sdist uploaded to PyPI (`soap-tui`) via Trusted Publishing |
| `github-release` | a public Release attaching the three binaries + a combined `checksums.txt` |
| `homebrew-bump` | pushes the new version + sha256s to the `homebrew-soap` tap formula (see below) |

Targets are **macOS + Linux only** (no Windows for v1) and binaries are shipped
**unsigned** (macOS notarization deferred — `curl|sh`/direct downloads hit
Gatekeeper quarantine; right-click → Open once, or install via Homebrew/`brew`
which mostly bypasses the prompt).

### Dry run (no publish)

Trigger the workflow manually (Actions → **release** → *Run workflow*, or
`gh workflow run release.yml`). A `workflow_dispatch` run first runs the same
required validation checks, then builds and smoke-tests the binaries on all three
targets, but **skips** `publish-pypi` and `github-release` (both are gated on an
actual `v*` tag). Use it to validate the matrix before committing to a real tag.

## Required one-time public setup (captain-gated)

The GitHub tag ruleset and `pypi` environment review above are mandatory
repository controls, not documentation-only recommendations. Before the **first**
real tag, a PyPI **pending publisher** must be registered
so Trusted Publishing works with no tokens:

1. On <https://pypi.org>, go to **Your account → Publishing** and add a *pending
   publisher* (this also reserves the `soap-tui` name and handles the first
   upload):
   - **PyPI Project Name:** `soap-tui`
   - **Owner:** `GhifariArsa`
   - **Repository name:** `soap`
   - **Workflow name:** `release.yml`
   - **Environment name:** `pypi`
2. In the GitHub repo, create an **Environment** named `pypi` (Settings →
   Environments), add the required release-captain reviewers, and restrict
   deployments to `v*` tags. Confirm the environment's protection rules are
   active; this cannot be enabled from `release.yml`.

No API tokens or username/password are ever stored — the `publish-pypi` job
requests a short-lived OIDC token (`id-token: write`) and
`pypa/gh-action-pypi-publish` exchanges it with PyPI. Sigstore attestations are
on by default.

After the pending publisher's first successful upload it becomes a normal
Trusted Publisher; nothing changes for subsequent tags.

## Homebrew tap auto-bump

soap is distributed via Homebrew through the public tap
[`GhifariArsa/homebrew-soap`](https://github.com/GhifariArsa/homebrew-soap)
(`brew install GhifariArsa/soap/soap-tui`). The tap ships a **binary** formula
that downloads the prebuilt release binaries — it has no Python dependency and
never builds from source. There is deliberately no Intel-macOS binary, so the
formula `odie`s with a clear message on Intel macs (documented in the tap
README).

The `homebrew-bump` job in `release.yml` keeps the formula current: on every
`v*` tag it downloads that release's `checksums.txt` and runs
`scripts/bump_homebrew_formula.py`, which rewrites the three per-platform
`url`/`sha256` pairs in `Formula/soap-tui.rb`, then commits and pushes to the
tap. (We rewrite in place rather than `brew bump-formula-pr` because the formula
is a multi-platform binary formula with three nested `url`/`sha256` blocks plus
the Intel-mac `odie`, which the single-URL bumper does not model.)

### One-time secret setup (captain-gated)

The job needs cross-repo push access to the tap. This is the **only** manual
step, and it must be done before the first auto-bump runs (the initial formula
is pinned by hand, so Homebrew works immediately regardless):

1. Create a **fine-grained personal access token** (GitHub → Settings →
   Developer settings → Fine-grained tokens) scoped to **only** the
   `GhifariArsa/homebrew-soap` repository, with **Repository permissions →
   Contents: Read and write**. No other permissions are needed.
2. On the `soap` repo, add it as a repository secret named exactly
   **`HOMEBREW_TAP_TOKEN`** (Settings → Secrets and variables → Actions → New
   repository secret). The workflow references `secrets.HOMEBREW_TAP_TOKEN`;
   nothing is ever hardcoded or committed.

If the secret is absent, `homebrew-bump` **no-ops with a notice** instead of
failing the release — so a release still succeeds, and Homebrew keeps serving
the last manually-pinned version until the secret is added.

## What is deliberately *not* here

- **macOS code-signing / notarization** — deferred for v1 (no Apple Developer
  account yet); no `codesign`/`notarytool` steps are present.
- **Windows** — out of scope for v1.

## How the pieces fit

- `scripts/tui_smoke.py` — a stdlib-`pty` driver the `build-binaries` job runs to
  confirm the frozen Textual TUI actually launches and clean-exits under a real
  terminal (portable across macOS and Linux runners, unlike `unbuffer`/`script`).
- PyApp build knobs live in the workflow: `PYAPP_DISTRIBUTION_EMBED=1`
  (offline/self-contained), the reviewed Python minor `3.14` (the supported
  PyApp selector), `PYAPP_EXEC_SPEC=soap.main:app`,
  `PYAPP_SELF_COMMAND=none`, and the pinned `cargo install pyapp --version
  0.29.0 --locked`. The macOS build also sets `MACOSX_DEPLOYMENT_TARGET=13.0`
  so native code compiled on the current runner stays back-compatible to
  macOS 13.

## Regenerating the demo GIFs

The README's two demo GIFs — `docs/demo.gif` (the TUI browse/organize tour) and
`docs/add.gif` (the ingest + review story) — are **generated**, not hand-recorded.
The source of truth is a pair of [VHS](https://github.com/charmbracelet/vhs)
tapes plus a Python driver:

- `scripts/demo.tape` / `scripts/demo-add.tape` — the recorded keystrokes,
  using soap's real keybindings (browse: sidebar tour, search, tag add/remove;
  add: three `soap add`s then working the review queue from the CLI and TUI).
- `scripts/demo.py` — renders each GIF from its own throwaway `HOME`/`SOAP_DIR`:
  the browse GIF over a library seeded with invented papers, the add GIF over a
  fresh library plus placeholder PDFs under a throwaway `~/Downloads`. It cleans
  up both temp dirs. Nothing from your real library or home directory appears.

**Prerequisites:** `vhs`, and its `ttyd` + `ffmpeg` dependencies, on `PATH`.
On macOS: `brew install vhs` (pulls in ttyd + ffmpeg).

**Regenerate both GIFs:**

```sh
uv run python scripts/demo.py
```

> **Network:** the browse GIF is fully offline, but the add GIF is **not** — its
> first two adds hit the live network to show real fetched metadata (an ISBN
> lookup via Open Library, and an arXiv link → arXiv metadata + best-effort PDF).
> The third add is offline (`--no-fetch`). If those services are unreachable the
> add GIF can't be regenerated; re-run when they're up. The placeholder book PDFs
> under `~/Downloads` are stub `%PDF` files the driver writes into the throwaway
> HOME — never real copyrighted book files.

Re-runs are deterministic apart from those live lookups (fixed ids/titles for the
seeded set) and write straight over `docs/demo.gif` and `docs/add.gif`. To tweak
a walkthrough, edit the `.tape` files (they are commented); to change the seeded
browse library, edit the `DOCS` list in `scripts/demo.py` (the same fake-library
idea as `scripts/shoot_tui.py`, which shoots the static reference screenshots).
