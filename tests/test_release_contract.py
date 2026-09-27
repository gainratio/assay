from __future__ import annotations

import base64
import hashlib
import importlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import zipfile
from pathlib import Path

import pytest


def _identity_fixture(tmp_path: Path, *, python_version: str, npm_version: str) -> Path:
    root = tmp_path / f"identity-{python_version}-{npm_version}"
    shutil.copytree("scripts", root / "scripts")
    shutil.copytree("src/assay", root / "src/assay")
    source = root / "src/assay/_version.py"
    source.write_text(
        re.sub(
            r'^__version__ = "[^"]+"$',
            f'__version__ = "{python_version}"',
            source.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        ),
        encoding="utf-8",
    )
    (root / "ts").mkdir()
    package = json.loads(Path("ts/package.json").read_text(encoding="utf-8"))
    package["version"] = npm_version
    (root / "ts/package.json").write_text(json.dumps(package), encoding="utf-8")
    _initialize_repository(root)
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root / "scripts/verify_release_identity.py"


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _initialize_repository(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "assay-tests@example.invalid")
    _git(root, "config", "user.name", "Assay Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")


def _tag(root: Path, tag: str, *, annotated: bool = False, target: str = "HEAD") -> None:
    arguments = ("tag", "-am", "fixture tag", tag, target) if annotated else ("tag", tag, target)
    _git(root, *arguments)


def _run_identity(
    script: Path,
    tag: str,
    *,
    annotated: bool = False,
    target: str = "HEAD",
    github_sha: str | None = None,
) -> subprocess.CompletedProcess[str]:
    root = script.parents[1]
    _tag(root, tag, annotated=annotated, target=target)
    expected = github_sha or _git(root, "rev-parse", "HEAD")
    return subprocess.run(
        [sys.executable, script, tag, expected],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("python_version", "npm_version", "tag"),
    [("1.2.3", "1.2.3", "v1.2.3"), ("1.2.3.dev4", "1.2.3-dev.4", "v1.2.3-dev.4")],
)
def test_should_accept_exact_stable_and_dev_release_identities(
    tmp_path: Path, python_version: str, npm_version: str, tag: str
) -> None:
    # Given aligned source identities in the only supported spellings
    script = _identity_fixture(tmp_path, python_version=python_version, npm_version=npm_version)
    # When the exact tag is checked
    result = _run_identity(script, tag)
    # Then release eligibility succeeds without normalization ambiguity
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        f"verified release identity: {tag}\n",
        "",
    )


@pytest.mark.parametrize("annotated", [False, True], ids=("lightweight", "annotated"))
def test_should_peel_the_release_tag_to_the_exact_head_commit(
    tmp_path: Path, annotated: bool
) -> None:
    # Given either supported Git tag representation at the checked-out release commit
    script = _identity_fixture(tmp_path, python_version="0.5.0.dev0", npm_version="0.5.0-dev.0")
    # When source identities, peeled tag target, HEAD, and hosted SHA are verified together
    result = _run_identity(script, "v0.5.0-dev.0", annotated=annotated)
    # Then both tag forms bind to the same immutable release commit
    assert (result.returncode, result.stderr) == (0, "")


def test_should_reject_a_release_tag_pointing_away_from_head(tmp_path: Path) -> None:
    # Given a release tag on the first commit and a newer checked-out HEAD
    script = _identity_fixture(tmp_path, python_version="0.5.0.dev0", npm_version="0.5.0-dev.0")
    root = script.parents[1]
    tagged = _git(root, "rev-parse", "HEAD")
    (root / "README").write_text("new head\n", encoding="utf-8")
    _git(root, "add", "README")
    _git(root, "commit", "-qm", "advance head")
    # When the old commit is tagged for the new release
    result = _run_identity(script, "v0.5.0-dev.0", target=tagged)
    # Then release eligibility fails closed
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "release tag, commit, and artifact versions do not match\n"


def test_should_reject_a_hosted_sha_different_from_tag_and_head(tmp_path: Path) -> None:
    # Given an exact tag at HEAD but a different hosted SHA
    script = _identity_fixture(tmp_path, python_version="0.5.0.dev0", npm_version="0.5.0-dev.0")
    # When release eligibility checks the mismatched hosted context
    result = _run_identity(script, "v0.5.0-dev.0", github_sha="f" * 40)
    # Then it cannot publish artifacts from an unbound commit
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "release tag, commit, and artifact versions do not match\n"


def test_should_reject_a_release_commit_outside_protected_main(tmp_path: Path) -> None:
    # Given an exact release identity committed only on an unprotected branch
    script = _identity_fixture(tmp_path, python_version="0.5.0.dev2", npm_version="0.5.0-dev.2")
    root = script.parents[1]
    (root / "branch-only.txt").write_text("not merged\n", encoding="utf-8")
    _git(root, "add", "branch-only.txt")
    _git(root, "commit", "-qm", "branch-only release")
    # When that unmerged commit receives an otherwise exact release tag
    result = _run_identity(script, "v0.5.0-dev.2")
    # Then the release boundary fails before any publisher can receive credentials
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "release commit is not exact protected main\n"


def test_should_reject_a_release_when_protected_main_is_unavailable(tmp_path: Path) -> None:
    # Given an exact release identity without an independently fetched protected-main ref
    script = _identity_fixture(tmp_path, python_version="0.5.0.dev2", npm_version="0.5.0-dev.2")
    root = script.parents[1]
    _git(root, "update-ref", "-d", "refs/remotes/origin/main")
    # When the exact tag is otherwise eligible
    result = _run_identity(script, "v0.5.0-dev.2")
    # Then missing ancestry evidence cannot degrade into permission to publish
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "release commit is not exact protected main\n"


