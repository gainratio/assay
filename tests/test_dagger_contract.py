"""Behavioral contracts for Assay's typed Dagger release graph."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Self, cast

import dagger
import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".dagger/src"))

import assay_dagger.main as dagger_module  # noqa: E402
from assay_dagger.main import Assay  # noqa: E402
from scripts.release_epoch import source_date_epoch  # noqa: E402

FOUNDATION_SHA = "9d491851fc5c65ad4a388ed2dd7bb4def4e1f007"
REPOSITORY = "hseshadr/assay"


class FoundationRejectedError(RuntimeError):
    """Controlled fail-closed response from the shared Foundation boundary."""


class HistoryUnavailableError(RuntimeError):
    """Controlled failure to resolve the already verified exact history."""


class RecordingFoundation:
    """Record the typed values sent across the generated Foundation API."""

    def __init__(self) -> None:
        self.bound = cast(dagger.Directory, object())
        self.checked = cast(dagger.Container, object())
        self.source_call: tuple[dagger.Directory, str, str] | None = None
        self.guard_call: tuple[dagger.Directory, str, str] | None = None
        self.reject_guard = False

    def source(
        self, source: dagger.Directory, repository: str, commit_sha: str
    ) -> dagger.Directory:
        self.source_call = source, repository, commit_sha
        return self.bound

    def guard(self, source: dagger.Directory, repository: str, commit_sha: str) -> dagger.Container:
        self.guard_call = source, repository, commit_sha
        if self.reject_guard:
            raise FoundationRejectedError("shared guard rejected the source")
        return self.checked


class SyncResult:
    """Record completion of one lazy product gate."""

    def __init__(self, name: str, completed: list[str]) -> None:
        self._name = name
        self._completed = completed

    async def sync(self) -> Self:
        self._completed.append(self._name)
        return self


class RejectingSyncResult:
    """Raise a controlled Foundation rejection when the lazy guard executes."""

    def __init__(self, error: FoundationRejectedError) -> None:
        self._error = error

    async def sync(self) -> Self:
        raise self._error


class RecordingAssay(Assay):
    """Expose the source composition selected by the canonical CI graph."""

    def configure(self, bound: dagger.Directory, history: dagger.Directory | None = None) -> None:
        self.bound = bound
        self.history = history if history is not None else cast(dagger.Directory, object())
        self.guard_error: FoundationRejectedError | None = None
        self.history_error: HistoryUnavailableError | None = None
        self.inputs: list[tuple[str, dagger.Directory]] = []
        self.completed: list[str] = []
        self.security_call: dagger.Directory | None = None

    def _canonical_source(self, source: dagger.Directory, commit_sha: str) -> dagger.Directory:
        self.inputs.append((f"bind:{commit_sha}", source))
        return self.bound

    def _source_with_history(
        self, source: dagger.Directory, commit_sha: str = ""
    ) -> dagger.Directory:
        self.inputs.append((f"history:{commit_sha}", source))
        if self.history_error is not None:
            raise self.history_error
        return self.history

    def _shared_guard(self, source: dagger.Directory, commit_sha: str) -> dagger.Container:
        self.inputs.append((f"guard:{commit_sha}", source))
        if self.guard_error is not None:
            return cast(dagger.Container, RejectingSyncResult(self.guard_error))
        return cast(dagger.Container, SyncResult("foundation-guard", self.completed))

    def _node(self, name: str, source: dagger.Directory) -> dagger.Container:
        self.inputs.append((name, source))
        return cast(dagger.Container, SyncResult(name, self.completed))

    def _python_gate(self, source: dagger.Directory) -> dagger.Container:
        return self._node("python", source)

    def _typescript_gate(self, source: dagger.Directory) -> dagger.Container:
        return self._node("typescript", source)

    def _release_evidence(self, source: dagger.Directory) -> dagger.Container:
        return self._node("evidence", source)

    def _artifact_container(self, source: dagger.Directory) -> dagger.Container:
        return self._node("artifact", source)

    async def _security(self, source: dagger.Directory) -> None:
        self.inputs.append(("security", source))
        self.security_call = source
        self.completed.append("security")


class ReleaseEpochResult:
    """Run the real artifact epoch read against one controlled source tree."""

    def __init__(self, source: Path, assay: EpochRecordingAssay) -> None:
        self._source = source
        self._assay = assay

    async def sync(self) -> Self:
        self._assay.artifact_epoch = source_date_epoch(self._source)
        self._assay.completed.append("artifact")
        return self


def _product_history(root: Path) -> tuple[int, int]:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],  # noqa: S607
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return status.returncode, source_date_epoch(root)


class ProductHistoryResult:
    """Exercise the hosted product gate against its selected source tree."""

    def __init__(self, source: Path, assay: EpochRecordingAssay) -> None:
        self._source = source
        self._assay = assay

    async def sync(self) -> Self:
        status, epoch = await asyncio.to_thread(_product_history, self._source)
        self._assay.product_git_status = status
        self._assay.product_epoch = epoch
        self._assay.completed.append("python")
        return self


class EpochRecordingAssay(RecordingAssay):
    """Exercise the artifact boundary with a real git-archive-like source."""

    def configure_paths(self, snapshot: Path, overlay: Path) -> None:
        self.configure(cast(dagger.Directory, snapshot), cast(dagger.Directory, overlay))
        self.artifact_epoch: int | None = None
        self.product_epoch: int | None = None
        self.product_git_status: int | None = None

    def _python_gate(self, source: dagger.Directory) -> dagger.Container:
        self.inputs.append(("python", source))
        result = ProductHistoryResult(cast(Path, source), self)
        return cast(dagger.Container, result)

    def _artifact_container(self, source: dagger.Directory) -> dagger.Container:
        self.inputs.append(("artifact", source))
        result = ReleaseEpochResult(cast(Path, source), self)
        return cast(dagger.Container, result)


class RecordingWorkspace:
    """Record the explicit source root selected by the constructor."""

    def __init__(self) -> None:
        self.path = ""
        self.excludes: list[str] = []

    def directory(self, path: str, *, exclude: list[str]) -> dagger.Directory:
        self.path = path
        self.excludes = exclude
        return cast(dagger.Directory, object())


class CandidateFile:
    """Return one controlled candidate file payload."""

    def __init__(self, contents: str) -> None:
        self._contents = contents

    async def contents(self) -> str:
        return self._contents


class RecordingCandidate:
    """Expose a controlled candidate directory without a Dagger engine."""

    def __init__(self, entries: list[str], files: dict[str, str], prefix: str = "") -> None:
        self._entries = entries
        self._files = files
        self._prefix = prefix

    async def entries(self) -> list[str]:
        prefix = f"{self._prefix}/" if self._prefix else ""
        children = {
            entry.removeprefix(prefix).split("/", maxsplit=1)[0]
            for entry in self._entries
            if entry.startswith(prefix)
        }
        return sorted(children)

    def directory(self, path: str) -> dagger.Directory:
        prefix = f"{self._prefix}/{path}" if self._prefix else path
        return cast(dagger.Directory, RecordingCandidate(self._entries, self._files, prefix))

    def file(self, path: str) -> dagger.File:
        return cast(dagger.File, CandidateFile(self._files[path]))


def _git(root: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", *arguments],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout.strip()


def _committed_source(root: Path) -> int:
    root.mkdir()
    (root / "README.md").write_text("verified artifact source\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "ci@example.invalid")
    _git(root, "config", "user.name", "CI")
    _git(root, "add", "README.md")
    dates = os.environ | {
        "GIT_AUTHOR_DATE": "1700000000 +0000",
        "GIT_COMMITTER_DATE": "1700000000 +0000",
    }
    _git(root, "commit", "-qm", "verified source", env=dates)
    return int(_git(root, "log", "-1", "--pretty=%ct"))


def _archive_and_history_overlay(tmp_path: Path) -> tuple[Path, Path, int]:
    history = tmp_path / "history"
    epoch = _committed_source(history)
    snapshot = tmp_path / "snapshot"
    shutil.copytree(history, snapshot, ignore=shutil.ignore_patterns(".git"))
    overlay = tmp_path / "overlay"
    shutil.copytree(snapshot, overlay)
    shutil.copytree(history / ".git", overlay / ".git")
    return snapshot, overlay, epoch


def test_should_construct_from_one_explicit_typed_workspace() -> None:
    # Given
    workspace = RecordingWorkspace()

    # When
    Assay.create(cast(dagger.Workspace, workspace))

    # Then
    assert workspace.path == "/"
    assert ".git" in workspace.excludes
    assert "**/node_modules" in workspace.excludes
    assert {".env", "**/.env", "*.key", "**/*.key", "*.pem", "**/*.pem"} <= set(workspace.excludes)
    signature = inspect.signature(Assay.create, eval_str=True)
    assert signature.parameters["workspace"].annotation is dagger.Workspace


def test_should_pin_the_exact_foundation_dependency_and_lock() -> None:
    # Given / When
    config = json.loads((ROOT / "dagger.json").read_text(encoding="utf-8"))

    # Then
    assert config["dependencies"] == [
        {
            "name": "foundation",
            "source": f"github.com/hseshadr/ci/modules/portfolio-foundation@{FOUNDATION_SHA}",
            "pin": FOUNDATION_SHA,
        }
    ]
    assert ".dagger/uv.lock" in config["include"]
    assert (ROOT / ".dagger/uv.lock").is_file()


def test_should_declare_the_dagger_main_object_for_clean_bootstrap() -> None:
    # Given / When
    project = tomllib.loads((ROOT / ".dagger/pyproject.toml").read_text(encoding="utf-8"))

    # Then
    assert project["project"]["entry-points"]["dagger.mod"] == {"main_object": "assay_dagger:Assay"}
    assert project["build-system"] == {
        "requires": ["uv_build>=0.8.4,<0.9.0"],
        "build-backend": "uv_build",
    }


def test_should_send_the_caller_snapshot_and_exact_identity_to_foundation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = cast(dagger.Directory, object())
    foundation = RecordingFoundation()
    monkeypatch.setattr(dagger_module, "_foundation", lambda: foundation, raising=False)

    # When
    bound = Assay._canonical_source(source, "a" * 40)
    checked = Assay._shared_guard(source, "a" * 40)

    # Then
    assert bound is foundation.bound
    assert checked is foundation.checked
    assert foundation.source_call == (source, REPOSITORY, "a" * 40)
    assert foundation.guard_call == (source, REPOSITORY, "a" * 40)


def test_should_propagate_a_foundation_rejection_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = cast(dagger.Directory, object())
    foundation = RecordingFoundation()
    foundation.reject_guard = True
    monkeypatch.setattr(dagger_module, "_foundation", lambda: foundation, raising=False)

    # When / Then
    with pytest.raises(FoundationRejectedError, match="shared guard rejected the source"):
        Assay._shared_guard(source, "b" * 40)
    assert foundation.guard_call == (source, REPOSITORY, "b" * 40)


def test_should_run_canonical_ci_on_the_history_bound_verified_source() -> None:
    # Given
    source = cast(dagger.Directory, object())
    bound = cast(dagger.Directory, object())
    history = cast(dagger.Directory, object())
    assay = RecordingAssay.__new__(RecordingAssay)
    assay.configure(bound, history)

    # When
    asyncio.run(assay._run_ci(source, "c" * 40))

    # Then
    assert assay.inputs == [
        (f"bind:{'c' * 40}", source),
        (f"guard:{'c' * 40}", bound),
        (f"history:{'c' * 40}", bound),
        ("python", history),
        ("typescript", history),
        ("evidence", history),
        ("security", history),
        ("artifact", history),
    ]
    assert assay.security_call is history
    assert assay.completed == [
        "foundation-guard",
        "python",
        "typescript",
        "evidence",
        "security",
        "artifact",
    ]


def test_should_overlay_exact_history_before_product_and_artifact_gates(tmp_path: Path) -> None:
    # Given
    snapshot, overlay, expected_epoch = _archive_and_history_overlay(tmp_path)
    assay = EpochRecordingAssay.__new__(EpochRecordingAssay)
    assay.configure_paths(snapshot, overlay)

    # When
    asyncio.run(assay._run_ci(cast(dagger.Directory, snapshot), "e" * 40))

    # Then
    assert assay.product_git_status == 0
    assert assay.product_epoch == expected_epoch
    assert assay.artifact_epoch == expected_epoch
    assert (f"history:{'e' * 40}", cast(dagger.Directory, snapshot)) in assay.inputs
    assert assay.inputs[-1] == ("artifact", cast(dagger.Directory, overlay))


@pytest.mark.parametrize("message", ["exact history is missing", "exact history is wrong"])
def test_should_fail_closed_before_product_or_artifacts_when_exact_history_is_unavailable(
    message: str,
) -> None:
    # Given
    source = cast(dagger.Directory, object())
    bound = cast(dagger.Directory, object())
    assay = RecordingAssay.__new__(RecordingAssay)
    assay.configure(bound)
    assay.history_error = HistoryUnavailableError(message)

    # When / Then
    with pytest.raises(HistoryUnavailableError, match=message):
        asyncio.run(assay._run_ci(source, "f" * 40))
    assert assay.completed == ["foundation-guard"]


def test_should_fail_closed_before_history_or_product_when_foundation_guard_rejects() -> None:
    # Given
    source = cast(dagger.Directory, object())
    bound = cast(dagger.Directory, object())
    assay = RecordingAssay.__new__(RecordingAssay)
    assay.configure(bound)
    assay.guard_error = FoundationRejectedError("shared guard rejected the source")

    # When / Then
    with pytest.raises(FoundationRejectedError, match="shared guard rejected the source"):
        asyncio.run(assay._run_ci(source, "b" * 40))
    assert assay.inputs == [
        (f"bind:{'b' * 40}", source),
        (f"guard:{'b' * 40}", bound),
    ]
    assert assay.completed == []


def test_should_run_scheduled_security_on_the_foundation_bound_snapshot() -> None:
    # Given
    source = cast(dagger.Directory, object())
    bound = cast(dagger.Directory, object())
    assay = RecordingAssay.__new__(RecordingAssay)
    assay.source = source
    assay.configure(bound)

    # When
    result = asyncio.run(assay.security("d" * 40))

    # Then
    assert assay.inputs == [
        (f"bind:{'d' * 40}", source),
        (f"guard:{'d' * 40}", bound),
        (f"history:{'d' * 40}", bound),
        ("security", assay.history),
    ]
    assert assay.security_call is assay.history
    assert assay.completed == ["foundation-guard", "security"]
    assert result == "Assay Dagger security gate passed"


def test_should_leave_shared_guard_work_out_of_the_local_adapter() -> None:
    # Given / When
    module = inspect.getmodule(Assay)

    # Then
    assert module is not None
    assert not hasattr(Assay, "_workflow_security")
    assert not hasattr(Assay, "_secret_scan")
    assert not hasattr(Assay, "_actionlint")
    assert not hasattr(Assay, "_gitleaks")
    assert not hasattr(module, "ACTIONLINT_IMAGE")
    assert not hasattr(module, "GITLEAKS_IMAGE")


def test_should_expose_one_complete_ci_and_release_graph() -> None:
    # Given
    expected = {
        "artifacts",
        "ci",
        "python",
        "typescript",
        "security",
        "release_candidate",
        "pypi_required",
        "publish_npm",
    }

    # When
    available = {name for name in expected if hasattr(Assay, name)}

    # Then
    assert available == expected


def test_should_type_every_authority_and_artifact_boundary() -> None:
    # Given / When
    candidate = inspect.signature(Assay.release_candidate, eval_str=True)
    npm = inspect.signature(Assay.publish_npm, eval_str=True)

    # Then
    assert candidate.parameters["github_token"].annotation is dagger.Secret
    assert candidate.return_annotation is dagger.Directory
    assert npm.parameters["candidate"].annotation is dagger.Directory
    assert npm.parameters["oidc_url"].annotation is dagger.Secret
    assert npm.parameters["oidc_token"].annotation is dagger.Secret
    assert npm.return_annotation is str


def test_should_keep_quality_on_the_typed_source_but_overlay_only_git_history() -> None:
    # Given / When
    implementation = inspect.getsource(Assay._source_with_history)

    # Then
    assert 'filter(include=[".git", ".git/**"])' in implementation
    assert 'git_metadata.with_directory("/", source)' in implementation
    assert 'history.with_directory("/", source)' not in implementation


def test_should_run_product_audits_after_shared_guard_and_history_binding() -> None:
    # Given / When
    verification = inspect.getsource(Assay._verified_source)
    security = inspect.getsource(Assay._security)

    # Then
    assert "_shared_guard" in verification
    assert "_source_with_history" in verification
    assert "audit-python" in security
    assert "audit-typescript" in security
    assert "_shellcheck" in security


def test_should_build_candidate_from_exact_history_and_record_registry_decisions() -> None:
    # Given / When
    release = inspect.getsource(Assay.release_candidate)
    candidate = inspect.getsource(Assay._candidate)
    artifacts = inspect.getsource(Assay._artifact_container)

    # Then
    assert "_source_with_history" in release
    assert "_hosted" in release
    assert "_registry_preflight" in candidate
    assert "build_release_artifacts.sh" in artifacts
    assert "stage_npm_publisher.sh" in candidate
    assert "CANDIDATE-SHA256SUMS" in candidate


def test_should_accept_only_a_checksum_bound_candidate_plan() -> None:
    # Given
    sha = "a" * 40
    entries = ["CANDIDATE-SHA256SUMS", "publication/pypi.env"]
    files = {
        "publication/pypi.env": f"expected-sha={sha}\npublish=true\n",
        "CANDIDATE-SHA256SUMS": "",
    }
    candidate = RecordingCandidate(entries, files)

    # When
    required = asyncio.run(
        Assay._pypi_decision(cast(dagger.Directory, candidate), expected_sha=sha)
    )

    # Then
    assert required is True


def test_should_reject_a_candidate_plan_for_another_commit() -> None:
    # Given
    candidate = RecordingCandidate(
        ["publication/pypi.env"],
        {"publication/pypi.env": f"expected-sha={'a' * 40}\npublish=true\n"},
    )

    # When / Then
    with pytest.raises(ValueError, match="candidate commit identity differs"):
        asyncio.run(Assay._pypi_decision(cast(dagger.Directory, candidate), expected_sha="b" * 40))


def test_should_reject_a_non_boolean_npm_publish_decision() -> None:
    # Given
    sha = "a" * 40
    plan = (
        f"expected-sha={sha}\npublish=maybe\ndist-tag=latest\npublish-tag=latest\n"
        "channel-version=__ABSENT__\npublish-tag-version=__ABSENT__\n"
    )
    candidate = RecordingCandidate(["publication/npm.env"], {"publication/npm.env": plan})

    # When / Then
    with pytest.raises(ValueError, match="candidate npm decision is malformed"):
        asyncio.run(Assay._npm_decision(cast(dagger.Directory, candidate), expected_sha=sha))


def test_should_reject_extra_material_inside_the_release_envelope() -> None:
    # Given
    entries = [
        "CANDIDATE-SHA256SUMS",
        "publication/npm.env",
        "publication/pypi.env",
        "publish-tools/npm-12.0.2.tgz",
        "release/SHA256SUMS",
        "release/npm/edgeproc-assay-0.5.0-dev.3.tgz",
        "release/python/assay_engine-0.5.0.dev3-py3-none-any.whl",
        "release/python/assay_engine-0.5.0.dev3.tar.gz",
        "release/source.py",
    ]
    candidate = RecordingCandidate(entries, {})

    # When / Then
    with pytest.raises(ValueError, match="candidate release envelope differs"):
        asyncio.run(Assay._require_candidate_shape(cast(dagger.Directory, candidate)))


def test_should_pin_every_downloaded_tool_and_package_manager() -> None:
    # Given / When
    module = inspect.getmodule(Assay)

    # Then
    assert module is not None
    assert "@sha256:" in module.PYTHON_IMAGE
    assert "@sha256:" in module.NODE_IMAGE
    assert "@sha256:" in module.UV_IMAGE
    assert module.PNPM_VERSION == "11.5.0"


def test_should_lock_temporal_audit_tools_in_the_repository_environment() -> None:
    # Given / When
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")

    # Then scheduled audits cannot resolve an unreviewed uvx environment at runtime
    assert '"pip-audit==2.10.1"' in project
    assert '"zizmor==1.30.1"' in project
    assert "uvx --from pip-audit" not in project
    assert "uvx zizmor" not in project
    assert 'name = "pip-audit"' in lock
    assert 'name = "zizmor"' in lock


def test_should_install_both_locked_uv_executables() -> None:
    # Given / When
    toolchain = inspect.getsource(Assay._toolchain)

    # Then dependency-audit tasks can invoke the uvx binary from the same pinned image
    assert '.file("/uv")' in toolchain
    assert '.file("/uvx")' in toolchain
    assert 'with_file("/usr/local/bin/uvx", uvx)' in toolchain


def test_should_reduce_the_total_dagger_and_workflow_surface() -> None:
    # Given
    paths = (
        *tuple((ROOT / ".dagger/src/assay_dagger").glob("*.py")),
        *tuple((ROOT / ".github/workflows").glob("*.yml")),
    )

    # When
    lines = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in paths)

    # Then (budget raised from 700 to 720 for the two central lineage steps in publish.yml,
    # hseshadr/ci#49: required publisher surface, not new repository logic)
    assert 400 <= lines <= 720
