"""Release workflow behavior at Assay's Dagger/transport boundary."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW_ROOT = ROOT / ".github/workflows"
EXPECTED_NPM_SHA256 = "eb115cbd809762f43cf45212682144581f1f18cefa5208300251886a10d1b3ee"


def _node_environment() -> dict[str, str]:
    environments = tuple((Path.home() / ".nvm/versions/node").glob("v22.*/bin"))
    if not environments:
        return dict(os.environ)
    selected = max(environments, key=lambda path: tuple(map(int, path.parent.name[1:].split("."))))
    return dict(os.environ) | {"PATH": f"{selected}:{os.environ['PATH']}"}


def _workflow(name: str) -> dict[str, object]:
    loader = yaml.BaseLoader((WORKFLOW_ROOT / name).read_text(encoding="utf-8"))
    try:
        document = loader.get_single_data()
    finally:
        loader.dispose()
    assert isinstance(document, dict)
    return document


def _jobs(name: str) -> dict[str, object]:
    jobs = _workflow(name)["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _steps(job: object) -> list[dict[str, object]]:
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    assert all(isinstance(step, dict) for step in steps)
    return steps


def _uses(job: object) -> tuple[str, ...]:
    return tuple(str(step["uses"]) for step in _steps(job) if "uses" in step)


def test_should_keep_action_pins_bound_to_reviewed_versions() -> None:
    # Given
    source = "\n".join(path.read_text(encoding="utf-8") for path in WORKFLOW_ROOT.glob("*.yml"))

    # When / Then
    expected = {
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2",
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0",
        "dagger/dagger-for-github@496f1b3d8b0d823834c13e67cf8a8e08ca3b9602 # v8.4.0",
        "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2",
    }
    assert all(item in source for item in expected)


def test_should_make_every_non_pypi_ci_or_cd_step_a_dagger_transport() -> None:
    # Given
    allowed = {
        "actions/checkout",
        "actions/upload-artifact",
        "actions/download-artifact",
        "dagger/dagger-for-github",
        "pypa/gh-action-pypi-publish",
    }

    # When
    used = {
        reference.partition("@")[0]
        for name in ("dagger.yml", "security-audit.yml", "release-candidate.yml", "publish.yml")
        for job in _jobs(name).values()
        for reference in _uses(job)
    }

    # Then
    assert used == allowed


def test_should_publish_pypi_only_after_dagger_validates_the_exact_candidate() -> None:
    # Given
    job = _jobs("publish.yml")["publish-python"]

    # When
    steps = _steps(job)
    actions = [str(step.get("uses", "")).partition("@")[0] for step in steps]

    # Then
    assert actions == [
        "actions/download-artifact",
        "dagger/dagger-for-github",
        "pypa/gh-action-pypi-publish",
    ]
    assert steps[1]["id"] == "plan"
    assert steps[2]["if"] == "steps.plan.outputs.output == 'true'"
    assert steps[2]["with"] == {
        "packages-dir": "candidate/release/python",
        "attestations": "true",
        "print-hash": "true",
    }


def test_should_publish_npm_only_inside_the_source_free_dagger_function() -> None:
    # Given
    job = _jobs("publish.yml")["publish-npm"]

    # When
    steps = _steps(job)
    dagger = steps[-1]
    arguments = str(dagger["with"])

    # Then
    assert len(steps) == 2
    assert "publish-npm --candidate=candidate" in arguments
    assert '--expected-sha="$HEAD_SHA"' in arguments
    assert dagger["env"] == {"HEAD_SHA": "${{ github.event.workflow_run.head_sha }}"}
    assert "--oidc-url=env:ACTIONS_ID_TOKEN_REQUEST_URL" in arguments
    assert "--oidc-token=env:ACTIONS_ID_TOKEN_REQUEST_TOKEN" in arguments


def test_should_remove_tag_push_and_completed_recovery_authority() -> None:
    # Given
    publish = (WORKFLOW_ROOT / "publish.yml").read_text(encoding="utf-8")

    # When / Then
    assert "workflow_run:" in publish
    assert "tags:" not in publish
    assert "recover_dev2" not in publish
    assert "35c1fe926c39dfd533b9b7f297abd63eac77c6e6" not in publish
    assert "publish-github" not in publish


def test_should_build_the_same_three_reviewed_artifacts_locally(tmp_path: Path) -> None:
    # Given
    destination = tmp_path / "release"

    # When
    result = subprocess.run(
        ["bash", "scripts/build_release_artifacts.sh", destination],
        cwd=ROOT,
        env=_node_environment(),
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert result.returncode == 0, result.stderr
    wheel = tuple((destination / "python").glob("*.whl"))
    sdist = tuple((destination / "python").glob("*.tar.gz"))
    npm = tuple((destination / "npm").glob("*.tgz"))
    assert tuple(map(len, (wheel, sdist, npm))) == (1, 1, 1)
    assert hashlib.sha256(npm[0].read_bytes()).hexdigest() == EXPECTED_NPM_SHA256


def test_should_keep_local_security_and_candidate_commands_dagger_owned() -> None:
    # Given
    tasks = _workflow("release-candidate.yml")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    docs = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    # When / Then
    assert tasks
    assert "uv run poe release-candidate" in docs
    assert "uv run poe audit" in docs
    assert 'release-candidate = "bash scripts/verify_release_candidate.sh"' in project
    assert 'audit = ["audit-python", "audit-typescript"]' in project


def test_should_scope_the_historical_secret_exception_to_one_public_key() -> None:
    # Given / When
    lines = (ROOT / ".gitleaksignore").read_text(encoding="utf-8").splitlines()
    entries = tuple(line for line in lines if line and not line.startswith("#"))

    # Then
    assert entries == ("8e309ff7a0bbeca01c0d283cbe138adbd6641704:ts/README.md:generic-api-key:43",)


def test_should_keep_current_release_identity_without_creating_a_new_release() -> None:
    # Given / When
    python = (ROOT / "src/assay/_version.py").read_text(encoding="utf-8")
    npm = (ROOT / "ts/package.json").read_text(encoding="utf-8")

    # Then
    assert '__version__ = "0.5.0.dev3"' in python
    assert '"version": "0.5.0-dev.3"' in npm