def test_should_reject_a_release_commit_behind_current_protected_main(tmp_path: Path) -> None:
    # Given a release commit followed by another already-merged main commit
    script = _identity_fixture(tmp_path, python_version="0.5.0.dev2", npm_version="0.5.0-dev.2")
    root = script.parents[1]
    release_sha = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-qb", "future-main")
    (root / "later-main.txt").write_text("later\n", encoding="utf-8")
    _git(root, "add", "later-main.txt")
    _git(root, "commit", "-qm", "later main")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(root, "checkout", "--detach", release_sha)
    # When the earlier merged commit is checked as the exact release identity
    result = _run_identity(script, "v0.5.0-dev.2")
    # Then a stale-but-merged tag cannot authorize publication
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "release commit is not exact protected main\n"


def test_should_accept_only_one_green_dagger_check_on_exact_tag_and_main() -> None:
    # Given
    identity = _load_module("scripts.verify_release_identity")
    sha = "a" * 40
    payload = json.dumps(
        {
            "main_sha": sha,
            "tag_sha": sha,
            "check_runs": [{"name": "Dagger", "head_sha": sha, "conclusion": "success"}],
        }
    )

    # When / Then
    identity.validate_hosted_eligibility(payload, sha)


def test_should_reject_duplicate_or_stale_hosted_dagger_evidence() -> None:
    # Given
    identity = _load_module("scripts.verify_release_identity")
    sha = "a" * 40
    check = {"name": "Dagger", "head_sha": sha, "conclusion": "success"}
    payload = json.dumps({"main_sha": sha, "tag_sha": "b" * 40, "check_runs": [check, check]})

    # When / Then
    with pytest.raises(ValueError, match="exact main, tag, and one green Dagger check required"):
        identity.validate_hosted_eligibility(payload, sha)


@pytest.mark.parametrize(
    ("python_version", "npm_version", "tag"),
    [
        ("01.2.3", "01.2.3", "v01.2.3"),
        ("1.02.3", "1.02.3", "v1.02.3"),
        ("1.2.03", "1.2.03", "v1.2.03"),
        ("1.2.3.post1", "1.2.3-post.1", "v1.2.3-post.1"),
        ("1.2.3+local", "1.2.3+local", "v1.2.3+local"),
        ("1.2.3a1", "1.2.3-alpha.1", "v1.2.3-alpha.1"),
        ("1.2.3b1", "1.2.3-beta.1", "v1.2.3-beta.1"),
        ("1.2.3rc1", "1.2.3-rc.1", "v1.2.3-rc.1"),
        ("1.2.3.dev1", "1.2.3-dev1", "v1.2.3-dev1"),
        ("1.2.3.dev1", "1.2.3-dev.2", "v1.2.3-dev.2"),
    ],
)
def test_should_reject_unsupported_or_divergent_release_identities(
    tmp_path: Path, python_version: str, npm_version: str, tag: str
) -> None:
    # Given malformed, unsupported, or divergent source identities
    script = _identity_fixture(tmp_path, python_version=python_version, npm_version=npm_version)
    # When release eligibility is checked
    result = _run_identity(script, tag)
    # Then it fails closed through one value-free message
    assert (result.returncode, result.stdout, result.stderr) == (
        1,
        "",
        "release tag and artifact versions do not match\n",
    )


def _load_guard() -> object:
    sys.path.insert(0, str(Path.cwd()))
    try:
        return importlib.import_module("scripts.registry_release_guard")
    finally:
        sys.path.pop(0)


def _load_module(name: str) -> object:
    sys.path.insert(0, str(Path.cwd()))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.pop(0)


def _npm_metadata(path: Path) -> dict[str, object]:
    sha1 = hashlib.sha1(path.read_bytes(), usedforsecurity=False).hexdigest()
    sha512 = base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()
    return {
        "dist": {
            "shasum": sha1,
            "integrity": f"sha512-{sha512}",
            "attestations": {
                "url": "https://registry.npmjs.org/-/npm/v1/attestations/example",
                "provenance": {"predicateType": "https://slsa.dev/provenance/v1"},
            },
        }
    }


def _npm_attestation(
    *, tag: str, sha: str, subject_sha512: str, workflow_ref: str = "refs/heads/main"
) -> dict[str, object]:
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": [
            {
                "name": f"pkg:npm/%40edgeproc/assay@{tag.removeprefix('v')}",
                "digest": {"sha512": subject_sha512},
            }
        ],
        "predicate": {
            "buildDefinition": {
                "externalParameters": {
                    "workflow": {
                        "repository": "https://github.com/hseshadr/assay",
                        "path": ".github/workflows/publish.yml",
                        "ref": workflow_ref,
                    }
                },
                "resolvedDependencies": [
                    {
                        "uri": f"git+https://github.com/hseshadr/assay@{workflow_ref}",
                        "digest": {"gitCommit": sha},
                    }
                ],
            }
        },
    }
    payload = base64.b64encode(json.dumps(statement).encode()).decode()
    return {"attestations": [{"bundle": {"dsseEnvelope": {"payload": payload}}}]}


def _pypi_attestation(
    *, filename: str, sha256: str, repository: str = "hseshadr/assay"
) -> dict[str, object]:
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": filename, "digest": {"sha256": sha256}}],
        "predicateType": "https://docs.pypi.org/attestations/publish/v1",
        "predicate": None,
    }
    encoded = base64.b64encode(json.dumps(statement).encode()).decode()
    return {
        "attestation_bundles": [
            {
                "publisher": {
                    "kind": "GitHub",
                    "repository": repository,
                    "workflow": "publish.yml",
                },
                "attestations": [{"envelope": {"statement": encoded}}],
            }
        ]
    }


