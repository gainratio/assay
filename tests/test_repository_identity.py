"""Executable repository identity and import-boundary contracts."""

from __future__ import annotations

import ast
import importlib
import inspect
import re
import runpy
import sys
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import pytest

from assay import models
from assay.settings import AssaySettings

ROOT = Path(__file__).parents[1]
FORBIDDEN_IMPORT_ROOTS = frozenset({"avow", "writ", "nacl", "rfc8785"})
FORBIDDEN_EVIDENCE_TERMS = re.compile(r"\b(?:key|signature|signing|receipt|ledger)\b", re.I)
PUBLIC_MODEL_TYPES = (
    models.ScoreRequest,
    models.SubScoreInput,
    models.CompositeRequest,
    models.RelevanceJudgment,
    models.RankedQuery,
    models.ItemRating,
    models.AgreementRequest,
    models.RankingRequest,
)


def _load_pyproject() -> Mapping[str, object]:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return cast(Mapping[str, object], tomllib.loads(pyproject))


def _table(config: Mapping[str, object], *keys: str) -> Mapping[str, object]:
    current = config
    for key in keys:
        value = current[key]
        assert isinstance(value, dict)
        current = cast(Mapping[str, object], value)
    return current


def _import_roots(path: Path) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    direct = {
        alias.name.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    from_imports = {
        node.module.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    return frozenset(direct | from_imports)


def _console_main() -> Callable[[], int]:
    scripts = _table(_load_pyproject(), "project", "scripts")
    entry = scripts["assay"]
    assert isinstance(entry, str)
    module_name, separator, attribute = entry.partition(":")
    assert separator
    module = importlib.import_module(module_name)
    return cast(Callable[[], int], getattr(module, attribute))


def _dependency_names(value: object) -> frozenset[str]:
    assert isinstance(value, list)
    dependencies = [item for item in value if isinstance(item, str)]
    assert len(dependencies) == len(value)
    names = (re.split(r"[<>=!~;\[]", item, maxsplit=1)[0] for item in dependencies)
    return frozenset(name.lower() for name in names)


def test_should_name_scoring_distribution_assay_engine() -> None:
    # Given
    project = _table(_load_pyproject(), "project")

    # When
    distribution_name = project["name"]

    # Then
    assert distribution_name == "assay-engine"


def test_should_package_only_assay_when_building_wheel() -> None:
    # Given
    project = _load_pyproject()

    # When
    wheel = _table(project, "tool", "hatch", "build", "targets", "wheel")

    # Then
    assert wheel["packages"] == ["src/assay"]


def test_should_single_source_assay_development_version() -> None:
    # Given
    project = _load_pyproject()
    expected_path = "src/assay/_version.py"

    # When
    version_config = _table(project, "tool", "hatch", "version")

    # Then
    assert version_config["path"] == expected_path
    assert (ROOT / expected_path).is_file()
    version_module = runpy.run_path(ROOT / expected_path)
    assert version_module["__version__"] == "0.5.0.dev6"


def test_should_install_assay_console_script() -> None:
    # Given
    project = _load_pyproject()

    # When
    scripts = _table(project, "project", "scripts")

    # Then
    assert scripts["assay"] == "assay.cli:main"


def test_should_import_assay_console_entry() -> None:
    # Given / When
    main = _console_main()

    # Then
    assert callable(main)


def test_should_refuse_legacy_security_command_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    private_key = tmp_path / "legacy.key"
    public_key = tmp_path / "legacy.pub"
    command = ["assay", "keygen", "--out", str(private_key), "--pub", str(public_key)]
    monkeypatch.setattr(sys, "argv", command)

    # When
    status = _console_main()()
    captured = capsys.readouterr()

    # Then
    assert status == 2
    assert captured.err == "FAIL: assay.command_moved_to_avow; use `avow keygen ...`\n"
    assert not private_key.exists()
    assert not public_key.exists()


def test_should_exclude_other_product_source_packages() -> None:
    # Given
    other_products = (ROOT / "src" / "avow", ROOT / "src" / "writ")

    # When
    existing_products = [path for path in other_products if path.exists()]

    # Then
    assert not existing_products


def test_should_reject_cross_product_imports_from_assay() -> None:
    # Given
    sources = sorted((ROOT / "src" / "assay").rglob("*.py"))

    # When
    violations = [
        (str(path.relative_to(ROOT)), sorted(_import_roots(path) & FORBIDDEN_IMPORT_ROOTS))
        for path in sources
        if _import_roots(path) & FORBIDDEN_IMPORT_ROOTS
    ]

    # Then
    assert not violations, violations


def test_should_exclude_evidence_storage_from_scoring_settings() -> None:
    # Given / When
    setting_names = frozenset(AssaySettings.model_fields)

    # Then
    assert "signing_key_path" not in setting_names
    assert "ledger_path" not in setting_names


def test_should_use_scoring_language_on_public_request_models() -> None:
    # Given / When
    documentation = "\n".join(inspect.getdoc(model) or "" for model in PUBLIC_MODEL_TYPES)

    # Then
    assert not FORBIDDEN_EVIDENCE_TERMS.findall(documentation)


def test_should_keep_base_dependencies_minimal() -> None:
    # Given / When
    project = _table(_load_pyproject(), "project")

    # Then
    assert _dependency_names(project["dependencies"]) == frozenset({"pydantic"})


def test_should_isolate_metric_and_cli_dependencies() -> None:
    # Given / When
    extras = _table(_load_pyproject(), "project", "optional-dependencies")

    # Then
    assert _dependency_names(extras["metrics"]) == frozenset(
        {"pydantic-settings", "scikit-learn", "scipy", "numpy", "ir-measures"}
    )
    assert _dependency_names(extras["cli"]) == frozenset({"typer"})


def test_should_exclude_cryptography_dependencies_from_metadata_and_lock() -> None:
    # Given / When
    dependency_state = "\n".join(
        (ROOT / name).read_text(encoding="utf-8").lower() for name in ("pyproject.toml", "uv.lock")
    )

    # Then
    assert "pynacl" not in dependency_state
    assert "rfc8785" not in dependency_state


def test_should_document_the_assay_only_repository_workflow() -> None:
    # Given / When
    guidance = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    # Then
    assert guidance.startswith("# Assay scoring engine")
    assert "uv run poe gate" in guidance
    assert "node@22.13.0" in guidance
    assert "corepack@0.34.0" in guidance
    assert "corepack pnpm gate" in guidance
    assert "corepack pnpm --dir ts" not in guidance
    assert "Node 22.13" in guidance
    assert "Avow trust-kernel monorepo" not in guidance
    assert "src/avow" not in guidance
    assert "src/writ" not in guidance
    assert "receipt-ui" not in guidance


def test_should_document_the_portable_binary64_contract() -> None:
    # Given / When
    source = (ROOT / "src" / "assay" / "contracts.py").read_text(encoding="utf-8")
    # Then
    assert "finite IEEE-754 binary64" in source
    assert "safe integer" not in source
