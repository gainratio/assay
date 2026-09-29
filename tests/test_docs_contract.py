"""Executable contract for Assay's scoring-only public documentation."""

from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path

import pytest

import assay
from assay.composite import SubScore, composite

_ROOT = Path(__file__).resolve().parents[1]
_README = _ROOT / "README.md"
_METHODS = _ROOT / "docs" / "METHODS.md"
_PUBLIC_DOCS = (
    _README,
    _ROOT / "QUICKSTART.md",
    _ROOT / "ts" / "README.md",
    _ROOT / "docs" / "ARCHITECTURE.md",
    _ROOT / "docs" / "OPERATIONS.md",
    _ROOT / "docs" / "GETTING_STARTED.md",
    _METHODS,
)
_TAGLINE = (
    "A Python and TypeScript library that combines several measurements into one score "
    "and shows exactly how it got there."
)
_STATUS = (
    "> **Status:** prerelease. The current versions are `assay-engine` 0.5.0.dev3 on PyPI "
    "and `@edgeproc/assay` 0.5.0-dev.3 on npm. There is no stable release yet."
)
_OPTIONAL = (
    "Assay only computes scores. Sealing evidence about a result is a separate project, "
    "Avow, and neither package imports or requires the other."
)
_TS_OPTIONAL = (
    "Assay computes scores; Avow seals evidence. They are separate products and neither "
    "requires the other."
)
_FIELDS = (
    "schema",
    "method.id",
    "method.version",
    "score",
    "interval",
    "clamp",
    "intercept",
    "weight_total",
    "components",
    "id",
    "raw",
    "normalized",
    "declared_weight",
    "operation",
    "coefficient",
    "contribution",
    "contribution_interval",
    "inputs_hash",
    "selected_component_id",
)
_STALE = (
    "one distribution `avow`",
    "pip install 'avow[assay]'",
    "demo/run_demo.py",
    "demo/unification_demo.py",
    "gate-ts",
    "gate-all",
    "assay.api",
    "assay.receipt",
    "sign_payload",
    "typescript package is still being finalized",
    "byte-equivalent json",
    "score verified",
)
_FENCE = re.compile(r"^```(?P<language>[^\n]*)\n(?P<body>.*?)^```$", re.MULTILINE | re.DOTALL)
_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _headings(text: str) -> tuple[str, ...]:
    return tuple(line for line in text.splitlines() if line.startswith("## "))


def _blocks(text: str, language: str | None = None) -> tuple[str, ...]:
    matches = _FENCE.finditer(text)
    return tuple(
        match.group("body")
        for match in matches
        if language is None or match.group("language").strip() == language
    )


def _local_link(document: Path, target: str) -> Path | None:
    cleaned = target.strip("<>").split("#", maxsplit=1)[0]
    if not cleaned or re.match(r"^[a-z][a-z0-9+.-]*:", cleaned, re.IGNORECASE):
        return None
    return (document.parent / cleaned).resolve()


def _typecheck_typescript(
    source: str, tmp_path: Path, index: int
) -> subprocess.CompletedProcess[str]:
    module = (_ROOT / "ts" / "src" / "index.js").as_posix()
    target = tmp_path / f"example-{index}.ts"
    target.write_text(source.replace('"@edgeproc/assay"', f'"{module}"'), encoding="utf-8")
    command = [
        "pnpm",
        "--dir",
        "ts",
        "exec",
        "tsc",
        "--noEmit",
        "--target",
        "ES2022",
        "--module",
        "NodeNext",
        "--moduleResolution",
        "NodeNext",
        "--strict",
        "--skipLibCheck",
        str(target),
    ]
    return subprocess.run(  # noqa: S603 - fixed compiler checks repository-owned examples
        command, cwd=_ROOT, capture_output=True, text=True, check=False
    )


def test_should_open_with_exact_product_identity_and_status() -> None:
    # Given the root product page
    readme = _read(_README)
    # When its opening and installation status are read
    # Then one product identity, its tagline, and the exact prerelease status are present
    assert readme.startswith(f"# Assay\n\n{_TAGLINE}\n")
    assert readme.count("# Assay") == 1
    assert _STATUS in readme
    assert readme.index(_TAGLINE) < readme.index("## Install\n") < readme.index(_STATUS)
    assert "`pip install assay-engine`" in readme
    assert "`npm install @edgeproc/assay`" in readme


def test_should_make_one_install_line_the_first_runnable_block() -> None:
    # Given every shell block in README order
    readme = _read(_README)
    blocks = _blocks(readme, "bash")
    # When a cold reader reaches the first runnable material
    # Then it is one pinned install line, and the artifact-backed demo stays one line
    assert blocks[0] == "pip install assay-engine\n"
    assert "bash examples/run_composite.sh\n" in blocks
    assert readme.index("## Try it\n") < readme.index(f"```bash\n{blocks[0]}")


def test_should_order_example_explanation_and_limits_before_install() -> None:
    # Given the README text
    readme = _read(_README)
    required = (
        "## Try it",
        "## How it works",
        "## What it does not do",
        "## When to use something else",
        "## Install",
        "## Develop",
    )
    # When the required sections are located
    positions = tuple(readme.index(f"\n{heading}\n") for heading in required)
    # Then the real example and its honest limits come before setup detail
    assert positions == tuple(sorted(positions))


