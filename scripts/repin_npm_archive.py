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
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import sys
import tarfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

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
BODY: Final = (
    "Automated by .github/workflows/dependabot-repin.yml. The packed archive changed\n"
    "only in package.json devDependencies; every shipped member is byte-identical to\n"
    "main. Digest recomputed by the Dagger `artifacts` function."
)


class RepinError(ValueError):
    """A refusal: the pins or inputs are not in the shape this tool can trust."""


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    verdict = commands.add_parser("classify")
    verdict.add_argument("base", type=Path)
    verdict.add_argument("head", type=Path)
    pins = commands.add_parser("rewrite")
    pins.add_argument("--root", type=Path, required=True)
    pins.add_argument("--digest", required=True)
    commit = commands.add_parser("commit-request")
    for flag in ("--root", "--repository", "--branch", "--head-sha"):
        commit.add_argument(flag, required=True)
    commit.add_argument("paths", nargs="+", type=Path)
    return parser


def _classify_lines(arguments: argparse.Namespace) -> list[str]:
    verdict = classify(arguments.base.read_bytes(), arguments.head.read_bytes())
    return [f"decision={verdict.decision}", f"digest={verdict.digest}", f"reason={verdict.reason}"]


def _rewrite_lines(arguments: argparse.Namespace) -> list[str]:
    return [str(path) for path in rewrite(arguments.root, arguments.digest)]


def _commit_lines(arguments: argparse.Namespace) -> list[str]:
    root, repository, branch = Path(arguments.root), arguments.repository, arguments.branch
    request = commit_request(root, repository, branch, arguments.head_sha, arguments.paths)
    return [json.dumps(request)]


COMMANDS: Final = {
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