def test_should_bind_pypi_provenance_to_filename_digest_and_publisher() -> None:
    # Given a PEP 740 provenance bundle from the configured trusted publisher
    guard = _load_guard()
    filename = "assay_engine-0.5.0.dev0-py3-none-any.whl"
    payload = _pypi_attestation(filename=filename, sha256="a" * 64)
    # When the local file identity is checked
    # Then filename, digest, repository, workflow, and publish predicate must all agree
    assert guard.pypi_provenance_valid(payload, filename, "a" * 64) is True
    assert guard.pypi_provenance_valid(payload, filename, "b" * 64) is False
    wrong = _pypi_attestation(filename=filename, sha256="a" * 64, repository="elsewhere/assay")
    assert guard.pypi_provenance_valid(wrong, filename, "a" * 64) is False


def test_should_reject_duplicate_pypi_filename_records() -> None:
    # Given two PyPI records that repeat one filename even with the same digest
    guard = _load_guard()
    record = {"filename": "assay_engine-1.2.3.tar.gz", "digests": {"sha256": "a" * 64}}
    payload = {"urls": [record, record]}
    # When registry filenames are normalized for exact-set comparison
    # Then duplicates cannot disappear through dictionary overwrite
    with pytest.raises(ValueError, match="duplicate PyPI filename"):
        guard._pypi_digests(payload)


def test_should_bind_npm_provenance_to_default_branch_workflow_and_exact_sha(
    tmp_path: Path,
) -> None:
    # Given exact local bytes and an official-shaped npm provenance statement
    guard = _load_guard()
    tarball = tmp_path / "edgeproc-assay.tgz"
    tarball.write_bytes(b"assay-npm")
    metadata = _npm_metadata(tarball)
    sha = "a" * 40
    subject_sha512 = hashlib.sha512(tarball.read_bytes()).hexdigest()
    attestation = _npm_attestation(tag="v0.5.0-dev.0", sha=sha, subject_sha512=subject_sha512)
    identity = guard.ProvenanceIdentity("v0.5.0-dev.0", sha, subject_sha512)
    # When every release binding agrees
    publish = guard.npm_release_state(tarball, metadata, attestation, identity)
    # Then an existing release is safely skipped
    assert publish is False


@pytest.mark.parametrize(("tag", "sha"), [("v0.5.0", "a" * 40), ("v0.5.0-dev.0", "b" * 40)])
def test_should_reject_npm_provenance_bound_to_another_run(
    tmp_path: Path, tag: str, sha: str
) -> None:
    # Given exact bytes but provenance for another tag or commit
    guard = _load_guard()
    tarball = tmp_path / "edgeproc-assay.tgz"
    tarball.write_bytes(b"assay-npm")
    subject_sha512 = hashlib.sha512(tarball.read_bytes()).hexdigest()
    attestation = _npm_attestation(tag=tag, sha=sha, subject_sha512=subject_sha512)
    expected = guard.ProvenanceIdentity("v0.5.0-dev.0", "a" * 40, subject_sha512)
    # When registry state is checked
    # Then the mismatch cannot be treated as a retry-safe skip
    with pytest.raises(ValueError, match="npm artifact or provenance mismatch"):
        guard.npm_release_state(tarball, _npm_metadata(tarball), attestation, expected)


def test_should_reject_npm_provenance_from_the_old_tag_trigger(tmp_path: Path) -> None:
    # Given exact bytes and SHA from the retired tag-triggered publisher
    guard = _load_guard()
    tarball = tmp_path / "edgeproc-assay.tgz"
    tarball.write_bytes(b"assay-npm")
    sha = "a" * 40
    digest = hashlib.sha512(tarball.read_bytes()).hexdigest()
    attestation = _npm_attestation(
        tag="v0.5.0-dev.0",
        sha=sha,
        subject_sha512=digest,
        workflow_ref="refs/tags/v0.5.0-dev.0",
    )
    identity = guard.ProvenanceIdentity("v0.5.0-dev.0", sha, digest)

    # When / Then the default-branch candidate publisher cannot accept the old authority path
    with pytest.raises(ValueError, match="npm artifact or provenance mismatch"):
        guard.npm_release_state(tarball, _npm_metadata(tarball), attestation, identity)


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("0.5.0", None, "latest"),
        ("0.5.0", "0.4.1", "latest"),
        ("0.5.0-dev.0", None, "next"),
        ("0.5.0-dev.1", "0.5.0-dev.0", "next"),
    ],
)
def test_should_allow_only_monotonic_dist_tag_updates(
    candidate: str, current: str | None, expected: str
) -> None:
    # Given a missing or older version on the candidate channel
    guard = _load_guard()
    # When the publish tag is selected
    # Then the release remains on the correct monotonic channel
    assert guard.checked_npm_dist_tag(candidate, current) == expected


@pytest.mark.parametrize(
    ("candidate", "current"),
    [("0.4.1", "0.5.0"), ("0.5.0-dev.0", "0.5.0-dev.1")],
)
def test_should_refuse_a_historical_retry_that_moves_a_dist_tag_backward(
    candidate: str, current: str
) -> None:
    # Given a dist-tag already pointing at a newer version
    guard = _load_guard()
    # When a historical missing release is retried
    # Then the channel cannot be moved backward
    with pytest.raises(ValueError, match="dist-tag would move backward"):
        guard.checked_npm_dist_tag(candidate, current)


