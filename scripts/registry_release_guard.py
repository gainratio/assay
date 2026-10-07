"""Fail-closed registry preflight for retry-safe trusted publication."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from scripts.verify_release_artifacts import verify_release_bundle

_PROVENANCE_TYPE = "https://slsa.dev/provenance/v1"
_PYPI_PUBLISH_TYPE = "https://docs.pypi.org/attestations/publish/v1"
_NPM_ATTESTATION_ROOT = "https://registry.npmjs.org/-/npm/v1/attestations/"
_PYTHON_FILE_COUNT = 2
_NOT_FOUND = 404
_CORE = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
_NPM_VERSION = re.compile(rf"^{_CORE}(?:-dev\.(0|[1-9]\d*))?$")
# Exactly the two names this repository publishes from (hseshadr -> gainratio org move).
# Registry provenance names the publishing repository; releases from either owner verify.
_PYPI_REPOSITORIES = ("hseshadr/assay", "gainratio/assay")
_REPOSITORIES = tuple(f"https://github.com/{name}" for name in _PYPI_REPOSITORIES)
# npm makes a package's first version `latest` whatever `--tag` says, so a freshly claimed
# name has this trusted-publishing stub on `latest`. It is no release on any channel.
BOOTSTRAP_VERSION = "0.0.0-bootstrap.0"
_WORKFLOW = ".github/workflows/publish.yml"
_PUBLISH_REF = "refs/heads/main"
_FETCH_ATTEMPTS = 3
_METADATA_LIMIT = 1_048_576


class _Headers(Protocol):
    def get(self, name: str) -> str | None: ...


class _ReadableResponse(Protocol):
    headers: _Headers

    def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True)
class ProvenanceIdentity:
    """Exact hosted release context required by npm provenance."""

    tag: str
    sha: str
    subject_sha512: str = ""


@dataclass(frozen=True)
class ReleaseDecision:
    """One registry lane's publish and npm-channel decision."""

    publish: bool
    channel: str | None = None
    publish_tag: str | None = None
    channel_version: str | None = None
    publish_tag_version: str | None = None


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("registry metadata is malformed")
    return cast(dict[str, object], value)


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("registry metadata is malformed")
    return value


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _integrity(path: Path) -> str:
    digest = hashlib.sha512(path.read_bytes()).digest()
    return f"sha512-{base64.b64encode(digest).decode()}"


def _local_python_digests(root: Path) -> dict[str, str]:
    files = tuple(sorted(path for path in root.iterdir() if path.is_file()))
    if len(files) != _PYTHON_FILE_COUNT:
        raise ValueError("Python release artifact count mismatch")
    return {path.name: _digest(path, "sha256") for path in files}


def _pypi_digests(payload: object) -> dict[str, str]:
    urls = _sequence(_mapping(payload).get("urls"))
    digests: dict[str, str] = {}
    for item in urls:
        record = _mapping(item)
        filename = str(record.get("filename"))
        if filename in digests:
            raise ValueError("duplicate PyPI filename")
        digests[filename] = str(_mapping(record.get("digests")).get("sha256"))
    return digests


def pypi_release_state(root: Path, payload: object | None, provenance: set[str]) -> bool:
    """Return whether PyPI needs publishing; reject any existing drift."""
    if payload is None:
        return True
    expected = _local_python_digests(root)
    if _pypi_digests(payload) != expected or provenance != set(expected):
        raise ValueError("PyPI artifact or provenance mismatch")
    return False


def _npm_attestation_url(payload: object) -> str:
    dist = _mapping(_mapping(payload).get("dist"))
    attestations = _mapping(dist.get("attestations"))
    provenance = _mapping(attestations.get("provenance"))
    if provenance.get("predicateType") != _PROVENANCE_TYPE:
        raise ValueError("npm artifact or provenance mismatch")
    url = attestations.get("url")
    if not isinstance(url, str) or not url.startswith(_NPM_ATTESTATION_ROOT):
        raise ValueError("npm artifact or provenance mismatch")
    return url


