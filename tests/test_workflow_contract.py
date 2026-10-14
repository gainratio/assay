"""Release workflow behavior at Assay's Dagger/transport boundary."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW_ROOT = ROOT / ".github/workflows"
PINNED_USE = re.compile(
    r"\s*(?:- )?uses: [\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40} # v\d+\.\d+\.\d+"
)
EXPECTED_NPM_SHA256 = "60ce4fe119a33b25335626e5fbd26e2ab36a9c11b015dabb350f1a796205036d"


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


def test_should_pin_every_action_to_an_immutable_sha_with_its_exact_version() -> None:
    # Given: the property, not a copy of the SHAs. Dependabot moves a SHA and its comment
    # together, so a copied SHA list only turned every Actions bump red.
    lines = [
        line
        for path in sorted(WORKFLOW_ROOT.glob("*.yml"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if "uses:" in line
    ]

    # When
    unpinned = [line.strip() for line in lines if not PINNED_USE.fullmatch(line)]

    # Then: a tag or branch ref is mutable; a floating-major comment hides what was reviewed
    assert lines
    assert unpinned == []


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
        "dagger/dagger-for-github",
        "actions/download-artifact",
        "dagger/dagger-for-github",
        "pypa/gh-action-pypi-publish",
    ]
    assert steps[2]["id"] == "plan"
    assert steps[3]["if"] == "steps.plan.outputs.output == 'true'"
    assert steps[3]["with"] == {
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
    assert len(steps) == 3
    assert "publish-npm --candidate=candidate" in arguments
    assert '--expected-sha="$HEAD_SHA"' in arguments
    assert dagger["env"] == {"HEAD_SHA": "${{ github.event.workflow_run.head_sha }}"}
    assert "--oidc-url=env:ACTIONS_ID_TOKEN_REQUEST_URL" in arguments
    assert "--oidc-token=env:ACTIONS_ID_TOKEN_REQUEST_TOKEN" in arguments


def test_should_hand_npm_the_lineage_proven_github_actions_context() -> None:
    # Given
    steps = _steps(_jobs("publish.yml")["publish-npm"])

    # When
    lineage = steps[0]["with"]
    publisher = steps[-1]["with"]
    assert isinstance(lineage, dict)
    assert isinstance(publisher, dict)

    # Then
    assert lineage["args"].startswith("release-provenance --github-token=env:GH_TOKEN ")
    assert lineage["args"].endswith(" export --path=github-context.json")
    assert publisher["args"].endswith(" --github-context=github-context.json")


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
    assert '__version__ = "0.5.0.dev6"' in python
    assert '"version": "0.5.0-dev.6"' in npm
