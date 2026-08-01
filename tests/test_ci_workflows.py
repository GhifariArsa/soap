"""Regression tests for CI validation and release safety gates."""

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTION_REF = re.compile(r"^\s*(?:- )?uses: (?P<action>[\w./-]+)@(?P<ref>\S+)", re.MULTILINE)
SHA = re.compile(r"^[0-9a-f]{40}$")


def _workflow(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def test_ci_has_required_validation_jobs() -> None:
    workflow = _workflow("ci.yml")

    assert set(workflow["on"]) == {"push", "pull_request", "workflow_call"}
    assert {
        "tests",
        "lint",
        "packaging",
        "security",
        "workflow",
    } <= set(workflow["jobs"])
    assert workflow["permissions"] == {"contents": "read"}


def test_release_waits_for_reusable_validation() -> None:
    workflow = _workflow("release.yml")
    jobs = workflow["jobs"]

    assert jobs["validate"]["uses"] == "./.github/workflows/ci.yml"
    assert jobs["build-binaries"]["needs"] == "validate"
    assert set(jobs["publish-pypi"]["needs"]) == {"validate", "build-binaries"}
    assert set(jobs["github-release"]["needs"]) == {
        "validate",
        "build-binaries",
        "publish-pypi",
    }

    # Keep the three native targets and the manual build-only dry run intact.
    matrix = jobs["build-binaries"]["strategy"]["matrix"]["include"]
    targets = {item["target"] for item in matrix}
    assert targets == {
        "macos-arm64",
        "linux-x86_64",
        "linux-arm64",
    }
    runners = {item["target"]: item["os"] for item in matrix}
    assert runners["macos-arm64"] == "macos-14"
    assert runners["linux-x86_64"] == "ubuntu-24.04"
    assert runners["linux-arm64"] == "ubuntu-24.04-arm"
    pyapp_step = next(
        step
        for step in jobs["build-binaries"]["steps"]
        if step.get("name") == "Build standalone binary (PyApp)"
    )
    assert pyapp_step["env"]["MACOSX_DEPLOYMENT_TARGET"] == "13.0"
    assert jobs["publish-pypi"]["if"] == "startsWith(github.ref, 'refs/tags/v')"
    assert jobs["github-release"]["if"] == "startsWith(github.ref, 'refs/tags/v')"


def test_workflow_actions_and_release_build_inputs_are_pinned() -> None:
    texts = [
        (WORKFLOWS / "ci.yml").read_text(),
        (WORKFLOWS / "release.yml").read_text(),
    ]
    refs = [match for text in texts for match in ACTION_REF.finditer(text)]

    assert refs
    assert all(SHA.fullmatch(match["ref"]) for match in refs)
    text = texts[1]
    assert 'version: "0.10.0"' in text
    assert 'python-version: "3.14.2"' in text
    assert 'go-version: "1.24.5"' in texts[0]
    assert 'toolchain: "1.87.0"' in text
    assert "cargo install pyapp --version 0.29.0 --locked" in text
    assert 'PYAPP_PYTHON_VERSION: "3.14"' in text


def test_pypi_keeps_oidc_least_privilege_and_environment_gate() -> None:
    publish = _workflow("release.yml")["jobs"]["publish-pypi"]

    assert publish["environment"] == "pypi"
    assert publish["permissions"] == {"id-token": "write"}
    assert "contents" not in publish["permissions"]


def test_homebrew_bump_is_tag_gated_and_token_scoped() -> None:
    workflow = _workflow("release.yml")
    bump = workflow["jobs"]["homebrew-bump"]

    # Runs only after the release exists, and only on a real tag push.
    assert set(bump["needs"]) == {
        "validate",
        "build-binaries",
        "publish-pypi",
        "github-release",
    }
    assert bump["if"] == "startsWith(github.ref, 'refs/tags/v')"

    # Cross-repo push auth comes only from the secret; nothing is hardcoded.
    text = (WORKFLOWS / "release.yml").read_text()
    assert "secrets.HOMEBREW_TAP_TOKEN" in text
    assert bump["permissions"] == {"contents": "read"}
    checkout = next(step for step in bump["steps"] if step.get("with", {}).get("path") == "tap")
    assert checkout["with"]["repository"] == "GhifariArsa/homebrew-soap"
    assert checkout["with"]["token"] == "${{ secrets.HOMEBREW_TAP_TOKEN }}"

    # The committed bump script the job invokes must exist.
    assert (ROOT / "scripts" / "bump_homebrew_formula.py").is_file()
