# Releasing soap

Every distribution channel is fed by a **single git tag push**. Tagging `v*`
triggers `.github/workflows/release.yml`, which builds the standalone binaries,
publishes to PyPI, and cuts a public GitHub Release — all from one source of
truth (the tag), so the wheel version, `soap --version`, and the Release never
drift (versioning is `hatch-vcs`, see `CLAUDE.md`).

> ⚠️ **These are captain-gated public steps.** Pushing a `v*` tag publishes to
> PyPI (irreversible — a version can be yanked but never re-uploaded) and creates
> a public GitHub Release. Do not tag until the release is approved.

## Cutting a release

```sh
git tag v0.1.0
git push origin v0.1.0
```

That's it. The tag drives:

| Job (`release.yml`) | Produces |
|---|---|
| `build-binaries` | `soap-<target>` binaries for macOS arm64/x86_64 + Linux x86_64/arm64, each with a `.sha256` |
| `publish-pypi` | wheel + sdist uploaded to PyPI (`soap-tui`) via Trusted Publishing |
| `github-release` | a public Release attaching the four binaries + a combined `checksums.txt` |

Targets are **macOS + Linux only** (no Windows for v1) and binaries are shipped
**unsigned** (macOS notarization deferred — `curl|sh`/direct downloads hit
Gatekeeper quarantine; right-click → Open once, or install via Homebrew/`brew`
which mostly bypasses the prompt).

### Dry run (no publish)

Trigger the workflow manually (Actions → **release** → *Run workflow*, or
`gh workflow run release.yml`). A `workflow_dispatch` run builds and smoke-tests
the binaries on all four targets but **skips** `publish-pypi` and
`github-release` (both are gated on an actual `v*` tag). Use it to validate the
matrix before committing to a real tag.

## Required one-time public setup (captain-gated)

Before the **first** real tag, a PyPI **pending publisher** must be registered
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
   Environments). Optionally add reviewers/branch protection to it as an extra
   manual gate on publishing.

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
  (offline/self-contained), `PYAPP_PYTHON_VERSION=3.14`,
  `PYAPP_EXEC_SPEC=soap.main:app`, `PYAPP_SELF_COMMAND=none`, and the mandatory
  `cargo install pyapp --locked`.