@pytest.mark.parametrize(
    ("target", "current"),
    [("0.4.1", "0.5.0"), ("0.5.0-dev.0", "0.5.0-dev.1")],
)
def test_should_accept_a_completed_historical_retry_without_retagging(
    target: str, current: str
) -> None:
    # Given exact target bytes already exist while the channel has advanced
    guard = _load_guard()
    # When final verification checks the untouched channel
    # Then exact or newer same-channel pointers are valid without rollback
    assert guard.dist_tag_is_current_or_newer(target, current) is True


@pytest.mark.parametrize(
    ("version", "tags", "expected"),
    [
        ("1.2.3", {}, "latest"),
        ("1.2.3", {"latest": "1.2.2"}, "latest"),
        ("1.2.3", {"latest": "1.2.4"}, "assay-v1-2-3"),
        ("1.2.3-dev.4", {"next": "1.2.3-dev.5"}, "assay-v1-2-3-dev-4"),
    ],
)
def test_should_select_a_nondefault_tag_for_out_of_order_publication(
    version: str, tags: dict[str, object], expected: str
) -> None:
    # Given absent target bytes and the authoritative package-wide tag state
    guard = _load_guard()
    # When the publish tag is selected immediately before publication
    # Then only a forward release can change latest/next
    assert guard.npm_publish_tag(version, tags) == expected


def test_should_reject_a_version_specific_tag_collision() -> None:
    # Given an out-of-order release whose deterministic nondefault tag is occupied
    guard = _load_guard()
    tags = {"latest": "1.2.4", "assay-v1-2-3": "9.9.9"}
    # When publication is planned
    # Then registry drift fails closed rather than overwriting another tag
    with pytest.raises(ValueError, match="version-specific dist-tag collision"):
        guard.npm_publish_tag("1.2.3", tags)


def test_should_fail_closed_on_cross_channel_registry_state() -> None:
    # Given an externally corrupted canonical tag pointing at another release channel
    guard = _load_guard()
    # When a publish decision is selected
    # Then it cannot be treated as a same-channel semver comparison
    with pytest.raises(ValueError, match="npm channel contains an incompatible version"):
        guard.npm_publish_tag("1.2.3", {"latest": "1.2.4-dev.0"})


_PLACEHOLDER_RECORD: dict[str, object] = {
    "name": "@edgeproc/assay",
    "version": "0.0.0-bootstrap.0",
    "dist": {"fileCount": 3},
}


def _real_record(version: str) -> dict[str, object]:
    return {
        "name": "@edgeproc/assay",
        "version": version,
        "main": "./dist/index.js",
        "exports": {".": {"import": "./dist/index.js"}},
        "dist": {"fileCount": 25},
    }


def _package_document(tags: dict[str, str]) -> dict[str, object]:
    versions: dict[str, object] = {"0.0.0-bootstrap.0": _PLACEHOLDER_RECORD}
    for version in ("0.5.0-dev.3", "0.5.0-dev.4", "0.4.0", "0.5.0"):
        versions[version] = _real_record(version)
    return {"name": "@edgeproc/assay", "dist-tags": tags, "versions": versions}


def test_should_refuse_a_publish_that_leaves_latest_on_the_bootstrap_placeholder() -> None:
    # Given the observed 2026-09 registry state: a prerelease just landed on `next`
    # while `latest` still names the empty 0.0.0-bootstrap.0 trusted-publishing stub
    verifier = _load_module("scripts.verify_published_release")
    package = _package_document(
        {
            "bootstrap": "0.0.0-bootstrap.0",
            "latest": "0.0.0-bootstrap.0",
            "next": "0.5.0-dev.4",
        }
    )
    # When post-publish verification checks the tags
    # Then a plain `npm install @edgeproc/assay` would get nothing, so it fails closed
    with pytest.raises(ValueError, match="npm latest does not identify an installable release"):
        verifier._verify_tags(package, "0.5.0-dev.4", "next", "next", True)


def test_should_refuse_latest_pointing_at_a_version_the_registry_does_not_list() -> None:
    # Given a latest tag naming a version with no registry record
    verifier = _load_module("scripts.verify_published_release")
    package = _package_document({"latest": "9.9.9", "next": "0.5.0-dev.4"})
    # Then the default install target cannot be proven real
    with pytest.raises(ValueError, match="npm latest does not identify an installable release"):
        verifier._verify_tags(package, "0.5.0-dev.4", "next", "next", True)


def test_should_accept_a_prerelease_publish_when_latest_is_a_real_release() -> None:
    # Given latest on an installable release and next on the new prerelease
    verifier = _load_module("scripts.verify_published_release")
    package = _package_document({"latest": "0.5.0-dev.3", "next": "0.5.0-dev.4"})
    # Then verification accepts the state (no exception)
    verifier._verify_tags(package, "0.5.0-dev.4", "next", "next", True)


def test_should_refuse_a_stable_publish_that_leaves_latest_on_an_older_release() -> None:
    # Given a stable 0.5.0 publish whose latest tag still names 0.4.0
    verifier = _load_module("scripts.verify_published_release")
    package = _package_document({"latest": "0.4.0"})
    # Then the default channel moving backward is a permanent failure
    with pytest.raises(ValueError, match="npm channel moved backward or across channels"):
        verifier._verify_tags(package, "0.5.0", "latest", "latest", True)