def provenance_payload_valid(payload: object | None) -> bool:
    """Recognize nonempty PyPI or npm provenance endpoint payloads."""
    if payload is None:
        return False
    metadata = _mapping(payload)
    collections = (metadata.get("attestations"), metadata.get("attestation_bundles"))
    return any(isinstance(items, list) and bool(items) for items in collections)


def _pypi_statement(attestation: object) -> dict[str, object]:
    envelope = _mapping(_mapping(attestation).get("envelope"))
    encoded = envelope.get("statement")
    if not isinstance(encoded, str):
        raise ValueError("PyPI artifact or provenance mismatch")
    return _mapping(json.loads(base64.b64decode(encoded, validate=True)))


def _subject_matches(
    statement: dict[str, object], filename: str, algorithm: str, digest: str
) -> bool:
    subjects = (_mapping(item) for item in _sequence(statement.get("subject")))
    expected = {algorithm: digest}
    return any(
        item.get("name") == filename and _mapping(item.get("digest")) == expected
        for item in subjects
    )


def _pypi_bundle_valid(bundle: object, filename: str, digest: str) -> bool:
    metadata = _mapping(bundle)
    publisher = _mapping(metadata.get("publisher"))
    expected = ("GitHub", Path(_WORKFLOW).name)
    actual = (publisher.get("kind"), publisher.get("workflow"))
    if actual != expected or publisher.get("repository") not in _PYPI_REPOSITORIES:
        return False
    attestations = _sequence(metadata.get("attestations"))
    return any(_pypi_attestation_valid(item, filename, digest) for item in attestations)


def _pypi_attestation_valid(attestation: object, filename: str, digest: str) -> bool:
    statement = _pypi_statement(attestation)
    return (
        statement.get("_type") == "https://in-toto.io/Statement/v1"
        and statement.get("predicateType") == _PYPI_PUBLISH_TYPE
        and statement.get("predicate") is None
        and _subject_matches(statement, filename, "sha256", digest)
    )


def pypi_provenance_valid(payload: object | None, filename: str, digest: str) -> bool:
    """Validate a PEP 740 file statement and exact trusted-publisher identity."""
    if payload is None:
        return False
    bundles = _sequence(_mapping(payload).get("attestation_bundles"))
    return any(_pypi_bundle_valid(bundle, filename, digest) for bundle in bundles)


def _statement(attestation: object) -> dict[str, object]:
    bundle = _mapping(_mapping(attestation).get("bundle"))
    envelope = _mapping(bundle.get("dsseEnvelope"))
    encoded = envelope.get("payload")
    if not isinstance(encoded, str):
        raise ValueError("npm artifact or provenance mismatch")
    return _mapping(json.loads(base64.b64decode(encoded, validate=True)))


def _workflow_matches(statement: dict[str, object]) -> bool:
    predicate = _mapping(statement.get("predicate"))
    definition = _mapping(predicate.get("buildDefinition"))
    external = _mapping(definition.get("externalParameters"))
    workflow = _mapping(external.get("workflow"))
    return (
        workflow.get("repository") in _REPOSITORIES
        and workflow.get("path") == _WORKFLOW
        and workflow.get("ref") == _PUBLISH_REF
    )


def _workflow_repository(statement: dict[str, object]) -> object:
    definition = _mapping(_mapping(statement.get("predicate")).get("buildDefinition"))
    workflow = _mapping(_mapping(definition.get("externalParameters")).get("workflow"))
    return workflow.get("repository")


def _dependency_matches(value: object, identity: ProvenanceIdentity, repository: object) -> bool:
    dependency = _mapping(value)
    expected_uri = f"git+{repository}@{_PUBLISH_REF}"
    return dependency.get("uri") == expected_uri and _mapping(dependency.get("digest")) == {
        "gitCommit": identity.sha
    }


def _statement_header_matches(statement: dict[str, object], identity: ProvenanceIdentity) -> bool:
    return (
        statement.get("_type") == "https://in-toto.io/Statement/v1"
        and statement.get("predicateType") == _PROVENANCE_TYPE
        and _workflow_matches(statement)
    )


