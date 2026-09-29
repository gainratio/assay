"""Run the realistic Northstar example through clean built artifacts."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "examples" / "run_composite.sh"
_REQUEST = _ROOT / "examples" / "northstar_score.json"
_VECTORS = _ROOT / "testdata" / "vectors" / "composition.json"
_PNPM_DOCUMENTS = (_ROOT / "QUICKSTART.md", _ROOT / "ts" / "README.md", _ROOT / "CLAUDE.md")
_DEMO_DOCUMENTS = (_ROOT / "README.md", _ROOT / "QUICKSTART.md", _ROOT / "ts" / "README.md")
_MULTIPLY = "\N{MULTIPLICATION SIGN}"
_ARCHIVE = "edgeproc-assay-0.5.0-dev.3.tgz"
_ARCHIVE_SHA256 = "eb115cbd809762f43cf45212682144581f1f18cefa5208300251886a10d1b3ee"
_ARCHIVE_NORMALIZER = _ROOT / "ts" / "scripts" / "normalize-package-archive.mjs"
_SHELL_BLOCK = re.compile(r"^```bash\n(.*?)^```$", re.MULTILINE | re.DOTALL)
_ARCHIVE_MEMBERS = (
    "package/LICENSE",
    "package/README.md",
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
    "package/package.json",
)
_EXPECTED = f"""Northstar weighted score: 0.92
Method: weighted_mean @ northstar.2026-08-12
Interval: null — all inputs are deterministic

security       19/20  -> 0.950000 {_MULTIPLY} 0.20 = 0.19
privacy        15/15  -> 1.000000 {_MULTIPLY} 0.15 = 0.15
reliability    15/15  -> 1.000000 {_MULTIPLY} 0.15 = 0.15
performance    12/15  -> 0.800000 {_MULTIPLY} 0.15 = 0.12
correctness    15/15  -> 1.000000 {_MULTIPLY} 0.15 = 0.15
clarity        14/15  -> 0.933333 {_MULTIPLY} 0.15 = 0.14
production       2/5  -> 0.400000 {_MULTIPLY} 0.05 = 0.02

