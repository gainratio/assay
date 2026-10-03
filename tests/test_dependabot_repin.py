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


@pytest.mark.parametrize(
    ("kind", "names"),
    [
        ("archive", ("EXPECTED_ARCHIVE_SHA256", *map(str, PIN_SITES))),
        ("actions", ("tests/test_workflow_contract.py", "tests/test_workflow_security.py")),
    ],
)
def test_should_explain_each_manual_repin_and_where_to_make_it(
    kind: str, names: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    # Given / When
    status = main(["explain", kind])

    # Then
    message = capsys.readouterr().out
    assert status == 0
    assert "human" in message
    assert all(name in message for name in names)


def test_should_refuse_an_unknown_explanation() -> None:
    with pytest.raises(SystemExit):
        main(["explain", "other"])


import scripts.repin_npm_archive as tool  # noqa: E402

HEAD = "e" * 40
REPOSITORY = "hseshadr/assay"


class FakeGitHub:
    """Serves one pull request and its head pin sites; records every write."""

    def __init__(self, root: Path, pull: dict[str, object], runs: list[object]) -> None:
        self.root = root
        self.pull = pull
        self.runs = runs
        self.posts: list[tuple[str, object]] = []

    def get_json(self, path: str) -> object:
        if path == f"/repos/{REPOSITORY}/pulls/7":
            return self.pull
        assert path == f"/repos/{REPOSITORY}/actions/workflows/dagger.yml/runs?head_sha={HEAD}"
        return {"workflow_runs": self.runs}

    def get_text(self, path: str) -> str:
        prefix, _, ref = path.partition("?ref=")
        assert ref == HEAD
        site = prefix.removeprefix(f"/repos/{REPOSITORY}/contents/")
        return (self.root / site).read_text(encoding="utf-8")

    def post_json(self, path: str, body: object) -> object:
        self.posts.append((path, body))
        return {"data": {"createCommitOnBranch": {"commit": {"oid": "f" * 40}}}}


def _pull(
    login: str = "dependabot[bot]",
    repo: str = REPOSITORY,
    ref: object = "dependabot/npm_and_yarn/x",
) -> dict[str, object]:
    head = {"ref": ref, "sha": HEAD, "repo": {"full_name": repo}}
    return {"user": {"login": login}, "head": head}


def _github(tmp_path: Path, pull: dict[str, object], runs: list[object]) -> FakeGitHub:
    (tmp_path / "head").mkdir()
    return FakeGitHub(_repository(tmp_path / "head"), pull, runs)


def test_should_commit_the_new_digest_bound_to_the_head_and_cancel_its_ci(
    tmp_path: Path,
) -> None:
    # Given
    runs = [{"id": 11, "status": "queued"}, {"id": 12, "status": "completed"}]
    github = _github(tmp_path, _pull(), runs)

    # When
    outcome = tool.repin_pull_request(github, REPOSITORY, 7, NEW)

    # Then
    (graphql, request), cancel = github.posts
    commit_input = request["variables"]["input"]  # type: ignore[index]
    assert graphql == "/graphql"
    assert commit_input["expectedHeadOid"] == HEAD
    assert commit_input["branch"]["branchName"] == "dependabot/npm_and_yarn/x"
    additions = commit_input["fileChanges"]["additions"]
    assert [item["path"] for item in additions] == [str(site) for site in PIN_SITES]
    assert {base64.b64decode(item["contents"]).decode() for item in additions} == {_pinned(NEW)}
    assert cancel == (f"/repos/{REPOSITORY}/actions/runs/11/cancel", {})
    assert outcome == "re-pinned 3 sites; cancelled runs 11"


def test_should_write_nothing_when_the_head_already_pins_the_digest(tmp_path: Path) -> None:
    # Given
    github = _github(tmp_path, _pull(), [])

    # When
    outcome = tool.repin_pull_request(github, REPOSITORY, 7, OLD)

    # Then
    assert github.posts == []
    assert outcome == "digest already pinned"


@pytest.mark.parametrize(
    ("pull", "reason"),
    [
        (_pull(login="octocat"), "not opened by dependabot"),
        (_pull(repo="fork/assay"), "not in hseshadr/assay"),
        (_pull(ref="dependabot/uv/x"), "not a Dependabot npm branch"),
        (_pull(ref=7), "malformed"),
    ],
)
def test_should_refuse_any_pull_request_outside_the_dependabot_npm_guard(
    tmp_path: Path, pull: dict[str, object], reason: str
) -> None:
    # Given
    github = _github(tmp_path, pull, [])

    # When / Then
    with pytest.raises(RepinError, match=reason):
        tool.repin_pull_request(github, REPOSITORY, 7, NEW)
    assert github.posts == []


def test_should_refuse_a_forged_digest_before_reading_the_pull_request(tmp_path: Path) -> None:
    # Given
    github = _github(tmp_path, _pull(), [])

    # When / Then
    with pytest.raises(RepinError, match="sha256"):
        tool.repin_pull_request(github, REPOSITORY, 7, "$(id)")
    assert github.posts == []


def test_should_refuse_a_graphql_error_before_cancelling_anything(tmp_path: Path) -> None:
    # Given
    github = _github(tmp_path, _pull(), [{"id": 11, "status": "queued"}])
    replies: list[object] = []

    def failing_post(path: str, body: object) -> object:
        replies.append(path)
        return {"errors": [{"message": "head moved"}]}

    github.post_json = failing_post  # type: ignore[method-assign]

    # When / Then
    with pytest.raises(RepinError, match="head moved"):
        tool.repin_pull_request(github, REPOSITORY, 7, NEW)
    assert replies == ["/graphql"]


@pytest.mark.parametrize(
    ("ref", "kind"),
    [("dependabot/npm_and_yarn/x", "archive"), ("dependabot/github_actions/x", "actions")],
)
def test_should_comment_the_explanation_on_a_guarded_pull_request(
    tmp_path: Path, ref: str, kind: str
) -> None:
    # Given
    github = _github(tmp_path, _pull(ref=ref), [])

    # When
    outcome = tool.explain_pull_request(github, REPOSITORY, 7, kind)

    # Then
    assert github.posts == [
        (f"/repos/{REPOSITORY}/issues/7/comments", {"body": tool.EXPLANATIONS[kind]})
    ]
    assert outcome == f"explained {kind} re-pin on #7"


def test_should_refuse_to_comment_on_a_non_dependabot_pull_request(tmp_path: Path) -> None:
    # Given
    github = _github(tmp_path, _pull(login="octocat"), [])

    # When / Then
    with pytest.raises(RepinError, match="not opened by dependabot"):
        tool.explain_pull_request(github, REPOSITORY, 7, "archive")
    assert github.posts == []


def test_should_classify_as_json_for_the_workflow_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    base, head = tmp_path / "base.tgz", tmp_path / "head.tgz"
    base.write_bytes(_package(MANIFEST))
    head.write_bytes(_package({**MANIFEST, "devDependencies": {"vitest": "5"}}))

    # When
    status = main(["classify", "--json", str(base), str(head)])

    # Then
    assert status == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["decision"] == "auto"
    assert verdict["digest"] == hashlib.sha256(head.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "--repository", REPOSITORY, "--pr", "7", "--digest", NEW],
        ["comment", "--repository", REPOSITORY, "--pr", "7", "--kind", "archive"],
    ],
)
def test_should_refuse_to_call_github_without_a_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    # Given
    monkeypatch.delenv("GH_TOKEN", raising=False)

    # When
    status = main(argv)

    # Then
    assert status == 1
    assert "GH_TOKEN" in capsys.readouterr().err