def test_should_treat_only_authoritative_404_as_registry_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given registry reads returning authoritative absence or transient failure
    guard = _load_guard()
    missing = urllib.error.HTTPError("https://registry.example/x", 404, "", {}, None)
    unavailable = urllib.error.HTTPError("https://registry.example/x", 503, "", {}, None)
    monkeypatch.setattr(guard.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(guard, "_read_json", lambda _url, _timeout: (_ for _ in ()).throw(missing))
    # Then 404 alone is absence
    assert guard._fetch_json("https://registry.example/x") is None
    monkeypatch.setattr(
        guard, "_read_json", lambda _url, _timeout: (_ for _ in ()).throw(unavailable)
    )
    # And 5xx remains a closed failure after bounded retries
    with pytest.raises(urllib.error.HTTPError) as raised:
        guard._fetch_json("https://registry.example/x")
    assert raised.value.code == 503


def test_should_define_one_exact_mutation_set_across_both_runtimes() -> None:
    # Given the release mutation harness
    sys.path.insert(0, str(Path.cwd() / "scripts"))
    try:
        harness = importlib.import_module("mutation_harness")
    finally:
        sys.path.pop(0)
    # When its active IDs and runners are inspected
    names = tuple(mutation.name for mutation in harness.MUTATIONS)
    runners = {mutation.runner for mutation in harness.MUTATIONS}
    # Then the set is non-vacuous, unique, scoring-only, and cross-runtime
    assert len(names) == 121
    assert len(names) == len(set(names))
    assert runners == {"pytest", "vitest"}
    assert sum(mutation.runner == "vitest" for mutation in harness.MUTATIONS) == 31
    assert "npm-release-quarantine-is-24h" in names
    assert "npm-latest-must-be-installable" in names
    assert all("envelope" not in name and "ledger" not in name for name in names)
    assert all(not mutation.target.startswith("src/avow/") for mutation in harness.MUTATIONS)
    source = Path("scripts/mutation_harness.py").read_text(encoding="utf-8")
    assert all(token in source for token in ("v22.13.0", "11.5.0", "_tree_snapshot"))


def test_should_reproduce_python_artifacts_across_independent_builds(tmp_path: Path) -> None:
    # Given two clean output directories and the locked no-isolation builder
    first, second = tmp_path / "first", tmp_path / "second"
    # When wheel and sdist candidates are built independently
    for destination in (first, second):
        subprocess.run(
            ["bash", "scripts/build_python_artifacts.sh", destination],
            check=True,
            capture_output=True,
            text=True,
        )
    # Then both exact filename sets and every byte are reproducible
    first_files = {path.name: path.read_bytes() for path in first.iterdir()}
    second_files = {path.name: path.read_bytes() for path in second.iterdir()}
    assert first_files == second_files
    assert len(tuple(first.glob("*.whl"))) == 1
    assert len(tuple(first.glob("*.tar.gz"))) == 1


def test_should_derive_the_build_epoch_only_from_packaged_python_sources(tmp_path: Path) -> None:
    # Given a package-source commit followed by a later report-only commit
    epoch = _load_module("scripts.release_epoch")
    (tmp_path / "src/assay").mkdir(parents=True)
    (tmp_path / "src/assay/module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
    _initialize_repository(tmp_path)
    source_epoch = epoch.source_date_epoch(tmp_path)
    (tmp_path / "implementation-report.md").write_text("evidence\n", encoding="utf-8")
    _git(tmp_path, "add", "implementation-report.md")
    _git(tmp_path, "commit", "-qm", "report only")
    # When the release epoch is derived again at the newer repository HEAD
    # Then report-only history cannot perturb wheel or sdist timestamps
    assert epoch.source_date_epoch(tmp_path) == source_epoch


@pytest.fixture(scope="module")
def release_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("release-bundle") / "release"
    result = subprocess.run(
        ["bash", "scripts/build_release_artifacts.sh", root],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return root


def _rewrite_manifest(root: Path) -> None:
    files = tuple(
        sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
    )
    lines = (
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root)}"
        for path in files
    )
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    "relative",
    [
        "python/assay_engine-0.5.0.dev3-py3-none-any.whl",
        "python/assay_engine-0.5.0.dev3.tar.gz",
        "npm/edgeproc-assay-0.5.0-dev.3.tgz",
    ],
)
def test_should_reject_renamed_release_artifacts_even_with_a_new_manifest(
    release_bundle: Path, tmp_path: Path, relative: str
) -> None:
    # Given valid bytes renamed away from their exact source-version-derived filename
    root = tmp_path / "release"
    shutil.copytree(release_bundle, root)
    artifact = root / relative
    artifact.rename(artifact.with_name(f"renamed-{artifact.name}"))
    _rewrite_manifest(root)
    # When the preflight verifier checks the recomputed envelope
    verifier = _load_module("scripts.verify_release_artifacts")
    # Then filenames cannot be discovered and trusted after the fact
    with pytest.raises(ValueError, match="release artifact filename mismatch"):
        verifier.verify_release_bundle(root)


def test_should_reject_a_duplicate_wheel_record(release_bundle: Path, tmp_path: Path) -> None:
    # Given a valid wheel with a duplicate archive record and a recomputed manifest
    root = tmp_path / "release"
    shutil.copytree(release_bundle, root)
    wheel = next((root / "python").glob("*.whl"))
    with (
        pytest.warns(UserWarning, match=r"Duplicate name: 'assay/py\.typed'"),
        zipfile.ZipFile(wheel, "a") as archive,
    ):
        archive.writestr("assay/py.typed", b"")
    _rewrite_manifest(root)
    # When exact member validation runs
    verifier = _load_module("scripts.verify_release_artifacts")
    # Then a duplicate cannot be hidden by set normalization
    with pytest.raises(ValueError, match="duplicate wheel member"):
        verifier.verify_release_bundle(root)


def _append_tar_link(path: Path, *, link_type: bytes) -> None:
    replacement = path.with_suffix(".replacement")
    with tarfile.open(path, "r:gz") as source, tarfile.open(replacement, "w:gz") as target:
        for member in source.getmembers():
            payload = source.extractfile(member)
            target.addfile(member, payload)
        link = tarfile.TarInfo("unexpected-link")
        link.type = link_type
        link.linkname = "package/package.json"
        target.addfile(link, io.BytesIO())
    replacement.replace(path)