def _npm_subject_matches(statement: dict[str, object], identity: ProvenanceIdentity) -> bool:
    expected = f"pkg:npm/%40gainratio/assay@{identity.tag.removeprefix('v')}"
    subjects = (_mapping(item) for item in _sequence(statement.get("subject")))
    matching = (item for item in subjects if item.get("name") == expected)
    return any(
        _mapping(item.get("digest")).get("sha512") == identity.subject_sha512 for item in matching
    )


def _resolved_dependency_matches(
    statement: dict[str, object], identity: ProvenanceIdentity
) -> bool:
    definition = _mapping(_mapping(statement.get("predicate")).get("buildDefinition"))
    dependencies = _sequence(definition.get("resolvedDependencies"))
    repository = _workflow_repository(statement)
    return any(_dependency_matches(item, identity, repository) for item in dependencies)


def _statement_matches(statement: dict[str, object], identity: ProvenanceIdentity) -> bool:
    return (
        _statement_header_matches(statement, identity)
        and _npm_subject_matches(statement, identity)
        and _resolved_dependency_matches(statement, identity)
    )


def _npm_provenance_valid(payload: object | None, identity: ProvenanceIdentity) -> bool:
    if payload is None:
        return False
    attestations = _sequence(_mapping(payload).get("attestations"))
    return any(_statement_matches(_statement(item), identity) for item in attestations)


def npm_dist_tag(version: str) -> str:
    """Map exact stable and prerelease SemVer versions to safe npm channels."""
    if _NPM_VERSION.fullmatch(version) is None:
        raise ValueError("npm release version is not valid SemVer")
    return "next" if "-" in version else "latest"


def _version_key(version: str) -> tuple[int, int, int, int, int]:
    match = _NPM_VERSION.fullmatch(version)
    if match is None:
        raise ValueError("npm release version is not valid SemVer")
    major, minor, patch, dev = match.groups()
    return int(major), int(minor), int(patch), 1 if dev is None else 0, int(dev or 0)


def checked_npm_dist_tag(version: str, current: str | None) -> str:
    """Choose the release channel without allowing a historical rollback."""
    channel = npm_dist_tag(version)
    if current is not None and _version_key(version) < _version_key(current):
        raise ValueError("npm dist-tag would move backward")
    return channel


def dist_tag_is_current_or_newer(version: str, current: str) -> bool:
    """Accept the exact release or a newer release on the same npm channel."""
    return npm_dist_tag(version) == npm_dist_tag(current) and _version_key(current) >= _version_key(
        version
    )


def version_specific_tag(version: str) -> str:
    """Return a deterministic non-default tag for a historical release."""
    _version_key(version)
    return f"assay-v{version.replace('.', '-')}"


def _stable_release_exists(tags: dict[str, object]) -> bool:
    latest = tags.get("latest")
    if latest is None or latest == BOOTSTRAP_VERSION:
        return False
    if not isinstance(latest, str):
        raise ValueError("npm registry metadata is malformed")
    return npm_dist_tag(latest) == "latest"


def npm_release_channel(version: str, tags: dict[str, object]) -> str:
    """Pick the channel a release moves: prereleases own `latest` until a stable one exists.

    Stable releases always go to `latest`, so `latest` naming a stable version is the
    signal that one exists. Before that, a prerelease on `next` would leave a plain
    `npm install` on an older release (or the bootstrap stub) until someone retags by hand.
    """
    channel = npm_dist_tag(version)
    if channel == "next" and not _stable_release_exists(tags):
        return "latest"
    return channel


def _channel_version(version: str, tags: dict[str, object]) -> str | None:
    channel = npm_release_channel(version, tags)
    current = tags.get(channel)
    if current is None or current == BOOTSTRAP_VERSION:
        return None
    if not isinstance(current, str):
        raise ValueError("npm registry metadata is malformed")
    _require_compatible_channel(channel, current)
    return current


