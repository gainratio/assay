"""Inspect and clean-install the three Assay release artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

_PYTHON_PRERELEASE = re.compile(r"^(\d+\.\d+\.\d+)\.dev(\d+)$")
_ARGUMENT_COUNT = 2
_MIN_TAR_PARTS = 2
_NODE_PROBE = """
import { compose, parseRequest } from '@gainratio/assay';
const request = parseRequest({method:'minimum',method_version:'artifact-v1',components:[
  {id:'a',label:'A',value:60,scale:{minimum:0,maximum:100,direction:'higher_is_better'},interval:null,weight:null},
  {id:'b',label:'B',value:80,scale:{minimum:0,maximum:100,direction:'higher_is_better'},interval:null,weight:null}],clamp:'reject'});
const result = compose(request);
if (result.score !== 0.6 || result.selected_component_id !== 'a') {
  throw new Error('composition mismatch');
}
for (const path of ['receipt','keys','canonical','ledger','writ']) {
  try { await import(`@gainratio/assay/${path}`); throw new Error('legacy subpath resolved'); }
  catch (error) {
    if (error instanceof Error && error.message === 'legacy subpath resolved') throw error;
  }
}
"""
_PYTHON_PROBE = """
from assay import compose, parse_request
request = parse_request({"method":"minimum","method_version":"artifact-v1","components":[
{"id":"a","label":"A","value":60,"scale":{"minimum":0,"maximum":100,"direction":"higher_is_better"},"interval":None,"weight":None},
{"id":"b","label":"B","value":80,"scale":{"minimum":0,"maximum":100,"direction":"higher_is_better"},"interval":None,"weight":None}],"clamp":"reject"})
result = compose(request)
assert result.score == 0.6 and result.selected_component_id == "a"
"""
_METRICS_PROBE = """
from assay.metrics import confusion_counts
assert confusion_counts([1, 0], [0.9, 0.1]).true_positives == 1
"""
_NPM_MEMBERS = frozenset(
    {
        "package/LICENSE",
        "package/README.md",
        "package/package.json",
        "package/dist/additive.d.ts",
        "package/dist/additive.js",
        "package/dist/compose.d.ts",
        "package/dist/compose.js",
        "package/dist/contracts.d.ts",
        "package/dist/contracts.js",
        "package/dist/errors.d.ts",
        "package/dist/errors.js",
        "package/dist/index.d.ts",
        "package/dist/index.js",
        "package/dist/metrics.d.ts",
        "package/dist/metrics.js",
        "package/dist/minimum.d.ts",
        "package/dist/minimum.js",
        "package/dist/normalize.d.ts",
        "package/dist/normalize.js",
        "package/dist/ranking.d.ts",
        "package/dist/ranking.js",
        "package/dist/requestHash.d.ts",
        "package/dist/requestHash.js",
        "package/dist/weightedMean.d.ts",
        "package/dist/weightedMean.js",
    }
)


@dataclass(frozen=True)
class Artifacts:
    wheel: Path
    sdist: Path
    npm: Path


@dataclass(frozen=True)
class Identity:
    name: str
    version: str


def _only(paths: tuple[Path, ...], kind: str) -> Path:
    if len(paths) != 1:
        raise ValueError(f"expected one {kind}, found {len(paths)}")
    return paths[0]


def _source_versions() -> tuple[str, str]:
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/assay/_version.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', source, re.MULTILINE)
    package = json.loads((root / "ts/package.json").read_text(encoding="utf-8"))
    npm = package.get("version") if isinstance(package, dict) else None
    if match is None or not isinstance(npm, str):
        raise ValueError("release source version is missing")
    return match.group(1), npm


def _expected_artifacts(root: Path, python_version: str, npm_version: str) -> Artifacts:
    return Artifacts(
        wheel=root / "python" / f"assay_engine-{python_version}-py3-none-any.whl",
        sdist=root / "python" / f"assay_engine-{python_version}.tar.gz",
        npm=root / "npm" / f"gainratio-assay-{npm_version}.tgz",
    )


def _artifacts(root: Path) -> Artifacts:
    artifacts = _expected_artifacts(root, *_source_versions())
    if not all(path.is_file() for path in _artifact_paths(artifacts)):
        raise ValueError("release artifact filename mismatch")
    return artifacts


def _wheel_metadata(path: Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        members = tuple(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = _only(tuple(Path(name) for name in members), "wheel METADATA")
        return archive.read(metadata.as_posix())


def _sdist_metadata(path: Path) -> bytes:
    with tarfile.open(path, "r:gz") as archive:
        members = tuple(item for item in archive.getmembers() if item.name.endswith("/PKG-INFO"))
        member = _only(tuple(Path(item.name) for item in members), "sdist PKG-INFO")
        extracted = archive.extractfile(member.as_posix())
        if extracted is None:
            raise ValueError("sdist PKG-INFO is unreadable")
        return extracted.read()


def _python_identity(payload: bytes) -> Identity:
    metadata = BytesParser().parsebytes(payload)
    return Identity(name=str(metadata["Name"]), version=str(metadata["Version"]))


def _npm_identity(path: Path) -> Identity:
    with tarfile.open(path, "r:gz") as archive:
        extracted = archive.extractfile("package/package.json")
        if extracted is None:
            raise ValueError("npm package metadata is unreadable")
        package = cast(object, json.loads(extracted.read()))
    return _npm_identity_from(package)


def _npm_identity_from(package: object) -> Identity:
    if not isinstance(package, dict):
        raise ValueError("npm package metadata is not an object")
    name, version = package.get("name"), package.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise ValueError("npm package identity is missing")
    return Identity(name=name, version=version)


def _npm_spelling(python_version: str) -> str:
    match = _PYTHON_PRERELEASE.fullmatch(python_version)
    if match is None:
        return python_version
    return f"{match.group(1)}-dev.{match.group(2)}"


def _validate_metadata(artifacts: Artifacts) -> tuple[Identity, Identity]:
    wheel = _python_identity(_wheel_metadata(artifacts.wheel))
    sdist = _python_identity(_sdist_metadata(artifacts.sdist))
    npm = _npm_identity(artifacts.npm)
    if wheel != sdist or wheel.name != "assay-engine":
        raise ValueError("Python artifact metadata does not match")
    if npm.name != "@gainratio/assay" or npm.version != _npm_spelling(wheel.version):
        raise ValueError("Python and npm artifact metadata does not match")
    return wheel, npm


def _source_members() -> frozenset[str]:
    root = Path(__file__).resolve().parents[1] / "src/assay"
    paths = (path for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    return frozenset(f"assay/{path.relative_to(root)}" for path in paths)


def _wheel_metadata_members(version: str) -> frozenset[str]:
    root = f"assay_engine-{version}.dist-info"
    names = ("METADATA", "WHEEL", "entry_points.txt", "RECORD", "licenses/LICENSE")
    return frozenset(f"{root}/{name}" for name in names)


def _validate_wheel_members(path: Path, version: str) -> None:
    with zipfile.ZipFile(path) as archive:
        members = _wheel_names(tuple(archive.infolist()))
    expected = _source_members() | _wheel_metadata_members(version)
    if members != expected:
        raise ValueError("wheel membership mismatch")


def _wheel_names(records: tuple[zipfile.ZipInfo, ...]) -> frozenset[str]:
    names = tuple(record.filename for record in records)
    if len(names) != len(set(names)):
        raise ValueError("duplicate wheel member")
    if any(not _regular_zip_record(record) for record in records):
        raise ValueError("non-regular wheel member")
    return frozenset(names)


def _regular_zip_record(record: zipfile.ZipInfo) -> bool:
    mode = record.external_attr >> 16
    return not record.is_dir() and (stat.S_IFMT(mode) in (0, stat.S_IFREG))


def _reject_unsafe_tar_name(name: str) -> None:
    if not name:
        raise ValueError("unexpected tar member")
    if name.startswith("/"):
        raise ValueError("unexpected tar member")
    if "\\" in name:
        raise ValueError("unexpected tar member")


def _tar_name_parts(name: str) -> tuple[str, ...]:
    _reject_unsafe_tar_name(name)
    parts = tuple(name.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("unexpected tar member")
    return parts


def _tar_member_path(member: tarfile.TarInfo) -> tuple[str, ...]:
    if not member.isfile():
        raise ValueError("non-regular tar member")
    return _tar_name_parts(member.name)


def _tar_file_members(path: Path) -> tuple[tuple[str, ...], ...]:
    with tarfile.open(path, "r:gz") as archive:
        members = tuple(_tar_member_path(member) for member in archive.getmembers())
    if len(members) != len(set(members)):
        raise ValueError("duplicate tar member")
    return members


def _validate_sdist_members(path: Path, version: str) -> None:
    expected = {"LICENSE", "README.md", "pyproject.toml", "PKG-INFO"}
    expected.update(f"src/{item}" for item in _source_members())
    members = _tar_file_members(path)
    root = f"assay_engine-{version}"
    actual = {_sdist_member_name(parts, root) for parts in members}
    if actual != expected:
        raise ValueError("sdist membership mismatch")


def _sdist_member_name(parts: tuple[str, ...], root: str) -> str:
    if len(parts) < _MIN_TAR_PARTS:
        raise ValueError("sdist membership mismatch")
    if parts[0] != root:
        raise ValueError("sdist membership mismatch")
    return "/".join(parts[1:])


def _validate_npm_members(path: Path) -> None:
    actual = frozenset("/".join(parts) for parts in _tar_file_members(path))
    if actual != _NPM_MEMBERS:
        raise ValueError("npm membership mismatch")


def _validate_memberships(artifacts: Artifacts) -> None:
    version, _npm_version = _source_versions()
    _validate_wheel_members(artifacts.wheel, version)
    _validate_sdist_members(artifacts.sdist, version)
    _validate_npm_members(artifacts.npm)


def _artifact_paths(artifacts: Artifacts) -> tuple[Path, ...]:
    return tuple(sorted((artifacts.wheel, artifacts.sdist, artifacts.npm)))


def _validate_envelope(root: Path, artifacts: Artifacts) -> None:
    expected = {root / "SHA256SUMS", *_artifact_paths(artifacts)}
    actual = {path for path in root.rglob("*") if path.is_file()}
    if actual != expected:
        raise ValueError("release artifact envelope mismatch")


def _digest_lines(root: Path, artifacts: Artifacts) -> tuple[str, ...]:
    return tuple(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root)}"
        for path in _artifact_paths(artifacts)
    )


def _write_digest_manifest(root: Path, artifacts: Artifacts) -> None:
    content = "\n".join(_digest_lines(root, artifacts)) + "\n"
    (root / "SHA256SUMS").write_text(content, encoding="utf-8")


def verify_release_bundle(root: Path) -> tuple[Identity, Identity]:
    """Recheck downloaded artifact metadata and its reviewed SHA-256 manifest."""
    artifacts = _artifacts(root)
    _validate_envelope(root, artifacts)
    _validate_memberships(artifacts)
    identities = _validate_metadata(artifacts)
    actual = tuple((root / "SHA256SUMS").read_text(encoding="utf-8").splitlines())
    if actual != _digest_lines(root, artifacts):
        raise ValueError("release artifact digest mismatch")
    return identities


def _run(arguments: list[str | Path], *, cwd: Path | None = None) -> None:
    # Arguments are fixed commands plus path values and never enter a shell.
    subprocess.run(arguments, cwd=cwd, check=True, capture_output=True, text=True)  # noqa: S603


def _clean_python_install(artifact: Path, root: Path, extra: str | None = None) -> None:
    suffix = "base" if extra is None else extra.replace(",", "-")
    environment = root / f"python-{suffix}-{artifact.name}"
    _run(["uv", "venv", "--python", "3.13", environment])
    python = environment / "bin/python"
    requirement = str(artifact) if extra is None else f"{artifact}[{extra}]"
    _run(["uv", "pip", "install", "--python", python, requirement])
    _run([python, "-c", _PYTHON_PROBE])
    if extra in ("metrics", "cli,metrics"):
        _run([python, "-c", _METRICS_PROBE])
    if extra in ("cli", "cli,metrics"):
        _run([environment / "bin/assay", "--help"])


def _clean_npm_install(artifact: Path, root: Path) -> None:
    project = root / "npm"
    project.mkdir()
    (project / "package.json").write_text('{"private":true,"type":"module"}', encoding="utf-8")
    _run(["npm", "install", "--ignore-scripts", "--no-package-lock", artifact], cwd=project)
    _run(["node", "--input-type=module", "--eval", _NODE_PROBE], cwd=project)


def _clean_installs(artifacts: Artifacts) -> None:
    with TemporaryDirectory(prefix="assay-release-") as temporary:
        root = Path(temporary)
        _clean_python_install(artifacts.sdist, root)
        for extra in (None, "cli", "metrics", "cli,metrics"):
            _clean_python_install(artifacts.wheel, root, extra)
        _clean_npm_install(artifacts.npm, root)


def main() -> int:
    if len(sys.argv) != _ARGUMENT_COUNT:
        sys.stderr.write("usage: verify_release_artifacts.py ARTIFACT_ROOT\n")
        return 1
    root = Path(sys.argv[1]).resolve()
    artifacts = _artifacts(root)
    _validate_memberships(artifacts)
    python, npm = _validate_metadata(artifacts)
    _clean_installs(artifacts)
    _write_digest_manifest(root, artifacts)
    verify_release_bundle(root)
    sys.stdout.write(
        f"verified release artifacts: {python.name} {python.version} and {npm.name} {npm.version}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