def _rewrite_tar_member_names(path: Path, mode: str) -> None:
    replacement = path.with_suffix(".replacement")
    with tarfile.open(path, "r:gz") as source, tarfile.open(replacement, "w:gz") as target:
        for member in source.getmembers():
            payload = source.extractfile(member)
            if mode == "npm-alias" and member.name == "package/LICENSE":
                member.name = "package/./LICENSE"
            elif mode == "sdist-root":
                member.name = f"wrong-root-9.9.9/{member.name.partition('/')[2]}"
            target.addfile(member, payload)
    replacement.replace(path)


def _rewrite_wheel_dist_info(path: Path) -> None:
    replacement = path.with_suffix(".replacement")
    with zipfile.ZipFile(path) as source:
        records = tuple((record, source.read(record.filename)) for record in source.infolist())
    with zipfile.ZipFile(replacement, "w") as target:
        for record, payload in records:
            if ".dist-info/" in record.filename:
                suffix = record.filename.partition("/")[2]
                record.filename = f"assay_engine-9.9.9.dist-info/{suffix}"
            target.writestr(record, payload)
    replacement.replace(path)


@pytest.mark.parametrize(
    ("relative", "link_type"),
    [
        ("python/assay_engine-0.5.0.dev3.tar.gz", tarfile.SYMTYPE),
        ("npm/edgeproc-assay-0.5.0-dev.3.tgz", tarfile.LNKTYPE),
    ],
)
def test_should_reject_every_nonregular_tar_member(
    release_bundle: Path, tmp_path: Path, relative: str, link_type: bytes
) -> None:
    # Given an otherwise valid sdist/npm tarball with an extra link and recomputed checksum
    root = tmp_path / "release"
    shutil.copytree(release_bundle, root)
    _append_tar_link(root / relative, link_type=link_type)
    _rewrite_manifest(root)
    # When preflight inspects the complete archive member stream
    verifier = _load_module("scripts.verify_release_artifacts")
    # Then symlinks, hardlinks, devices, and other non-regular entries fail closed
    with pytest.raises(ValueError, match="non-regular tar member"):
        verifier.verify_release_bundle(root)


@pytest.mark.parametrize(
    ("relative", "mode"),
    [
        ("python/assay_engine-0.5.0.dev3.tar.gz", "sdist-root"),
        ("npm/edgeproc-assay-0.5.0-dev.3.tgz", "npm-alias"),
    ],
)
def test_should_reject_noncanonical_or_wrong_root_tar_members(
    release_bundle: Path, tmp_path: Path, relative: str, mode: str
) -> None:
    # Given a re-manifested archive whose raw member spelling is outside the exact allowlist
    root = tmp_path / "release"
    shutil.copytree(release_bundle, root)
    _rewrite_tar_member_names(root / relative, mode)
    _rewrite_manifest(root)
    # When the complete release bundle is verified
    # Then aliases and an identity-incoherent sdist root both fail closed
    verifier = _load_module("scripts.verify_release_artifacts")
    with pytest.raises(ValueError, match=r"membership mismatch|unexpected tar member"):
        verifier.verify_release_bundle(root)


def test_should_bind_wheel_dist_info_root_to_the_release_identity(
    release_bundle: Path, tmp_path: Path
) -> None:
    # Given valid wheel contents moved under another version's dist-info root
    root = tmp_path / "release"
    shutil.copytree(release_bundle, root)
    _rewrite_wheel_dist_info(next((root / "python").glob("*.whl")))
    _rewrite_manifest(root)
    # When exact wheel membership is verified
    # Then embedded metadata cannot excuse a wrong dist-info identity
    verifier = _load_module("scripts.verify_release_artifacts")
    with pytest.raises(ValueError, match="wheel membership mismatch"):
        verifier.verify_release_bundle(root)


def test_should_freeze_scoring_only_benchmark_workloads_and_budgets() -> None:
    # Given executable Python and TypeScript release benchmark entry points
    python = Path("benchmarks/release.py").read_text(encoding="utf-8")
    contracts = Path("benchmarks/_contracts.py").read_text(encoding="utf-8")
    typescript = Path("ts/benchmarks/release.mjs").read_text(encoding="utf-8")
    operations = Path("docs/OPERATIONS.md").read_text(encoding="utf-8")
    # When workload contracts are compared to the operator-facing specification
    expected = ("150000", "10000", "p50", "p95", "p99", "peak RSS", "exact SHA")
    # Then every required workload and evidence field is explicit and synchronized
    source = (python + contracts + typescript).replace("_", "")
    assert all(token in source for token in expected[:2])
    assert all(token in operations for token in expected)
    assert "receipt" not in python.lower() + typescript.lower()
    assert "ledger" not in python.lower() + typescript.lower()


def test_should_report_typescript_peak_rss_from_the_process_high_water_mark() -> None:
    # Given a deterministic resource-usage high-water mark
    program = (
        "import { peakRssMib } from './ts/benchmarks/resourceUsage.mjs';"
        "console.log(peakRssMib({maxRSS: 2048}));"
    )
    # When the benchmark helper normalizes Node's KiB maxRSS value
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", program],
        check=False,
        capture_output=True,
        text=True,
    )
    # Then it reports the real 2 MiB high-water mark, not current RSS
    assert (result.returncode, result.stdout, result.stderr) == (0, "2\n", "")
    source = Path("ts/benchmarks/release.mjs").read_text(encoding="utf-8")
    assert "process.memoryUsage().rss" not in source
    assert "peakRssMib(process.resourceUsage())" in source


