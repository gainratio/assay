"""Executable security contracts for Assay's thin GitHub ingress."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest
import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW_ROOT = ROOT / ".github/workflows"
WORKFLOW_NAMES = {
    "dagger.yml",
    "dependabot-repin.yml",
    "publish.yml",
    "release-candidate.yml",
    "security-audit.yml",
}
PINNED = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return cast(dict[str, object], value)


def _workflow(name: str) -> dict[str, object]:
    loader = yaml.BaseLoader((WORKFLOW_ROOT / name).read_text(encoding="utf-8"))
    try:
        return _mapping(loader.get_single_data())
    finally:
        loader.dispose()


def _jobs(workflow: dict[str, object]) -> dict[str, object]:
    return _mapping(workflow["jobs"])


def _job(workflow: dict[str, object], name: str) -> dict[str, object]:
    return _mapping(_jobs(workflow)[name])


def _steps(job: dict[str, object]) -> list[dict[str, object]]:
    value = job["steps"]
    assert isinstance(value, list)
    return [_mapping(step) for step in value]


def _uses(job: dict[str, object]) -> tuple[str, ...]:
    return tuple(str(step["uses"]) for step in _steps(job) if "uses" in step)


def _with(step: dict[str, object]) -> dict[str, object]:
    return _mapping(step.get("with", {}))


def _action(name: str, job: dict[str, object]) -> dict[str, object]:
    matches = [step for step in _steps(job) if str(step.get("uses", "")).startswith(f"{name}@")]
    assert len(matches) == 1
    return matches[0]


def test_should_ship_only_the_dagger_ingress_and_repin_workflows() -> None:
    # Given / When
    names = {path.name for path in WORKFLOW_ROOT.iterdir() if path.suffix in {".yml", ".yaml"}}

    # Then
    assert names == WORKFLOW_NAMES
    assert all(_jobs(_workflow(name)) for name in names)


def test_should_pin_every_external_action_and_disable_checkout_credentials() -> None:
    # Given
    workflows = tuple(_workflow(name) for name in WORKFLOW_NAMES)

    # When
    jobs = [_mapping(job) for workflow in workflows for job in _jobs(workflow).values()]
    uses = [reference for job in jobs for reference in _uses(job)]
    checkouts = [
        step
        for job in jobs
        for step in _steps(job)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]

    # Then
    assert uses
    assert all(PINNED.fullmatch(reference) for reference in uses)
    assert all(_with(step).get("persist-credentials") == "false" for step in checkouts)


def test_should_make_ci_one_checkout_and_one_dagger_call() -> None:
    # Given
    workflow = _workflow("dagger.yml")
    job = _job(workflow, "dagger")

    # When
    dagger_step = _action("dagger/dagger-for-github", job)

    # Then
    # No workflow_dispatch: a dispatched run is not a pull-request check, so it can never
    # satisfy the required "Dagger" check (observed on the #101 pilot). The re-pin commit
    # instead gets the documented approval-required pull_request run.
    assert set(_mapping(workflow["on"])) == {"push", "pull_request"}
    assert _uses(job) == (
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "dagger/dagger-for-github@27b130bf0f79a7f6fbbbe0fbca6760dc9bb40a77",
    )
    assert all("run" not in step for step in _steps(job))
    assert _with(dagger_step) == {
        "version": "0.21.8",
        "verb": "call",
        "args": "ci --commit-sha=${{ github.sha }}",
    }


def test_should_make_the_schedule_one_complete_dagger_security_call() -> None:
    # Given
    workflow = _workflow("security-audit.yml")
    job = _job(workflow, "security")

    # When
    dagger_step = _action("dagger/dagger-for-github", job)

    # Then
    assert set(_mapping(workflow["on"])) == {"schedule", "workflow_dispatch"}
    assert len(_steps(job)) == 2
    assert all("run" not in step for step in _steps(job))
    assert _with(dagger_step)["args"] == "security --commit-sha=${{ github.sha }}"


#: The release invocation, as `dagger/dagger-for-github` args. The action pastes args into
#: its bash script, so every value is a double-quoted environment variable: bash expands it
#: as one literal word and never parses it as code. No `${{ }}` expression appears.
RELEASE_ARGS = (
    'release-candidate --tag="$TAG" --commit-sha="$GITHUB_SHA" '
    "--github-token=env:GITHUB_TOKEN export --path=candidate"
)

#: Expression roots whose values a dispatcher or event author controls.
ATTACKER_EXPRESSIONS = ("${{ inputs.", "${{ github.event.", "${{ github.head_ref")

#: Dispatch tags an attacker could type; each must reach Dagger as one inert argument.
HOSTILE_TAGS = (
    "v0.5.0",
    "",
    "v0.5.0 --commit-sha=0",
    "v0.5.0;touch pwned",
    "$(touch pwned)",
    "`touch pwned`",
    "v0.5.0\ntouch pwned",
    'v0.5.0" ; touch pwned ; "',
)


def _dagger_invocations() -> list[tuple[str, str]]:
    return [
        (name, str(_with(step).get(key, "")))
        for name in sorted(WORKFLOW_NAMES)
        for job in _jobs(_workflow(name)).values()
        for step in _steps(_mapping(job))
        if str(step.get("uses", "")).startswith("dagger/dagger-for-github@")
        for key in ("args", "call")
    ]


def _expand_action_args(args: str, tag: str, cwd: Path) -> list[str]:
    """Expand args exactly as dagger-for-github's final bash step does, but print them."""
    bash = shutil.which("bash")
    assert bash is not None
    env = {"TAG": tag, "GITHUB_SHA": "a" * 40, "PATH": "/usr/bin:/bin"}
    result = subprocess.run(  # noqa: S603 - fixed bash, test-owned argv
        [bash, "-c", f"printf '%s\\0' {args}"], env=env, cwd=cwd, capture_output=True, check=True
    )
    return result.stdout.decode().split("\0")[:-1]