def test_should_bound_all_cross_product_vocabulary_to_one_limits_bullet() -> None:
    # Given the README text
    readme = _read(_README)
    limits = readme.split("## What it does not do", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]
    # When product-boundary vocabulary is inspected outside the one boundary sentence
    remainder = readme.replace(_OPTIONAL, "").lower()
    # Then scoring stands alone, and the boundary is stated once, as a limit
    forbidden = r"\b(avow|writ|keys?|sign(?:ed|ing|ature)?s?|receipts?|ledgers?|envelopes?)\b"
    assert re.search(forbidden, remainder) is None
    assert readme.count(_OPTIONAL) == 1
    assert f"- {_OPTIONAL}" in limits


def test_should_define_all_methods_and_result_fields() -> None:
    # Given the detailed method reference
    methods = _read(_METHODS)
    # When its portable contract is inspected
    # Then all three algorithms and every serialized result field are defined
    assert all(f"## `{name}`" in methods for name in ("weighted_mean", "additive", "minimum"))
    assert all(f"`{field}`" in methods for field in _FIELDS)
    assert "(value - minimum) / (maximum - minimum)" in methods
    assert "(maximum - value) / (maximum - minimum)" in methods


def test_should_document_legacy_python_composite_as_nonportable_compatibility() -> None:
    # Given the shipped legacy Python adapter and its actual result
    scores = (
        SubScore("a", 0.5, 0.4, 0.6, 0.0, 1.0, 1.0),
        SubScore("b", 0.6, 0.5, 0.7, 0.0, 1.0, 1.0),
        SubScore("c", 0.7, 0.6, 0.8, 0.0, 1.0, 1.0),
    )
    result = composite(scores)
    # When README and architecture describe the supported composition surfaces
    documents = (_read(_README), _read(_ROOT / "docs" / "ARCHITECTURE.md"))
    # Then they distinguish the portable API from the deep-import compatibility adapter
    assert "composite" not in assay.__all__
    assert not hasattr(result, "method")
    assert not hasattr(result, "inputs_hash")
    for text in documents:
        assert all(term in text for term in ("assay.composite", "SubScore", "Python-only"))
        assert all(term in text for term in ("deep import", "TypeScript", "new code"))


def test_should_map_exactly_two_production_source_trees_to_artifacts() -> None:
    # Given the architecture document's source-to-artifact diagram
    readme = _read(_ROOT / "docs" / "ARCHITECTURE.md")
    mappings = tuple(line for line in readme.splitlines() if line.startswith(("src/", "ts/src/")))
    # When production mappings are separated from repository support files
    # Then the wheel and tarball each have one source tree and no third runtime package exists
    assert mappings == (
        "src/assay/  ──> assay-engine wheel ──> import assay",
        'ts/src/     ──> @edgeproc/assay npm tarball ──> import "@edgeproc/assay"',
    )
    assert all(f"`{name}/`" in readme for name in ("examples", "docs", "tests", "testdata"))


def test_should_resolve_every_local_markdown_link() -> None:
    # Given every repository-facing public document
    unresolved: list[tuple[str, str]] = []
    # When local Markdown targets are resolved relative to their document
    for document in _PUBLIC_DOCS:
        for target in _LINK.findall(_read(document)):
            resolved = _local_link(document, target)
            if resolved is not None and not resolved.exists():
                unresolved.append((document.relative_to(_ROOT).as_posix(), target))
    # Then no local link points at a missing file
    assert unresolved == []


def test_should_parse_python_and_shell_examples() -> None:
    # Given every Python and shell block in the public docs
    failures: list[tuple[str, str]] = []
    for document in _PUBLIC_DOCS:
        text = _read(document)
        for source in _blocks(text, "python"):
            ast.parse(source)
        for source in (*_blocks(text, "bash"), *_blocks(text, "sh")):
            result = subprocess.run(
                ["bash", "-n"],  # noqa: S607 - fixed shell syntax checker
                input=source,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                failures.append((document.name, result.stderr))
    # Then every copyable block is syntactically valid
    assert failures == []


def test_should_typecheck_typescript_examples(tmp_path: Path) -> None:
    # Given every TypeScript block in the public docs
    sources = tuple(
        block for document in _PUBLIC_DOCS for block in _blocks(_read(document), "typescript")
    )
    # When the examples are checked against the local package's public types
    results = tuple(
        _typecheck_typescript(source, tmp_path, index) for index, source in enumerate(sources)
    )
    # Then a reader can compile them without weakening TypeScript
    assert sources
    assert all(result.returncode == 0 for result in results), "\n".join(
        result.stdout + result.stderr for result in results
    )


def test_should_use_action_installed_pnpm_without_corepack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a conflicting Corepack beside the action-installed pnpm launcher
    corepack = tmp_path / "corepack"
    corepack.write_text("#!/bin/sh\nexit 86\n", encoding="utf-8")
    corepack.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    # When a documentation example is typechecked
    result = _typecheck_typescript("const value: number = 1;\n", tmp_path, 999)
    # Then the pinned direct pnpm interface remains authoritative
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("stale", _STALE)
def test_should_remove_stale_pre_split_copy(stale: str) -> None:
    # Given all public documentation after removing the one approved integration paragraph
    text = "\n".join(_read(document) for document in _PUBLIC_DOCS)
    text = text.replace(_OPTIONAL, "").replace(_TS_OPTIONAL, "")
    # When obsolete paths, commands, and product claims are searched case-insensitively
    # Then no pre-split instruction survives
    assert stale not in text.lower()
    assert re.search(r"\bavow\b", text, re.IGNORECASE) is None
    assert re.search(r"\bwrit\b", text, re.IGNORECASE) is None