def _require_compatible_channel(channel: str, current: str) -> None:
    # `latest` may hold a prerelease (no stable release yet); `next` never holds a stable one.
    if channel == "next" and npm_dist_tag(current) != channel:
        raise ValueError("npm channel contains an incompatible version")


def _checked_historical_tag(version: str, tags: dict[str, object]) -> str:
    historical = version_specific_tag(version)
    if tags.get(historical) not in (None, version):
        raise ValueError("npm version-specific dist-tag collision")
    return historical


def npm_publish_tag(version: str, tags: dict[str, object]) -> str:
    """Select the default channel unless doing so would move it backward."""
    channel = npm_release_channel(version, tags)
    current = _channel_version(version, tags)
    if current is None or _version_key(current) <= _version_key(version):
        return channel
    return _checked_historical_tag(version, tags)


def _npm_bytes_match(path: Path, payload: object) -> bool:
    dist = _mapping(_mapping(payload).get("dist"))
    expected = (_digest(path, "sha1"), _integrity(path))
    return (dist.get("shasum"), dist.get("integrity")) == expected


def _npm_attestation_matches(
    attestation: object | None, identity: ProvenanceIdentity | None
) -> bool:
    if identity is None:
        return provenance_payload_valid(attestation)
    return _npm_provenance_valid(attestation, identity)


def npm_release_state(
    path: Path,
    payload: object | None,
    attestation: object | None,
    identity: ProvenanceIdentity | None = None,
) -> bool:
    """Return whether npm needs publishing; reject any existing drift."""
    if payload is None:
        return True
    if not _npm_bytes_match(path, payload):
        raise ValueError("npm artifact or provenance mismatch")
    _npm_attestation_url(payload)
    if not _npm_attestation_matches(attestation, identity):
        raise ValueError("npm artifact or provenance mismatch")
    return False


def _declared_length(headers: _Headers) -> int | None:
    raw = headers.get("Content-Length")
    if raw is None:
        return None
    try:
        length = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("registry metadata length is malformed") from error
    if length < 0:
        raise ValueError("registry metadata length is malformed")
    return length


def _bounded_response(response: _ReadableResponse, limit: int) -> bytes:
    declared = _declared_length(response.headers)
    if declared is not None and declared > limit:
        raise ValueError("registry metadata exceeds limit")
    payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("registry metadata exceeds limit")
    return payload


def _read_json(url: str, timeout: float) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        payload = cast(object, json.loads(_bounded_response(response, _METADATA_LIMIT)))
    if payload is None:
        raise ValueError("registry metadata is malformed")
    return payload


def _fetch_once(url: str, deadline: float) -> object | None:
    remaining = deadline - time.monotonic()
    if remaining <= 0.0:
        raise TimeoutError("registry request deadline exhausted")
    try:
        return _read_json(url, min(15.0, remaining))
    except urllib.error.HTTPError as error:
        if error.code == _NOT_FOUND:
            return None
        raise


