"""Re-pin the npm archive digest on Dependabot pull requests.

The packed npm archive embeds ``package.json``, so every dev-tool bump changes its
sha256 even when the shipped code is byte-identical. This tool lets CI do the
re-pin a human used to do by hand, without loosening the pin check:

* ``classify`` compares the base and head archives built by the same Dagger
  ``artifacts`` function. It allows an automatic re-pin only when the member list
  and every shipped byte are unchanged and ``package.json`` differs only in
  ``devDependencies``. Anything else needs a human.
* ``rewrite`` replaces the one pinned digest at the three documented sites.
* ``commit-request`` emits a GraphQL ``createCommitOnBranch`` request bound to the
  exact head commit, so a concurrent Dependabot push makes it fail, not race.

The tests that compare the pinned digest with a fresh build stay as strict as
before: a wrong digest still fails CI.

* ``run`` and ``comment`` are what the Dagger functions ``repin-commit`` and
  ``repin-explain`` execute: they read the pull request and its pin sites as text
  through the GitHub REST API, re-check the Dependabot guard, then commit or comment.

Every job is a base-commit checkout followed by one Dagger call, the only shape the
hseshadr/ci fleet policy accepts; event values reach Dagger through step ``env``.

Security shape of .github/workflows/dependabot-repin.yml (GitHub docs: "Automating
Dependabot with GitHub Actions", "Troubleshooting Dependabot on GitHub Actions",
"Triggering a workflow from a workflow"):

* Triggered by ``pull_request`` only. Dependabot runs get a read-only token and no
  Actions secrets; ``permissions`` raises it per job. No secrets are used.
* Jobs run only for same-repository PRs authored by ``dependabot[bot]``
  (``github.event.pull_request.user.login``), never on the triggering actor.
* ``derive`` runs dependency code (the base module's Dagger ``artifacts`` build of the
  head tree) read-only and hands back one sha256, which ``rewrite`` re-validates. A
  forged digest can only yield a wrong pin, which the unchanged pin tests reject.
* ``repin`` holds the write token and runs no dependency code: this tool comes from
  the base commit, fetches the three pin sites as text, and only edits them, then
  cancels CI on the superseded commit.
  GitHub puts the pull_request run for a GITHUB_TOKEN commit in an approval-required
  state; one "Approve workflows to run" click starts the required Dagger check. Making
  that automatic needs a GitHub App token stored as a Dependabot secret (owner's call).
* ``explain`` comments on PRs that need a human: shipped bytes changed, or a GitHub
  Action SHA pin moved (those pins record a human review and are never automated).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

PIN_SITES: Final = (
    Path("ts/src/packageArtifact.test.ts"),
    Path("tests/test_example.py"),
    Path("tests/test_workflow_contract.py"),
)
MANIFEST: Final = "package/package.json"
SHA256: Final = re.compile(r"[0-9a-f]{64}")
COMMIT_SHA: Final = re.compile(r"[0-9a-f]{40}")
PRIMARY_PIN: Final = re.compile(r'EXPECTED_ARCHIVE_SHA256 =\s*"([0-9a-f]{64})"')
MUTATION: Final = (
    "mutation($input: CreateCommitOnBranchInput!) "
    "{ createCommitOnBranch(input: $input) { commit { oid url } } }"
)
HEADLINE: Final = "test: re-pin npm archive digest for the dev-tool bump"
EXPLANATIONS: Final = {
    "archive": (
        "Not re-pinned automatically: this bump changes shipped bytes of the npm archive "
        "(built code, runtime manifest fields, or the file list), not only devDependencies. "
        "A human must review the archive diff (the `derive` job summary names the changed "
        "members), then update EXPECTED_ARCHIVE_SHA256 in "
        f"{', '.join(map(str, PIN_SITES))}."
    ),
    "actions": (
        "Needs a human re-pin by design: GitHub Action SHA pins record a review of an "
        "external release, so they are never updated automatically. Review the release "
        "diff for each bumped action, then update the reviewed pins in "
        "tests/test_workflow_contract.py and tests/test_workflow_security.py."
    ),
}
BODY: Final = (
    "Automated by .github/workflows/dependabot-repin.yml. The packed archive changed\n"
    "only in package.json devDependencies; every shipped member is byte-identical to\n"
    "main. Digest recomputed by the Dagger `artifacts` function."
)


API: Final = "https://api.github.com"
DEPENDABOT: Final = "dependabot[bot]"
NPM_BRANCH: Final = "dependabot/npm_and_yarn/"
EXPLAINED_BRANCHES: Final = (NPM_BRANCH, "dependabot/github_actions/")
ALREADY_PINNED: Final = "digest already pinned"


class RepinError(ValueError):
    """A refusal: the pins or inputs are not in the shape this tool can trust."""


class GitHub(Protocol):
    """The REST/GraphQL verbs the re-pin and the explanation need."""

    def get_json(self, path: str) -> object: ...

    def get_text(self, path: str) -> str: ...

    def post_json(self, path: str, body: object) -> object: ...


@dataclass(frozen=True)
class PullRequest:
    """The fields of one pull request the guard and the commit depend on."""

    author: str
    head_repository: str
    branch: str
    head_sha: str


@dataclass(frozen=True)
class Verdict:
    """Whether a head archive may be re-pinned without human review."""

    decision: str
    digest: str
    reason: str


def parse_digest(value: str) -> str:
    """Return ``value`` if it is a lowercase hex sha256, else refuse."""
    if SHA256.fullmatch(value) is None:
        raise RepinError(f"{value!r} is not a sha256 digest")
    return value


def _members(archive: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        return {
            member.name: tar.extractfile(member).read()  # type: ignore[union-attr]
            for member in tar.getmembers()
            if member.isfile()
        }


def _runtime_manifest(payload: bytes) -> object:
    manifest = json.loads(payload)
    if isinstance(manifest, dict):
        manifest.pop("devDependencies", None)
    return manifest


def _changed(base: dict[str, bytes], head: dict[str, bytes]) -> list[str]:
    changed = [name for name in sorted(base) if name != MANIFEST and base[name] != head[name]]
    if _runtime_manifest(base[MANIFEST]) != _runtime_manifest(head[MANIFEST]):
        changed.append(MANIFEST)
    return changed


def classify(base_archive: bytes, head_archive: bytes) -> Verdict:
    """Decide whether the head archive differs from base only in devDependencies."""
    digest = hashlib.sha256(head_archive).hexdigest()
    if base_archive == head_archive:
        return Verdict("unchanged", digest, "archive bytes are identical")
    base, head = _members(base_archive), _members(head_archive)
    if set(base) != set(head) or MANIFEST not in base:
        return Verdict("review", digest, "the archive member list changed")
    changed = _changed(base, head)
    if changed:
        return Verdict("review", digest, f"shipped bytes changed: {', '.join(changed)}")
    return Verdict("auto", digest, "only package.json devDependencies changed")


def current_digest(root: Path) -> str:
    """Return the digest pinned at every site, refusing any disagreement."""
    match = PRIMARY_PIN.search((root / PIN_SITES[0]).read_text(encoding="utf-8"))
    if match is None:
        raise RepinError(f"{PIN_SITES[0]} has no pinned digest")
    digest = match.group(1)
    for site in PIN_SITES:
        if (root / site).read_text(encoding="utf-8").count(digest) != 1:
            raise RepinError(f"{site} must contain the pinned digest exactly once")
    return digest


def rewrite(root: Path, digest: str) -> tuple[Path, ...]:
    """Replace the pinned digest at every site; return the sites that changed."""
    new = parse_digest(digest)
    old = current_digest(root)
    if old == new:
        return ()
    for site in PIN_SITES:
        path = root / site
        path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    return PIN_SITES


def _addition(root: Path, path: Path) -> dict[str, str]:
    contents = base64.b64encode((root / path).read_bytes()).decode("ascii")
    return {"path": str(path), "contents": contents}


def commit_request(
    root: Path, repository: str, branch: str, head_sha: str, paths: Sequence[Path]
) -> dict[str, object]:
    """Build a createCommitOnBranch request that only applies on top of ``head_sha``."""
    if COMMIT_SHA.fullmatch(head_sha) is None:
        raise RepinError(f"{head_sha!r} is not a full commit sha")
    commit_input = {
        "branch": {"repositoryNameWithOwner": repository, "branchName": branch},
        "expectedHeadOid": head_sha,
        "message": {"headline": HEADLINE, "body": BODY},
        "fileChanges": {"additions": [_addition(root, path) for path in paths]},
    }
    return {"query": MUTATION, "variables": {"input": commit_input}}


class RestGitHub:
    """A token-bound urllib client for api.github.com."""

    def __init__(self, token: str, base: str = API) -> None:
        if not base.startswith("https://"):
            raise RepinError(f"{base!r} is not an https API base")
        self._token = token
        self._base = base

    def _send(self, path: str, accept: str, body: object = None) -> bytes:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Authorization": f"Bearer {self._token}", "Accept": accept}
        url = self._base + path  # the https-only base is checked in __init__
        request = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return bytes(response.read())

    def get_json(self, path: str) -> object:
        return json.loads(self._send(path, "application/vnd.github+json"))

    def get_text(self, path: str) -> str:
        return self._send(path, "application/vnd.github.raw+json").decode("utf-8")

    def post_json(self, path: str, body: object) -> object:
        raw = self._send(path, "application/vnd.github+json", body)
        return json.loads(raw) if raw.strip() else None


def _field(value: object, *keys: str) -> str:
    for key in keys:
        value = value.get(key) if isinstance(value, Mapping) else None
    if not isinstance(value, str):
        raise RepinError(f"pull request field {'.'.join(keys)} is malformed")
    return value


def _pull_request(
    github: GitHub, repository: str, number: int, branches: tuple[str, ...]
) -> PullRequest:
    pull = github.get_json(f"/repos/{repository}/pulls/{number}")
    found = PullRequest(
        author=_field(pull, "user", "login"),
        head_repository=_field(pull, "head", "repo", "full_name"),
        branch=_field(pull, "head", "ref"),
        head_sha=_field(pull, "head", "sha"),
    )
    _guard(found, repository, branches)
    return found


def _guard(pull: PullRequest, repository: str, branches: tuple[str, ...]) -> None:
    if pull.author != DEPENDABOT:
        raise RepinError(f"pull request is not opened by dependabot: {pull.author}")
    if pull.head_repository != repository:
        raise RepinError(f"head branch is not in {repository}")
    if not pull.branch.startswith(branches):
        raise RepinError(f"{pull.branch} is not a Dependabot npm branch")


def _fetch_sites(github: GitHub, repository: str, head_sha: str, root: Path) -> None:
    for site in PIN_SITES:
        quoted = urllib.parse.quote(site.as_posix())
        text = github.get_text(f"/repos/{repository}/contents/{quoted}?ref={head_sha}")
        (root / site).parent.mkdir(parents=True, exist_ok=True)
        (root / site).write_text(text, encoding="utf-8")


def _commit(github: GitHub, request: Mapping[str, object]) -> None:
    reply = github.post_json("/graphql", request)
    errors = reply.get("errors") if isinstance(reply, Mapping) else "no reply"
    if errors:
        raise RepinError(f"createCommitOnBranch failed: {errors}")


def _runs(listing: object) -> list[Mapping[str, object]]:
    runs = listing.get("workflow_runs", []) if isinstance(listing, Mapping) else []
    return [run for run in runs if isinstance(run, Mapping)]


def _live_runs(listing: object) -> list[int]:
    return [int(str(run["id"])) for run in _runs(listing) if run["status"] != "completed"]


def _cancel_superseded(github: GitHub, repository: str, head_sha: str) -> list[int]:
    path = f"/repos/{repository}/actions/workflows/dagger.yml/runs?head_sha={head_sha}"
    live = _live_runs(github.get_json(path))
    for run_id in live:
        github.post_json(f"/repos/{repository}/actions/runs/{run_id}/cancel", {})
    return live


def repin_pull_request(github: GitHub, repository: str, number: int, digest: str) -> str:
    """Pin ``digest`` on one guarded Dependabot npm pull request; return what happened."""
    parse_digest(digest)
    pull = _pull_request(github, repository, number, (NPM_BRANCH,))
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _fetch_sites(github, repository, pull.head_sha, root)
        changed = rewrite(root, digest)
        if not changed:
            return ALREADY_PINNED
        _commit(github, commit_request(root, repository, pull.branch, pull.head_sha, changed))
    cancelled = _cancel_superseded(github, repository, pull.head_sha)
    return (
        f"re-pinned {len(changed)} sites; cancelled runs {', '.join(map(str, cancelled)) or 'none'}"
    )


def explain_pull_request(github: GitHub, repository: str, number: int, kind: str) -> str:
    """Comment why one guarded Dependabot pull request needs a human re-pin."""
    _pull_request(github, repository, number, EXPLAINED_BRANCHES)
    body = {"body": EXPLANATIONS[kind]}
    github.post_json(f"/repos/{repository}/issues/{number}/comments", body)
    return f"explained {kind} re-pin on #{number}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Re-pin the npm archive digest.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("explain").add_argument("kind", choices=sorted(EXPLANATIONS))
    _add_pin_commands(commands)
    return parser


def _add_pin_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    verdict = commands.add_parser("classify")
    verdict.add_argument("--json", action="store_true")
    verdict.add_argument("base", type=Path)
    verdict.add_argument("head", type=Path)
    pins = commands.add_parser("rewrite")
    pins.add_argument("--root", type=Path, required=True)
    pins.add_argument("--digest", required=True)
    commit = commands.add_parser("commit-request")
    for flag in ("--root", "--repository", "--branch", "--head-sha"):
        commit.add_argument(flag, required=True)
    commit.add_argument("paths", nargs="+", type=Path)
    _add_api_commands(commands)


def _add_api_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    api_commands: tuple[tuple[str, str, list[str] | None], ...] = (
        ("run", "--digest", None),
        ("comment", "--kind", sorted(EXPLANATIONS)),
    )
    for name, flag, choices in api_commands:
        api = commands.add_parser(name)
        api.add_argument("--repository", required=True)
        api.add_argument("--pr", type=int, required=True)
        api.add_argument(flag, required=True, choices=choices)


def _classify_lines(arguments: argparse.Namespace) -> list[str]:
    verdict = classify(arguments.base.read_bytes(), arguments.head.read_bytes())
    if arguments.json:
        return [json.dumps(verdict.__dict__)]
    return [f"decision={verdict.decision}", f"digest={verdict.digest}", f"reason={verdict.reason}"]


def _rewrite_lines(arguments: argparse.Namespace) -> list[str]:
    return [str(path) for path in rewrite(arguments.root, arguments.digest)]


def _commit_lines(arguments: argparse.Namespace) -> list[str]:
    root, repository, branch = Path(arguments.root), arguments.repository, arguments.branch
    request = commit_request(root, repository, branch, arguments.head_sha, arguments.paths)
    return [json.dumps(request)]


def _explain_lines(arguments: argparse.Namespace) -> list[str]:
    return [EXPLANATIONS[arguments.kind]]


def _token() -> RestGitHub:
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise RepinError("GH_TOKEN is not set")
    return RestGitHub(token)


def _run_lines(arguments: argparse.Namespace) -> list[str]:
    github, repository = _token(), arguments.repository
    return [repin_pull_request(github, repository, arguments.pr, arguments.digest)]


def _comment_lines(arguments: argparse.Namespace) -> list[str]:
    github, repository = _token(), arguments.repository
    return [explain_pull_request(github, repository, arguments.pr, arguments.kind)]


COMMANDS: Final = {
    "run": _run_lines,
    "comment": _comment_lines,
    "explain": _explain_lines,
    "classify": _classify_lines,
    "rewrite": _rewrite_lines,
    "commit-request": _commit_lines,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Run one subcommand; print refusals to stderr and exit 1."""
    try:
        arguments = _parser().parse_args(argv)
        lines = COMMANDS[arguments.command](arguments)
    except RepinError as error:
        print(f"repin refused: {error}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