def _served_payloads(root: Path) -> tuple[dict[str, object], dict[str, object], dict[str, bytes]]:
    python_files = tuple(sorted((root / "python").iterdir()))
    npm = next((root / "npm").glob("*.tgz"))
    downloads = {
        f"https://files.pythonhosted.org/{path.name}": path.read_bytes() for path in python_files
    }
    npm_url = f"https://registry.npmjs.org/@edgeproc/assay/-/{npm.name}"
    downloads[npm_url] = npm.read_bytes()
    pypi = {
        "urls": [
            {"filename": path.name, "url": f"https://files.pythonhosted.org/{path.name}"}
            for path in python_files
        ]
    }
    return pypi, {"dist": {"tarball": npm_url}}, downloads


def test_should_materialize_and_verify_the_actual_registry_served_bytes(
    release_bundle: Path, tmp_path: Path
) -> None:
    # Given authoritative registry metadata whose download URLs serve the reviewed bytes
    verifier = _load_module("scripts.verify_published_release")
    pypi, npm, downloads = _served_payloads(release_bundle)
    served = tmp_path / "served"

    def fetch(url: str, _deadline: float, expected_size: int) -> bytes:
        assert len(downloads[url]) == expected_size
        return downloads[url]

    # When final verification materializes the registry responses
    verifier.materialize_served_bundle(release_bundle, served, pypi, npm, fetch, 600.0)
    # Then all three local files came from the served responses and form the exact envelope
    expected = {
        path.relative_to(release_bundle): path.read_bytes()
        for path in release_bundle.rglob("*")
        if path.is_file()
    }
    actual = {
        path.relative_to(served): path.read_bytes() for path in served.rglob("*") if path.is_file()
    }
    assert actual == expected


def test_should_retry_only_authoritative_propagation_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given registry state that is authoritatively absent twice before becoming ready
    verifier = _load_module("scripts.verify_published_release")
    now = [0.0]
    attempts = [0]
    sleeps: list[float] = []
    monkeypatch.setattr(verifier.time, "monotonic", lambda: now[0])

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    def verify(deadline: float) -> None:
        assert deadline == 600.0
        attempts[0] += 1
        if attempts[0] < 3:
            raise verifier.PropagationPending("registry object is absent")

    monkeypatch.setattr(verifier.time, "sleep", sleep)
    # When bounded polling runs
    verifier.poll_until_verified(verify, timeout_seconds=600.0)
    # Then only the two explicit absences were retried within the one global deadline
    assert (attempts, sleeps, now) == ([3], [10.0, 10.0], [20.0])


def test_should_fail_immediately_on_permanent_registry_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a permanent byte/provenance/channel conflict
    verifier = _load_module("scripts.verify_published_release")
    sleeps: list[float] = []
    monkeypatch.setattr(verifier.time, "sleep", sleeps.append)

    def verify(_deadline: float) -> None:
        raise ValueError("registry artifact mismatch")

    # When final verification runs
    with pytest.raises(ValueError, match="registry artifact mismatch"):
        verifier.poll_until_verified(verify, timeout_seconds=600.0)
    # Then it does not turn a permanent failure into ten minutes of retries
    assert sleeps == []


def test_should_bound_http_and_sleep_to_one_global_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given only five seconds remain in the complete verification budget
    verifier = _load_module("scripts.verify_published_release")
    observed: list[float] = []
    monkeypatch.setattr(verifier.time, "monotonic", lambda: 595.0)

    def open_url(_url: str, *, timeout: float) -> object:
        observed.append(timeout)
        raise urllib.error.HTTPError("https://registry.npmjs.org/x", 503, "", {}, None)

    monkeypatch.setattr(verifier.urllib.request, "urlopen", open_url)
    # When one HTTP read starts against the absolute 600-second deadline
    with pytest.raises(urllib.error.HTTPError):
        verifier.read_served_bytes("https://registry.npmjs.org/x", 600.0, 4)
    # Then the HTTP timeout consumes no more than the remaining global budget
    assert observed == [5.0]


class _Response(io.BytesIO):
    def __init__(self, payload: bytes, headers: dict[str, str] | None = None) -> None:
        super().__init__(payload)
        self.headers = headers or {}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_arguments: object) -> None:
        self.close()


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "1"}])
def test_should_bound_registry_json_when_content_length_is_absent_or_lies(
    monkeypatch: pytest.MonkeyPatch, headers: dict[str, str]
) -> None:
    # Given an oversized registry response with no trustworthy size declaration
    guard = _load_guard()
    payload = b'"' + b"x" * guard._METADATA_LIMIT + b'"'
    monkeypatch.setattr(
        guard.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(payload, headers),
    )
    # When metadata is streamed
    # Then the explicit cap wins over absent or dishonest Content-Length
    with pytest.raises(ValueError, match="registry metadata exceeds limit"):
        guard._read_json("https://registry.example/metadata", 5.0)


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "4"}])
def test_should_bound_served_artifacts_to_the_reviewed_local_size(
    monkeypatch: pytest.MonkeyPatch, headers: dict[str, str]
) -> None:
    # Given a registry artifact response larger than the reviewed four-byte candidate
    verifier = _load_module("scripts.verify_published_release")
    monkeypatch.setattr(
        verifier.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"abcde", headers),
    )
    monkeypatch.setattr(verifier.time, "monotonic", lambda: 0.0)
    # When actual served bytes are read
    # Then both absent and lying lengths fail before unbounded memory or disk use
    with pytest.raises(ValueError, match="registry artifact size"):
        verifier.read_served_bytes("https://registry.npmjs.org/artifact", 5.0, 4)