def _pause_before_retry(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0.0:
        raise TimeoutError("registry request deadline exhausted")
    time.sleep(min(1.0, remaining))


def _fetch_json(url: str) -> object | None:
    if not url.startswith("https://"):
        raise ValueError("registry URL must use HTTPS")
    deadline = time.monotonic() + 30.0
    for attempt in range(_FETCH_ATTEMPTS):
        try:
            return _fetch_once(url, deadline)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            if attempt + 1 == _FETCH_ATTEMPTS:
                raise
        _pause_before_retry(deadline)
    raise RuntimeError("registry retry loop exhausted")


def _pypi_provenance(version: str, digests: dict[str, str]) -> set[str]:
    root = f"https://pypi.org/integrity/assay-engine/{version}"
    proven = set()
    for filename, digest in digests.items():
        encoded = urllib.parse.quote(filename, safe="")
        payload = _fetch_json(f"{root}/{encoded}/provenance")
        if pypi_provenance_valid(payload, filename, digest):
            proven.add(filename)
    return proven


def _pypi_state(root: Path, version: str) -> bool:
    python, _npm = verify_release_bundle(root.parent)
    if python.version != version:
        raise ValueError("PyPI artifact version mismatch")
    url = f"https://pypi.org/pypi/assay-engine/{version}/json"
    payload = _fetch_json(url)
    digests = _local_python_digests(root) if payload is not None else {}
    return pypi_release_state(root, payload, _pypi_provenance(version, digests))


def _npm_tags(payload: object | None) -> dict[str, object]:
    if payload is None:
        return {}
    return _mapping(_mapping(payload).get("dist-tags"))


def _npm_artifact(root: Path, version: str) -> Path:
    _python, npm = verify_release_bundle(root.parent)
    if npm.version != version:
        raise ValueError("npm artifact version mismatch")
    tarballs = tuple(root.glob("*.tgz"))
    if len(tarballs) != 1:
        raise ValueError("npm release artifact count mismatch")
    return tarballs[0]


def _npm_attestation_payload(payload: object | None) -> object | None:
    return _fetch_json(_npm_attestation_url(payload)) if payload is not None else None


def _tag_version(tags: dict[str, object], tag: str) -> str | None:
    value = tags.get(tag)
    return value if isinstance(value, str) else None


def _provenance_identity(tarball: Path) -> ProvenanceIdentity:
    return ProvenanceIdentity(
        os.environ["RELEASE_TAG"], os.environ["GITHUB_SHA"], _digest(tarball, "sha512")
    )


def _npm_decision(
    publish: bool, channel: str, publish_tag: str, tags: dict[str, object]
) -> ReleaseDecision:
    return ReleaseDecision(
        publish,
        channel,
        publish_tag,
        _tag_version(tags, channel),
        _tag_version(tags, publish_tag),
    )


def _npm_state(root: Path, version: str) -> ReleaseDecision:
    tarball = _npm_artifact(root, version)
    encoded = urllib.parse.quote("@gainratio/assay", safe="")
    payload = _fetch_json(f"https://registry.npmjs.org/{encoded}/{version}")
    tags = _npm_tags(_fetch_json(f"https://registry.npmjs.org/{encoded}"))
    channel = npm_release_channel(version, tags)
    publish_tag = npm_publish_tag(version, tags)
    publish = npm_release_state(
        tarball, payload, _npm_attestation_payload(payload), _provenance_identity(tarball)
    )
    return _npm_decision(publish, channel, publish_tag, tags)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("registry", choices=("pypi", "npm"))
    parser.add_argument("artifact_root", type=Path)
    parser.add_argument("version")
    parser.add_argument("github_output", type=Path)
    return parser


def _write_decision(path: Path, decision: ReleaseDecision) -> None:
    lines = [f"publish={'true' if decision.publish else 'false'}"]
    if decision.channel is not None:
        lines.extend(
            (
                f"dist-tag={decision.channel}",
                f"publish-tag={decision.publish_tag}",
                f"channel-version={decision.channel_version or '__ABSENT__'}",
                f"publish-tag-version={decision.publish_tag_version or '__ABSENT__'}",
            )
        )
    with path.open("a", encoding="utf-8") as output:
        output.write("\n".join(lines) + "\n")


def _run(arguments: argparse.Namespace) -> ReleaseDecision:
    root = cast(Path, arguments.artifact_root)
    version = cast(str, arguments.version)
    if arguments.registry == "pypi":
        return ReleaseDecision(_pypi_state(root, version))
    return _npm_state(root, version)


def _decision_message(decision: ReleaseDecision) -> str:
    if decision.publish:
        return "registry artifact is missing"
    return "verified existing registry bytes and provenance"


def main() -> int:
    """Run one registry preflight and emit a GitHub Actions decision."""
    arguments = _parser().parse_args()
    try:
        decision = _run(arguments)
    except (OSError, ValueError, urllib.error.URLError) as error:
        print(str(error), file=sys.stderr)
        return 1
    _write_decision(cast(Path, arguments.github_output), decision)
    print(_decision_message(decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
