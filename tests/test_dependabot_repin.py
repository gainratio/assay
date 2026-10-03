"""Behavior of the Dependabot npm archive digest re-pin tool."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from scripts.repin_npm_archive import (
    PIN_SITES,
    RepinError,
    classify,
    commit_request,
    current_digest,
    main,
    parse_digest,
    rewrite,
)

OLD = "a" * 64
NEW = "b" * 64
MANIFEST = {"name": "@gainratio/assay", "version": "1.0.0", "devDependencies": {"vitest": "4"}}


def _archive(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _package(manifest: dict[str, object], dist: bytes = b"export {};\n") -> bytes:
    return _archive(
        {
            "package/package.json": json.dumps(manifest).encode(),
            "package/dist/index.js": dist,
        }
    )


def _pinned(digest: str) -> str:
    return f'EXPECTED_ARCHIVE_SHA256 = "{digest}"\nOTHER = 1\n'


def _repository(tmp_path: Path, digest: str = OLD) -> Path:
    for site in PIN_SITES:
        path = tmp_path / site
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_pinned(digest), encoding="utf-8")
    return tmp_path


def test_should_pin_exactly_the_three_documented_digest_sites() -> None:
    assert tuple(map(str, PIN_SITES)) == (
        "ts/src/packageArtifact.test.ts",
        "tests/test_example.py",
        "tests/test_workflow_contract.py",
    )


def test_should_find_the_real_repository_digest_at_every_site() -> None:
    assert len(current_digest(Path(__file__).parents[1])) == 64


@pytest.mark.parametrize("value", ["", "A" * 64, "a" * 63, "a" * 65, "g" * 64, f"{OLD}\n"])
def test_should_refuse_anything_but_a_lowercase_sha256(value: str) -> None:
    with pytest.raises(RepinError, match="not a sha256"):
        parse_digest(value)


def test_should_accept_a_lowercase_sha256() -> None:
    assert parse_digest(NEW) == NEW


def test_should_report_unchanged_when_archives_are_identical() -> None:
    archive = _package(MANIFEST)

    verdict = classify(archive, archive)

    assert (verdict.decision, verdict.reason) == ("unchanged", "archive bytes are identical")


def test_should_auto_repin_when_only_dev_dependencies_moved() -> None:
    bumped = MANIFEST | {"devDependencies": {"vitest": "5"}}
    head = _package(bumped)

    verdict = classify(_package(MANIFEST), head)

    assert verdict.decision == "auto"
    assert verdict.digest == hashlib.sha256(head).hexdigest()


def test_should_require_review_when_shipped_code_changes() -> None:
    verdict = classify(_package(MANIFEST), _package(MANIFEST, b"export const x = 1;\n"))

    assert verdict.decision == "review"
    assert "package/dist/index.js" in verdict.reason


def test_should_require_review_when_runtime_manifest_fields_change() -> None:
    changed = MANIFEST | {"dependencies": {"left-pad": "1"}}

    verdict = classify(_package(MANIFEST), _package(changed))

    assert verdict.decision == "review"
    assert "package/package.json" in verdict.reason


def test_should_require_review_when_the_member_list_changes() -> None:
    head = _archive({"package/package.json": json.dumps(MANIFEST).encode()})

    verdict = classify(_package(MANIFEST), head)

    assert verdict.decision == "review"
    assert "member list" in verdict.reason


def test_should_rewrite_every_site_and_nothing_else(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    changed = rewrite(root, NEW)

    assert changed == PIN_SITES
    for site in PIN_SITES:
        assert (root / site).read_text(encoding="utf-8") == _pinned(NEW)


def test_should_change_nothing_when_the_digest_is_already_pinned(tmp_path: Path) -> None:
    assert rewrite(_repository(tmp_path), OLD) == ()


def test_should_refuse_sites_that_disagree(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / PIN_SITES[2]).write_text(f'PIN = "{"c" * 64}"\n', encoding="utf-8")

    with pytest.raises(RepinError, match=re.escape("tests/test_workflow_contract.py")):
        rewrite(root, NEW)


def test_should_refuse_a_site_with_a_repeated_pin(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / PIN_SITES[1]).write_text(f'A = "{OLD}"\nB = "{OLD}"\n', encoding="utf-8")

    with pytest.raises(RepinError, match="exactly once"):
        rewrite(root, NEW)


def test_should_refuse_a_primary_site_without_a_pin(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / PIN_SITES[0]).write_text("nothing here\n", encoding="utf-8")

    with pytest.raises(RepinError, match="no pinned digest"):
        current_digest(root)


def test_should_build_a_head_bound_signed_commit_request(tmp_path: Path) -> None:
    root = _repository(tmp_path, NEW)

    request = commit_request(root, "hseshadr/assay", "dependabot/x", "f" * 40, PIN_SITES)

    variables = request["variables"]
    assert isinstance(variables, dict)
    payload = variables["input"]
    assert payload["expectedHeadOid"] == "f" * 40
    assert payload["branch"] == {
        "repositoryNameWithOwner": "hseshadr/assay",
        "branchName": "dependabot/x",
    }
    additions = payload["fileChanges"]["additions"]
    assert [item["path"] for item in additions] == list(map(str, PIN_SITES))
    decoded = base64.b64decode(additions[0]["contents"]).decode()
    assert decoded == _pinned(NEW)
    assert "createCommitOnBranch" in str(request["query"])


def test_should_refuse_a_head_that_is_not_a_full_commit_sha(tmp_path: Path) -> None:
    with pytest.raises(RepinError, match="commit sha"):
        commit_request(_repository(tmp_path), "hseshadr/assay", "b", "main", PIN_SITES)


def test_should_print_the_verdict_as_step_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base, head = tmp_path / "base.tgz", tmp_path / "head.tgz"
    base.write_bytes(_package(MANIFEST))
    head.write_bytes(_package(MANIFEST | {"devDependencies": {}}))

    assert main(["classify", str(base), str(head)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "decision=auto"
    assert lines[1].startswith("digest=")


def test_should_rewrite_and_list_changed_sites_from_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repository(tmp_path)

    assert main(["rewrite", "--root", str(root), "--digest", NEW]) == 0

    assert capsys.readouterr().out.splitlines() == list(map(str, PIN_SITES))


def test_should_emit_the_commit_request_as_json_from_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repository(tmp_path)
    arguments = ["commit-request", "--root", str(root), "--repository", "hseshadr/assay"]

    assert main([*arguments, "--branch", "b", "--head-sha", "e" * 40, *map(str, PIN_SITES)]) == 0

    assert json.loads(capsys.readouterr().out)["variables"]["input"]["expectedHeadOid"] == "e" * 40


def test_should_fail_closed_with_a_message_on_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["rewrite", "--root", str(_repository(tmp_path)), "--digest", "nope"]) == 1

    assert "not a sha256" in capsys.readouterr().err


def test_should_require_review_when_a_non_object_manifest_changes() -> None:
    base = _archive({"package/package.json": b"[1]", "package/dist/index.js": b""})
    head = _archive({"package/package.json": b"[2]", "package/dist/index.js": b""})

    assert classify(base, head).decision == "review"