def test_should_require_served_artifact_content_length_even_when_body_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given registry bytes whose body matches but whose length is undeclared
    verifier = _load_module("scripts.verify_published_release")
    monkeypatch.setattr(
        verifier.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"abcd"),
    )
    monkeypatch.setattr(verifier.time, "monotonic", lambda: 0.0)
    # When final verification downloads the artifact
    # Then a missing declaration fails closed rather than trusting the body alone
    with pytest.raises(ValueError, match="registry artifact size is missing"):
        verifier.read_served_bytes("https://registry.npmjs.org/artifact", 5.0, 4)


def test_should_use_five_isolated_heavy_samples_and_nearest_rank_percentiles() -> None:
    # Given the frozen benchmark contracts in both runtimes
    contracts = _load_module("benchmarks._contracts")
    python = Path("benchmarks/release.py").read_text(encoding="utf-8")
    typescript = Path("ts/benchmarks/release.mjs").read_text(encoding="utf-8")
    # When the heavy workload distribution is inspected
    # Then one observation can never masquerade as three percentiles
    assert contracts.HEAVY_SAMPLES == 5
    assert contracts.percentile([5.0, 1.0, 3.0, 2.0, 4.0], 0.50) == 3.0
    assert contracts.percentile([5.0, 1.0, 3.0, 2.0, 4.0], 0.95) == 5.0
    assert "range(HEAVY_SAMPLES)" in python
    assert "const HEAVY_SAMPLES = 5" in typescript
    assert "--sample" in python
    assert "--sample" in typescript


def test_should_time_out_a_hung_python_benchmark_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a child process that exceeds its workload-specific execution bound
    release = _load_module("benchmarks.release")

    def hang(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired("python", 65.0)

    monkeypatch.setattr(release.subprocess, "run", hang)
    # When the isolated sampler waits for it
    # Then it fails with a stable bounded-execution error
    with pytest.raises(RuntimeError, match="benchmark child timed out"):
        release._run_sample("minimum")


def test_should_inventory_ignored_release_outputs_but_not_managed_dependencies(
    tmp_path: Path,
) -> None:
    # Given generated release outputs alongside managed Python and Node dependencies
    harness = _load_module("scripts.mutation_harness")
    (tmp_path / ".venv/lib").mkdir(parents=True)
    (tmp_path / ".venv/lib/managed").write_text("managed\n", encoding="utf-8")
    (tmp_path / "ts/node_modules/pkg").mkdir(parents=True)
    (tmp_path / "ts/node_modules/pkg/index.js").write_text("managed\n", encoding="utf-8")
    (tmp_path / "dist/release/python").mkdir(parents=True)
    (tmp_path / "dist/release/python/assay.whl").write_bytes(b"wheel")
    (tmp_path / "dist/publish-tools").mkdir(parents=True)
    (tmp_path / "dist/publish-tools/npm.tgz").write_bytes(b"npm")
    # When the whole-tree cleanliness inventory is captured
    inventory = harness._generated_inventory(tmp_path)
    # Then ignored release products remain visible without hashing managed environments
    assert inventory == (
        "dist/publish-tools/npm.tgz",
        "dist/release/python/assay.whl",
    )


def test_should_remove_every_generated_release_output_when_the_local_gate_exits() -> None:
    # Given the complete local release candidate script
    source = Path("scripts/verify_release_candidate.sh").read_text(encoding="utf-8")
    # Then success and failure both clean the configurable artifact and staged-publisher roots
    assert "trap cleanup EXIT" in source
    assert 'rm -rf -- "$artifact_root" "$publisher_root"' in source
    assert 'artifact_root="${ASSAY_ARTIFACT_ROOT:-dist/release}"' in source
    assert 'publisher_root="${ASSAY_PUBLISHER_ROOT:-dist/publish-tools}"' in source
    assert "${CI:-false}" not in source
    assert "git diff --exit-code" in source
    assert "git status --porcelain=v1 --untracked-files=all" in source


def test_should_describe_only_current_assay_mutation_surfaces() -> None:
    # Given the mutation harness is also the operator-facing map of retained guards
    source = Path("scripts/mutation_harness.py").read_text(encoding="utf-8")
    # Then deleted Avow concepts and obsolete vector-workaround commentary stay absent
    assert "Legacy Avow" not in source
    assert "A one-item file has nothing to drop" not in source


def test_should_keep_registry_jobs_free_of_source_execution_and_broad_secret_scope() -> None:
    # Given the two jobs that can mint a registry identity
    workflow = Path(".github/workflows/publish.yml").read_text(encoding="utf-8")
    document = pytest.importorskip("yaml").safe_load(workflow)
    jobs = document["jobs"]
    # When each privileged job is inspected structurally
    for name in ("publish-python", "publish-npm"):
        job = jobs[name]
        steps = job["steps"]
        source = json.dumps(steps).lower()
        assert "actions/checkout" not in source
        assert not any(
            token in source
            for token in (
                "uv sync",
                "pnpm",
                "npm install",
                "npm ci",
                "pytest",
                "vitest",
                "scripts/",
                "git ",
            )
        )
        assert job["permissions"] == {"actions": "read", "id-token": "write"}
        assert job["environment"] == "npm-release"
        assert "secrets." not in source
    assert [step.get("uses", "").split("@")[0] for step in jobs["publish-python"]["steps"]] == [
        "actions/download-artifact",
        "dagger/dagger-for-github",
        "pypa/gh-action-pypi-publish",
    ]
    npm_source = json.dumps(jobs["publish-npm"]["steps"])
    assert npm_source.count("actions/download-artifact@") == 1
    assert npm_source.count("dagger/dagger-for-github@") == 1
    assert "actions/setup-node@" not in npm_source
    assert "NODE_AUTH_TOKEN" not in npm_source