def test_should_paste_no_attacker_controlled_expression_into_any_dagger_args() -> None:
    # Given every dagger-for-github invocation (the action pastes args/call into bash)
    invocations = _dagger_invocations()

    # Then none carries an expression whose value the dispatcher or event author controls
    assert invocations
    assert [
        (name, text)
        for name, text in invocations
        if any(expression in text for expression in ATTACKER_EXPRESSIONS)
    ] == []


@pytest.mark.parametrize("tag", HOSTILE_TAGS)
def test_should_pass_any_dispatched_tag_to_dagger_as_one_inert_argument(
    tag: str, tmp_path: Path
) -> None:
    # Given the real release args, expanded by bash with a hostile TAG
    argv = _expand_action_args(RELEASE_ARGS, tag, tmp_path)

    # Then the tag is one literal argument and bash ran nothing
    assert argv == [
        "release-candidate",
        f"--tag={tag}",
        "--commit-sha=" + "a" * 40,
        "--github-token=env:GITHUB_TOKEN",
        "export",
        "--path=candidate",
    ]
    assert not (tmp_path / "pwned").exists()


def test_should_build_and_upload_only_a_manual_default_branch_candidate() -> None:
    # Given
    workflow = _workflow("release-candidate.yml")
    job = _job(workflow, "candidate")

    # When
    dagger_step = _action("dagger/dagger-for-github", job)
    upload = _action("actions/upload-artifact", job)

    # Then
    assert set(_mapping(workflow["on"])) == {"workflow_dispatch"}
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert all("run" not in step for step in _steps(job))
    # Inverted contract (was: the args must contain `--tag=${{ inputs.tag }}`, which pasted
    # the dispatch input into dagger-for-github's bash script). The tag now arrives only as
    # the TAG environment variable and the args hold only double-quoted variables.
    assert _with(dagger_step) == {"version": "0.21.8", "verb": "call", "args": RELEASE_ARGS}
    assert _mapping(dagger_step["env"]) == {
        "GITHUB_TOKEN": "${{ github.token }}",
        "TAG": "${{ inputs.tag }}",
    }
    assert _with(upload) == {
        "name": "assay-${{ github.sha }}",
        "path": "candidate/",
        "if-no-files-found": "error",
        "retention-days": "1",
    }


def test_should_keep_both_privileged_publishers_source_free() -> None:
    # Given
    workflow = _workflow("publish.yml")
    publishers = tuple(_mapping(job) for job in _jobs(workflow).values())

    # When
    sources = [str(step.get("uses", "")) for job in publishers for step in _steps(job)]
    commands = [step for job in publishers for step in _steps(job) if "run" in step]

    # Then
    assert set(_mapping(workflow["on"])) == {"workflow_run"}
    assert not any(reference.startswith("actions/checkout@") for reference in sources)
    assert not any("setup-" in reference or "action-setup" in reference for reference in sources)
    assert commands == []
    assert all(job["environment"] == "npm-release" for job in publishers)
    assert all(
        # Widened contract (was actions:read + id-token:write only): the lineage proof
        # reads `compare/` and `branches/main`, which need contents:read.
        _mapping(job["permissions"])
        == {
            "actions": "read",
            "contents": "read",
            "id-token": "write",
        }
        for job in publishers
    )