def test_should_run_and_comment_against_the_rest_api_with_the_environment_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    seen: list[object] = []
    monkeypatch.setenv("GH_TOKEN", "t0ken")
    monkeypatch.setattr(tool, "repin_pull_request", lambda *a: seen.append(a) or "repinned")
    monkeypatch.setattr(tool, "explain_pull_request", lambda *a: seen.append(a) or "explained")

    # When
    statuses = [
        main(["run", "--repository", REPOSITORY, "--pr", "7", "--digest", NEW]),
        main(["comment", "--repository", REPOSITORY, "--pr", "7", "--kind", "actions"]),
    ]

    # Then
    assert statuses == [0, 0]
    assert capsys.readouterr().out.split() == ["repinned", "explained"]
    assert [call[1:] for call in seen] == [(REPOSITORY, 7, NEW), (REPOSITORY, 7, "actions")]  # type: ignore[index]
    assert all(isinstance(call[0], tool.RestGitHub) for call in seen)  # type: ignore[index]


class _Reply:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_should_send_bearer_requests_with_the_matching_media_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    sent: list[tuple[str, str | None, str | None, bytes | None]] = []
    replies = iter([b'{"a": 1}', b"raw text", b"", b'{"data": {}}'])

    def fake_urlopen(request: object, timeout: int) -> _Reply:
        sent.append(
            (
                request.full_url,  # type: ignore[attr-defined]
                request.get_header("Authorization"),  # type: ignore[attr-defined]
                request.get_header("Accept"),  # type: ignore[attr-defined]
                request.data,  # type: ignore[attr-defined]
            )
        )
        return _Reply(next(replies))

    monkeypatch.setattr(tool.urllib.request, "urlopen", fake_urlopen)
    github = tool.RestGitHub("t0ken", "https://api.test")

    # When
    results = [
        github.get_json("/j"),
        github.get_text("/t"),
        github.post_json("/c", {}),
        github.post_json("/g", {"q": 1}),
    ]

    # Then
    assert results == [{"a": 1}, "raw text", None, {"data": {}}]
    assert [item[0] for item in sent] == [f"https://api.test{p}" for p in ("/j", "/t", "/c", "/g")]
    assert {item[1] for item in sent} == {"Bearer t0ken"}
    assert sent[1][2] == "application/vnd.github.raw+json"
    assert sent[3][3] == b'{"q": 1}'


@pytest.mark.parametrize("base", ["file:///etc", "http://api.github.com"])
def test_should_refuse_an_api_base_that_is_not_https(base: str) -> None:
    with pytest.raises(RepinError, match="https"):
        tool.RestGitHub("t0ken", base)
