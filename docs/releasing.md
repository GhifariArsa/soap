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

## What is deliberately *not* here

- **Homebrew bump** — ships with the separate Homebrew task; it needs the public
  tap repo `GhifariArsa/homebrew-soap` and a push secret, so it lives in its own
  captain-gated step, not this workflow.
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
  0.29.0 --locked`.