def test_should_bind_download_and_remote_dagger_to_the_candidate_run_identity() -> None:
    # Given
    workflow = _workflow("publish.yml")

    # When / Then
    for job in (_mapping(value) for value in _jobs(workflow).values()):
        download = _action("actions/download-artifact", job)
        assert _with(download) == {
            "name": "assay-${{ github.event.workflow_run.head_sha }}",
            "path": "candidate",
            "github-token": "${{ github.token }}",
            "run-id": "${{ github.event.workflow_run.id }}",
        }
        dagger_step = _publisher_dagger(job)
        assert _with(dagger_step)["module"] == (
            "github.com/hseshadr/assay@${{ github.event.workflow_run.head_sha }}"
        )


#: The central lineage proof (hseshadr/ci#49), pinned at a literal hseshadr/ci commit.
LINEAGE_MODULE = re.compile(r"^github\.com/hseshadr/ci/modules/portfolio-foundation@[0-9a-f]{40}$")
#: Exact args: every value is a quoted env var bound to the triggering run, so a
#: hard-coded run id or SHA cannot make the proof about a different run.
LINEAGE_ARGS = (
    'release-lineage --github-token=env:GH_TOKEN --repository="$GITHUB_REPOSITORY" '
    '--run-id="$RUN_ID" --head-sha="$HEAD_SHA" --publish-run-id="$GITHUB_RUN_ID"'
)
# The npm publisher runs the same lineage proof through release-provenance, which also
# exports the GitHub Actions context npm needs (see the Dagger publish-npm contract).
LINEAGE_INVOCATIONS = {
    "publish-python": LINEAGE_ARGS,
    "publish-npm": LINEAGE_ARGS.replace("release-lineage ", "release-provenance ", 1)
    + " export --path=github-context.json",
}
LINEAGE_ENV = {
    "GH_TOKEN": "${{ github.token }}",
    "RUN_ID": "${{ github.event.workflow_run.id }}",
    "HEAD_SHA": "${{ github.event.workflow_run.head_sha }}",
}


def _is_lineage(step: dict[str, object]) -> bool:
    return str(_with(step).get("module", "")).startswith(
        "github.com/hseshadr/ci/modules/portfolio-foundation@"
    )


