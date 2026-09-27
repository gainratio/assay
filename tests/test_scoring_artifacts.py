"""Built artifacts describe and contain only the Assay scoring product."""

from __future__ import annotations

import re
import subprocess
import tarfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BOUNDARY = (
    "Assay only computes scores. Sealing evidence about a result is a separate project, "
    "Avow, and neither package imports or requires the other."
)
_LEGACY_PATHS = (
    "/avow/",
    "/writ/",
    "/demo/",
    "/docs/",
    "quickstart",
    "canonical.json",
    "receipts.json",
)
_LEGACY_WORDS = ("key", "signature", "receipt", "ledger")
_LEGACY_PRODUCT_WORDS = ("avow", "writ", "signature", "receipt", "ledger", "history")
_LEGACY_KEY_PHRASES = (
    "key custody",
    "key lifecycle",
    "key management",
    "private key",
    "public key",
    "signing key",
)
_ARTIFACT_SAFE_LINKS = (
    "https://github.com/hseshadr/assay/blob/main/QUICKSTART.md",
    "https://github.com/hseshadr/assay/blob/main/docs/ARCHITECTURE.md",
    "https://github.com/hseshadr/assay/blob/main/docs/METHODS.md",
    "https://github.com/hseshadr/assay/blob/main/docs/OPERATIONS.md",
)
_MIGRATION_BOUNDARY_FILES = frozenset(
    {
        "src/assay/cli.py",
        "src/assay/errors.py",
        "tests/test_cli_migration.py",
        "tests/test_errors.py",
        "tests/test_repository_identity.py",
    }
)


@pytest.fixture(scope="module")
def built_artifacts(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    out = tmp_path_factory.mktemp("artifacts")
    subprocess.run(  # noqa: S603 - fixed build command and test-owned output
        ["bash", "scripts/build_python_artifacts.sh", str(out)],  # noqa: S607
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return next(out.glob("*.whl")), next(out.glob("*.tar.gz"))


def _sdist_names(path: Path) -> tuple[str, ...]:
    with tarfile.open(path, "r:gz") as archive:
        return tuple(member.name.lower() for member in archive.getmembers())


def _wheel_metadata(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        name = next(item for item in archive.namelist() if item.endswith(".dist-info/METADATA"))
        return archive.read(name).decode()


def _sdist_readme(path: Path) -> str:
    with tarfile.open(path, "r:gz") as archive:
        member = next(item for item in archive.getmembers() if item.name.endswith("/README.md"))
        stream = archive.extractfile(member)
        assert stream is not None
        return stream.read().decode()


def _extracted_texts(path: Path, destination: Path) -> tuple[tuple[str, str], ...]:
    with tarfile.open(path, "r:gz") as archive:
        archive.extractall(destination, filter="data")
    root = next(item for item in destination.iterdir() if item.is_dir())
    texts: list[tuple[str, str]] = []
    for item in root.rglob("*"):
        if item.is_file():
            texts.append((item.relative_to(root).as_posix(), item.read_text()))
    return tuple(texts)


def _words(text: str) -> Iterator[str]:
    yield from re.findall(r"[a-z]+", text.lower())


def _without_required_migration_words(name: str, text: str) -> str:
    if name == "tests/test_docs_contract.py":
        return ""
    if name not in _MIGRATION_BOUNDARY_FILES:
        return text
    return text.replace("avow", "").replace("ledger", "")


def test_should_exclude_legacy_product_assets_from_the_sdist(
    built_artifacts: tuple[Path, Path],
) -> None:
    _, sdist = built_artifacts
    names = _sdist_names(sdist)
    source = {
        f"/src/assay/{path.name.lower()}"
        for path in (_ROOT / "src/assay").iterdir()
        if path.is_file()
    }
    assert not any(token in name for name in names for token in _LEGACY_PATHS)
    assert not any(name.endswith("/changelog.md") for name in names)
    expected = {"/license", "/pkg-info", "/readme.md", "/pyproject.toml"} | source
    assert {name[name.index("/") :] for name in names} == expected


def test_should_keep_one_bounded_integration_paragraph_and_no_legacy_product_copy(
    built_artifacts: tuple[Path, Path],
) -> None:
    wheel, sdist = built_artifacts
    for text in (_wheel_metadata(wheel), _sdist_readme(sdist)):
        assert text.count(_BOUNDARY) == 1
        remainder = text.replace(_BOUNDARY, "")
        assert "avow" not in _words(remainder)
        assert not set(_LEGACY_WORDS) & set(_words(remainder))


def test_should_keep_long_description_links_valid_outside_the_sdist(
    built_artifacts: tuple[Path, Path],
) -> None:
    wheel, sdist = built_artifacts
    for text in (_wheel_metadata(wheel), _sdist_readme(sdist)):
        assert all(link in text for link in _ARTIFACT_SAFE_LINKS)
        assert "](QUICKSTART.md)" not in text
        assert "](docs/" not in text


def test_should_scan_every_sdist_text_for_legacy_product_copy(
    built_artifacts: tuple[Path, Path], tmp_path: Path
) -> None:
    _, sdist = built_artifacts
    texts = _extracted_texts(sdist, tmp_path)
    readme = next(text for name, text in texts if name == "README.md")
    assert readme.count(_BOUNDARY) == 1
    boundary_files = {name for name, text in texts if _BOUNDARY in text}
    assert boundary_files == {"PKG-INFO", "README.md"}
    for name, text in texts:
        assert text.count(_BOUNDARY) == (1 if name in boundary_files else 0)
        remainder = text.replace(_BOUNDARY, "").lower()
        remainder = _without_required_migration_words(name, remainder)
        assert not set(_LEGACY_PRODUCT_WORDS) & set(_words(remainder))
        legacy_key_phrases = tuple(phrase for phrase in _LEGACY_KEY_PHRASES if phrase in remainder)
        assert not legacy_key_phrases, (name, legacy_key_phrases)