Total: 0.92
inputs_hash: sha256:0266b1c59c97bacf85dc945685c55bb4386856b525249c7d5663a8edf020ba06
Parity: Python and TypeScript fields and values match
"""


def _northstar_vector() -> dict[str, object]:
    vectors: list[dict[str, object]] = json.loads(_VECTORS.read_text(encoding="utf-8"))
    return next(vector for vector in vectors if vector["id"] == "northstar_uncapped_weighted")


def _tree_state() -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],  # noqa: S607
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _run_example(cwd: Path, artifact_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
    before = _tree_state()
    env = os.environ.copy()
    if artifact_dir is not None:
        env["ASSAY_EXAMPLE_ARTIFACT_DIR"] = str(artifact_dir)
    completed = subprocess.run(  # noqa: S603 - repository script is the test subject
        ["bash", str(_SCRIPT)],  # noqa: S607 - fixed shell interpreter
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert _tree_state() == before
    return completed


def _tool_path(package: str, binary: str) -> Path:
    completed = subprocess.run(  # noqa: S603 - pinned package lookup
        [  # noqa: S607 - fixed package lookup tool
            "npx",
            "--yes",
            f"--package={package}",
            "-c",
            f"command -v {binary}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(completed.stdout.strip())


def _node_environment() -> tuple[Path, Path, dict[str, str]]:
    node = _tool_path("node@22.13.0", "node")
    pnpm = _tool_path("pnpm@11.5.0", "pnpm")
    env = os.environ | {"PATH": f"{node.parent}:{pnpm.parent}:{os.environ['PATH']}"}
    return node, pnpm, env


def _run_node_tool(
    command: list[str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed repository tool and arguments
        command,
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _pack_real_package(destination: Path) -> Path:
    node, pnpm, env = _node_environment()
    completed = _run_node_tool(
        [str(pnpm), "--dir", "ts", "pack", "--pack-destination", str(destination)], env
    )
    assert completed.returncode == 0, completed.stderr
    archive = destination / _ARCHIVE
    normalized = _run_node_tool([str(node), str(_ARCHIVE_NORMALIZER), str(archive)], env)
    assert normalized.returncode == 0, normalized.stderr
    return archive


def _archive_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_archive(path: Path) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    assert node is not None
    return _run_node_tool([node, str(_ARCHIVE_NORMALIZER), str(path)])


def _gzip_with_os(os_byte: int) -> bytearray:
    archive = bytearray(gzip.compress(b"assay-portable-archive", mtime=0))
    archive[:9] = bytes.fromhex("1f8b08000000000000")
    archive[9] = os_byte
    return archive


def _archive_members(path: Path) -> tuple[str, ...]:
    with tarfile.open(path, "r:gz") as archive:
        return tuple(sorted(archive.getnames()))


def _copy_clean_checkout(destination: Path) -> None:
    ignored = shutil.ignore_patterns("node_modules", "dist", "coverage")
    shutil.copytree(_ROOT / "ts", destination / "ts", ignore=ignored)
    shutil.copy2(_ROOT / "LICENSE", destination / "LICENSE")
    shutil.copytree(_ROOT / "testdata", destination / "testdata")


def _documented_build_block(path: Path) -> str:
    blocks = _SHELL_BLOCK.findall(path.read_text(encoding="utf-8"))
    block = next(item for item in blocks if "install --frozen-lockfile" in item and "pack" in item)
    lines = block.splitlines()
    first = next(
        (index for index, line in enumerate(lines) if line.startswith("NODE22=")),
        next(index for index, line in enumerate(lines) if "corepack pnpm" in line),
    )
    start = first - 1 if first and lines[first - 1] == "cd ts" else first
    return "\n".join(lines[start:]) + "\n"


def _documented_demo_block(path: Path) -> str:
    blocks = _SHELL_BLOCK.findall(path.read_text(encoding="utf-8"))
    return next(block for block in blocks if "examples/run_composite.sh" in block)


def _run_documented_build(
    path: Path, checkout: Path, temporary: Path
) -> subprocess.CompletedProcess[str]:
    node = _tool_path("node@22.13.0", "node")
    env = os.environ | {
        "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
        "PATH": f"{node.parent}:{os.environ['PATH']}",
        "TMPDIR": str(temporary),
    }
    return subprocess.run(  # noqa: S603 - documentation shell block is the test subject
        [  # noqa: S607 - fixed shell interpreter
            "bash",
            "-euo",
            "pipefail",
            "-c",
            _documented_build_block(path),
        ],
        cwd=checkout,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture(scope="module")
def example_runs(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[subprocess.CompletedProcess[str], ...]:
    unrelated = tmp_path_factory.mktemp("unrelated")
    return _run_example(_ROOT), _run_example(unrelated)


def test_should_copy_only_the_committed_northstar_request() -> None:
    # Given the committed Northstar parity vector and public example request
    vector = _northstar_vector()
    request = json.loads(_REQUEST.read_text(encoding="utf-8"))
    # When their request objects are compared directly
    # Then no vector metadata or oracle output leaked into the public input
    assert tuple(request) == ("method", "method_version", "components", "clamp")
    assert request == vector["request"]


def test_should_run_built_artifacts_from_repository_and_unrelated_directories(
    example_runs: tuple[subprocess.CompletedProcess[str], ...],
) -> None:
    # Given two executions with different current working directories
    # When each builds and installs the real wheel and npm tarball
    # Then both complete silently except for the one shared human explanation
    for completed in example_runs:
        assert completed.returncode == 0, completed.stderr
        assert completed.stderr == ""
        assert completed.stdout == _EXPECTED


def test_should_print_each_result_summary_line_once(
    example_runs: tuple[subprocess.CompletedProcess[str], ...],
) -> None:
    # Given the artifact-parity explanation
    output = example_runs[0].stdout
    # When its result footer is counted
    # Then one language-neutral score, total, hash, and parity verdict are shown
    assert output.count("Northstar weighted score:") == 1
    assert output.count("Total:") == 1
    assert output.count("inputs_hash:") == 1
    assert output.count("Parity:") == 1


def test_should_pack_the_exact_real_npm_artifact_from_the_demo(tmp_path: Path) -> None:
    # Given separate destinations for the demo and normal pinned package build
    demo_destination = tmp_path / "demo"
    normal_destination = tmp_path / "normal"
    demo_destination.mkdir()
    normal_destination.mkdir()
    # When both paths build their npm tarball
    completed = _run_example(_ROOT, demo_destination)
    normal = _pack_real_package(normal_destination)
    demo = demo_destination / _ARCHIVE
    # Then the demo emits the complete byte-identical real package
    assert completed.returncode == 0, completed.stderr
    assert _archive_members(demo) == tuple(sorted(_ARCHIVE_MEMBERS))
    assert demo.read_bytes() == normal.read_bytes()
    assert _archive_hash(demo) == _ARCHIVE_SHA256


def test_should_canonicalize_only_the_npm_gzip_os_byte(tmp_path: Path) -> None:
    # Given equivalent gzip streams marked as macOS and Unix
    macos, unix = _gzip_with_os(0x13), _gzip_with_os(0x03)
    archive = tmp_path / "package.tgz"
    archive.write_bytes(macos)
    # When the shipped archive normalizer processes the macOS spelling
    completed = _normalize_archive(archive)
    # Then it emits the Unix-canonical bytes without touching the payload
    assert completed.returncode == 0, completed.stderr
    assert archive.read_bytes() == bytes(unix)
    assert archive.read_bytes()[:9] + archive.read_bytes()[10:] == bytes(macos[:9] + macos[10:])


@pytest.mark.parametrize(("offset", "value"), [(0, 0), (2, 0), (3, 1), (4, 1), (8, 2)])
def test_should_reject_an_unexpected_gzip_header_without_mutation(
    offset: int, value: int, tmp_path: Path
) -> None:
    # Given an archive whose fixed gzip header drifted from npm's reviewed format
    unexpected = _gzip_with_os(0x13)
    unexpected[offset] = value
    archive = tmp_path / "unexpected.tgz"
    archive.write_bytes(unexpected)
    # When normalization is attempted
    completed = _normalize_archive(archive)
    # Then the boundary fails closed and preserves every input byte
    assert (completed.returncode, completed.stderr) == (1, "expected canonical gzip header\n")
    assert archive.read_bytes() == bytes(unexpected)


def test_should_leave_a_canonical_gzip_archive_byte_identical(tmp_path: Path) -> None:
    # Given an already canonical archive
    archive = tmp_path / "canonical.tgz"
    archive.write_bytes(_gzip_with_os(0x03))
    before = archive.read_bytes()
    # When normalization is repeated
    completed = _normalize_archive(archive)
    # Then it is idempotent and the payload still decompresses exactly
    assert completed.returncode == 0, completed.stderr
    assert archive.read_bytes() == before
    assert gzip.decompress(archive.read_bytes()) == b"assay-portable-archive"


@pytest.mark.parametrize("document", _PNPM_DOCUMENTS)
def test_should_rehearse_documented_pnpm_build_from_dependency_clean_checkout(
    document: Path, tmp_path: Path
) -> None:
    # Given a checkout with no generated TypeScript dependencies
    checkout = tmp_path / "checkout"
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    _copy_clean_checkout(checkout)
    assert not (checkout / "ts" / "node_modules").exists()
    # When the documented build block runs with exact Node 22.13
    completed = _run_documented_build(document, checkout, temporary)
    # Then Corepack selects pnpm 11.5 and install, gate, and real pack all succeed
    assert completed.returncode == 0, completed.stderr
    assert "v22.13.0" in completed.stdout.splitlines()
    assert "11.5.0" in completed.stdout.splitlines()
    archive = temporary / "assay-pack" / _ARCHIVE
    assert _archive_hash(archive) == _ARCHIVE_SHA256


def test_should_run_each_displayed_demo_command_from_its_documented_location() -> None:
    # Given each public onboarding page's displayed demo command
    blocks = tuple(_documented_demo_block(path) for path in _DEMO_DOCUMENTS)
    # When their shared checkout-root command is rehearsed exactly as displayed
    completed = subprocess.run(  # noqa: S603 - documentation command is the test subject
        ["bash", "-euo", "pipefail", "-c", blocks[0]],  # noqa: S607
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    # Then every page gives the same working command without a false any-directory claim
    assert blocks == ("bash examples/run_composite.sh\n",) * len(_DEMO_DOCUMENTS)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == _EXPECTED
    claims = "\n".join(path.read_text(encoding="utf-8") for path in _DEMO_DOCUMENTS[1:])
    assert "any current directory" not in claims
    assert "from any directory" not in claims