def _publisher_dagger(job: dict[str, object]) -> dict[str, object]:
    matches = [
        step
        for step in _steps(job)
        if str(step.get("uses", "")).startswith("dagger/dagger-for-github@")
        and not _is_lineage(step)
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize("name", ["publish-python", "publish-npm"])
def test_should_prove_candidate_lineage_before_any_publisher_touches_an_artifact(
    name: str,
) -> None:
    # Given the first step of each publisher. The job `if` (head_branch == default_branch)
    # is satisfied by a dispatch on a TAG named `main`, so this proof must run first.
    lineage = _steps(_job(_workflow("publish.yml"), name))[0]
    invocation = dict(_with(lineage))

    # Then it is the central lineage call, fed only through quoted env values
    assert str(lineage["uses"]).startswith("dagger/dagger-for-github@")
    assert _mapping(lineage.get("env")) == LINEAGE_ENV
    assert LINEAGE_MODULE.fullmatch(str(invocation.pop("module")))
    expected = {"version": "0.21.8", "verb": "call", "args": LINEAGE_INVOCATIONS[name]}
    assert invocation == expected


def test_should_gate_publishers_on_successful_manual_default_branch_candidate() -> None:
    # Given / When
    jobs = _jobs(_workflow("publish.yml"))
    conditions = {str(_mapping(job)["if"]) for job in jobs.values()}

    # Then
    assert conditions == {
        "github.event.workflow_run.conclusion == 'success' && "
        "github.event.workflow_run.event == 'workflow_dispatch' && "
        "github.event.workflow_run.head_branch == github.event.repository.default_branch"
    }


def test_should_give_no_fork_or_shell_path_privileged_base_authority() -> None:
    # Given
    source = "\n".join(
        (WORKFLOW_ROOT / name).read_text(encoding="utf-8") for name in WORKFLOW_NAMES
    )

    # When / Then
    assert "pull_request_target" not in source
    assert "dangerously-allow-all-builds" not in source
    assert "secrets." not in source


def test_should_serialize_every_graph_and_scope_the_one_trigger_exception() -> None:
    # Given
    workflows = {name: _workflow(name) for name in WORKFLOW_NAMES}
    sources = {name: (WORKFLOW_ROOT / name).read_text(encoding="utf-8") for name in WORKFLOW_NAMES}

    # When / Then
    for workflow in workflows.values():
        concurrency = _mapping(workflow["concurrency"])
        assert str(concurrency["group"]).startswith("assay-")
        assert concurrency["cancel-in-progress"] in {"true", "false"}
    exception = "zizmor: ignore[dangerous-triggers]"
    assert exception in sources["publish.yml"]
    assert sum(source.count(exception) for source in sources.values()) == 1


REPIN_GUARD = (
    "github.event.pull_request.user.login == 'dependabot[bot]' && "
    "github.event.pull_request.head.repo.full_name == github.repository && "
    "startsWith(github.head_ref, 'dependabot/npm_and_yarn/')"
)


def test_should_run_the_repin_only_for_same_repository_dependabot_npm_prs() -> None:
    # Given
    workflow = _workflow("dependabot-repin.yml")
    jobs = _jobs(workflow)

    # When
    derive = _job(workflow, "derive")

    # Then
    assert set(_mapping(workflow["on"])) == {"pull_request"}
    assert workflow["permissions"] == {}
    assert str(derive["if"]) == REPIN_GUARD
    assert {name: _mapping(job).get("needs") for name, job in jobs.items()} == {
        "derive": None,
        "repin": "derive",
        "explain": "derive",
    }
    assert "github.actor" not in (WORKFLOW_ROOT / "dependabot-repin.yml").read_text("utf-8")


DAGGER_REPIN = "dagger/dagger-for-github@27b130bf0f79a7f6fbbbe0fbca6760dc9bb40a77"
BASE_CHECKOUT = {"ref": "${{ github.event.pull_request.base.sha }}", "persist-credentials": "false"}
PR_NUMBER = "${{ github.event.pull_request.number }}"
REPIN_CALLS: dict[str, tuple[str, dict[str, str]]] = {
    "derive": (
        'repin-classify --head-sha="$HEAD_SHA"',
        {"HEAD_SHA": "${{ github.event.pull_request.head.sha }}"},
    ),
    "repin": (
        'repin-commit --github-token=env:GH_TOKEN --pr-number="$PR_NUMBER" --digest="$DIGEST"',
        {
            "GH_TOKEN": "${{ github.token }}",
            "PR_NUMBER": PR_NUMBER,
            "DIGEST": "${{ needs.derive.outputs.digest }}",
        },
    ),
    "explain": (
        'repin-explain --github-token=env:GH_TOKEN --pr-number="$PR_NUMBER" --kind="$KIND"',
        {
            "GH_TOKEN": "${{ github.token }}",
            "PR_NUMBER": PR_NUMBER,
            "KIND": "${{ needs.derive.outputs.decision == 'review' && 'archive' || 'actions' }}",
        },
    ),
}


def test_should_keep_dependency_code_away_from_the_write_token() -> None:
    # Given
    workflow = _workflow("dependabot-repin.yml")

    # When
    permissions = {name: _mapping(job)["permissions"] for name, job in _jobs(workflow).items()}
    derive = _job(workflow, "derive")

    # Then: only the read-only job builds the head; the write jobs only edit text
    assert permissions == {
        "derive": {"contents": "read"},
        "repin": {"contents": "write", "actions": "write"},
        "explain": {"pull-requests": "write"},
    }
    assert _mapping(derive["outputs"]) == {
        "decision": "${{ fromJSON(steps.classify.outputs.output).decision }}",
        "digest": "${{ fromJSON(steps.classify.outputs.output).digest }}",
    }


@pytest.mark.parametrize("name", sorted(REPIN_CALLS))
def test_should_run_each_repin_job_as_base_checkout_then_one_dagger_call(name: str) -> None:
    # Given: the hseshadr/ci fleet policy accepts only checkout then Dagger, no run step
    job = _job(_workflow("dependabot-repin.yml"), name)
    args, env = REPIN_CALLS[name]

    # When
    steps = _steps(job)

    # Then: the base commit's module and tool do the work; event values arrive by env
    assert _uses(job) == ("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", DAGGER_REPIN)
    assert not [step for step in steps if "run" in step]
    assert _with(steps[0]) == BASE_CHECKOUT
    assert _mapping(steps[1].get("env")) == env
    assert _with(steps[1]) == {"version": "0.21.8", "verb": "call", "args": args}


def test_should_explain_manual_repins_once() -> None:
    # Given
    job = _job(_workflow("dependabot-repin.yml"), "explain")

    # When
    condition = " ".join(str(job["if"]).split())

    # Then
    assert condition.startswith("always() && github.event.action == 'opened' && ")
    assert "github.event.pull_request.user.login == 'dependabot[bot]'" in condition
    assert "github.event.pull_request.head.repo.full_name == github.repository" in condition
    assert "startsWith(github.head_ref, 'dependabot/github_actions/')" in condition


def test_should_expose_the_repin_graph_with_typed_tokens() -> None:
    # Given
    module = (ROOT / ".dagger/src/assay_dagger/main.py").read_text(encoding="utf-8")

    # Then
    assert "def repin_classify(self, head_sha: str)" in module
    assert "github_token: dagger.Secret, pr_number: int, digest: str" in module
    assert "github_token: dagger.Secret, pr_number: int, kind: str" in module
