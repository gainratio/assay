"""Verify registry provenance, served bytes, and clean consumer installs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol, cast

from scripts import registry_release_guard as guard
from scripts.verify_release_artifacts import _artifacts, _source_versions, verify_release_bundle

_ARGUMENT_COUNT = 5
_NOT_FOUND = 404
_POLL_SECONDS = 10.0
_PYPI_ROOT = "https://pypi.org"
_NPM_ROOT = "https://registry.npmjs.org"
_DOWNLOAD_HOSTS = frozenset({"files.pythonhosted.org", "registry.npmjs.org"})


class PropagationPending(RuntimeError):  # noqa: N818 - required workflow-state term
    """An authoritative endpoint says a just-published object is still absent."""


FetchBytes = Callable[[str, float, int], bytes]


class _Headers(Protocol):
    def get(self, name: str) -> str | None: ...


class _Response(Protocol):
    headers: _Headers

    def read(self, size: int = -1) -> bytes: ...

    def __enter__(self) -> _Response: ...

    def __exit__(self, *arguments: object) -> object: ...


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0.0:
        raise TimeoutError("registry verification deadline exhausted")
    return remaining


def _open(url: str, deadline: float) -> _Response:
    if not url.startswith("https://"):
        raise ValueError("registry URL must use HTTPS")
    try:
        response = urllib.request.urlopen(  # noqa: S310
            url, timeout=min(15.0, _remaining(deadline))
        )
        return cast(_Response, response)
    except urllib.error.HTTPError as error:
        if error.code == _NOT_FOUND:
            raise PropagationPending("registry object is absent") from error
        raise


def _read_json(url: str, deadline: float) -> object:
    with _open(url, deadline) as response:
        raw = guard._bounded_response(response, guard._METADATA_LIMIT)
    try:
        payload = cast(object, json.loads(raw))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("registry metadata is malformed") from error
    if payload is None:
        raise ValueError("registry metadata is malformed")
    return payload


def _declared_artifact_size(response: _Response) -> int:
    raw = response.headers.get("Content-Length")
    if raw is None:
        raise ValueError("registry artifact size is missing")
    try:
        size = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("registry artifact size is malformed") from error
    if size < 0:
        raise ValueError("registry artifact size is malformed")
    return size


def _read_exact(response: _Response, expected_size: int) -> bytes:
    declared = _declared_artifact_size(response)
    if declared != expected_size:
        raise ValueError("registry artifact size mismatch")
    payload = response.read(expected_size + 1)
    if len(payload) != expected_size:
        raise ValueError("registry artifact size mismatch")
    return payload


def read_served_bytes(url: str, deadline: float, expected_size: int) -> bytes:
    """Download exactly the reviewed byte count within the global deadline."""
    host = urllib.parse.urlsplit(url).hostname
    if host not in _DOWNLOAD_HOSTS:
        raise ValueError("registry artifact URL is not trusted")
    with _open(url, deadline) as response:
        return _read_exact(response, expected_size)


def _pypi_urls(payload: object) -> dict[str, str]:
    urls: dict[str, str] = {}
    for item in guard._sequence(guard._mapping(payload).get("urls")):
        record = guard._mapping(item)
        filename, url = record.get("filename"), record.get("url")
        if not isinstance(filename, str) or not isinstance(url, str):
            raise ValueError("registry metadata is malformed")
        if filename in urls:
            raise ValueError("duplicate PyPI filename")
        urls[filename] = url
    return urls


def _npm_tarball_url(payload: object) -> str:
    dist = guard._mapping(guard._mapping(payload).get("dist"))
    url = dist.get("tarball")
    if not isinstance(url, str):
        raise ValueError("registry metadata is malformed")
    return url


def _download(path: Path, url: str, fetch: FetchBytes, deadline: float, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(fetch(url, deadline, size))


def _materialize_python(
    reviewed: Path, served: Path, payload: object, fetch: FetchBytes, deadline: float
) -> None:
    local, remote = _artifacts(reviewed), _pypi_urls(payload)
    artifacts = (local.wheel, local.sdist)
    if set(remote) != {artifact.name for artifact in artifacts}:
        raise ValueError("PyPI artifact filename mismatch")
    for artifact in artifacts:
        target = served / artifact.relative_to(reviewed)
        _download(target, remote[artifact.name], fetch, deadline, artifact.stat().st_size)


def _materialize_npm(
    reviewed: Path, served: Path, payload: object, fetch: FetchBytes, deadline: float
) -> None:
    artifact = _artifacts(reviewed).npm
    target = served / artifact.relative_to(reviewed)
    _download(target, _npm_tarball_url(payload), fetch, deadline, artifact.stat().st_size)


def materialize_served_bundle(
    reviewed: Path,
    served: Path,
    pypi_payload: object,
    npm_payload: object,
    fetch: FetchBytes,
    deadline: float,
) -> None:
    """Build an exact release envelope from registry-served artifact bytes."""
    if served.exists():
        shutil.rmtree(served)
    _materialize_python(reviewed, served, pypi_payload, fetch, deadline)
    _materialize_npm(reviewed, served, npm_payload, fetch, deadline)
    shutil.copyfile(reviewed / "SHA256SUMS", served / "SHA256SUMS")
    verify_release_bundle(served)


def _pypi_ready(reviewed: Path, version: str, payload: object, deadline: float) -> None:
    expected = guard._local_python_digests(reviewed / "python")
    if guard._pypi_digests(payload) != expected:
        raise ValueError("PyPI artifact or provenance mismatch")
    root = f"{_PYPI_ROOT}/integrity/assay-engine/{version}"
    for filename, digest in expected.items():
        encoded = urllib.parse.quote(filename, safe="")
        provenance = _read_json(f"{root}/{encoded}/provenance", deadline)
        if not guard.pypi_provenance_valid(provenance, filename, digest):
            raise ValueError("PyPI artifact or provenance mismatch")


def _npm_ready(reviewed: Path, payload: object, tag: str, sha: str, deadline: float) -> None:
    artifact = _artifacts(reviewed).npm
    attestation = _read_json(guard._npm_attestation_url(payload), deadline)
    digest = hashlib.sha512(artifact.read_bytes()).hexdigest()
    identity = guard.ProvenanceIdentity(tag, sha, digest)
    if guard.npm_release_state(artifact, payload, attestation, identity):
        raise PropagationPending("npm release is absent")


def _tag_value(tags: dict[str, object], name: str) -> str | None:
    if name not in tags:
        return None
    value = tags[name]
    if not isinstance(value, str):
        raise ValueError("npm registry metadata is malformed")
    return value


def _verify_tags(
    payload: object, version: str, channel: str, selected: str, published: bool
) -> None:
    tags = guard._mapping(guard._mapping(payload).get("dist-tags"))
    current = _tag_value(tags, channel)
    if current is None:
        raise PropagationPending("npm channel is absent")
    if not guard.dist_tag_is_current_or_newer(version, current):
        raise ValueError("npm channel moved backward or across channels")
    _verify_selected_tag(tags, version, selected, published)
    _verify_default_install(payload, tags)


def _verify_default_install(payload: object, tags: dict[str, object]) -> None:
    """`npm install @edgeproc/assay` resolves `latest`; it must name a real, importable release.

    The registry assigns `latest` to a package's first version whatever `--tag` says, so the
    0.0.0-bootstrap.0 trusted-publishing stub became `latest`, and prereleases only move `next`.
    A version record without `exports` (the stub has none) installs nothing usable.
    """
    latest = _tag_value(tags, "latest")
    versions = guard._mapping(guard._mapping(payload).get("versions"))
    record = versions.get(latest) if latest is not None else None
    if not isinstance(record, dict) or "exports" not in record:
        raise ValueError("npm latest does not identify an installable release")


def _verify_selected_tag(
    tags: dict[str, object], version: str, selected: str, published: bool
) -> None:
    selected_version = _tag_value(tags, selected)
    if published and selected_version is None:
        raise PropagationPending("npm publish tag is absent")
    if published and selected_version != version:
        raise ValueError("npm publish tag does not identify the release")


def _clean_install(served: Path, deadline: float) -> None:
    verifier = Path(__file__).with_name("verify_release_artifacts.py")
    try:
        subprocess.run(  # noqa: S603
            [sys.executable, verifier, served],
            check=True,
            capture_output=True,
            text=True,
            timeout=_remaining(deadline),
        )
    except subprocess.TimeoutExpired as error:
        raise TimeoutError("registry clean-install deadline exhausted") from error
    except subprocess.CalledProcessError as error:
        raise ValueError("registry-served artifact clean install failed") from error


def _verify_once(
    reviewed: Path, channel: str, selected: str, published: bool, deadline: float
) -> None:
    python_version, npm_version = _source_versions()
    pypi = _read_json(f"{_PYPI_ROOT}/pypi/assay-engine/{python_version}/json", deadline)
    encoded = urllib.parse.quote("@edgeproc/assay", safe="")
    npm = _read_json(f"{_NPM_ROOT}/{encoded}/{npm_version}", deadline)
    _pypi_ready(reviewed, python_version, pypi, deadline)
    _npm_ready(reviewed, npm, os.environ["RELEASE_TAG"], os.environ["GITHUB_SHA"], deadline)
    package = _read_json(f"{_NPM_ROOT}/{encoded}", deadline)
    _verify_tags(package, npm_version, channel, selected, published)
    with TemporaryDirectory(prefix="assay-served-") as temporary:
        served = Path(temporary) / "release"
        materialize_served_bundle(reviewed, served, pypi, npm, read_served_bytes, deadline)
        _clean_install(served, deadline)


def poll_until_verified(operation: Callable[[float], None], timeout_seconds: float = 600.0) -> None:
    """Retry only authoritative absence under one monotonic global deadline."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            operation(deadline)
            return
        except PropagationPending:
            time.sleep(min(_POLL_SECONDS, _remaining(deadline)))


def _published(value: str) -> bool:
    if value not in ("true", "false"):
        raise ValueError("npm publish decision is malformed")
    return value == "true"


def _run_verification(reviewed: Path, channel: str, selected: str, value: str) -> None:
    publish = _published(value)
    operation = partial(_verify_once, reviewed, channel, selected, publish)
    poll_until_verified(operation, timeout_seconds=600.0)


def _usage() -> int:
    print("usage: verify_published_release.py ROOT CHANNEL PUBLISH_TAG PUBLISHED", file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) != _ARGUMENT_COUNT:
        return _usage()
    reviewed, channel, selected = Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3]
    try:
        _run_verification(reviewed, channel, selected, sys.argv[4])
    except (OSError, RuntimeError, ValueError, urllib.error.URLError) as error:
        print(str(error), file=sys.stderr)
        return 1
    python_version, npm_version = _source_versions()
    print(f"verified registry bytes for assay {python_version} and @edgeproc/assay {npm_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
