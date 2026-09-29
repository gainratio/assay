"""README contract: plain English first, real output, every technical doc one link away."""

from __future__ import annotations

import contextlib
import io
import json
import re
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_README = _ROOT / "README.md"
_REPO = "https://github.com/hseshadr/assay/blob/main/"
_TRY = "## Try it"
_SECTIONS = (
    "## Try it",
    "## How it works",
    "## What it does not do",
    "## When to use something else",
    "## Install",
    "## Develop",
    "## More detail",
    "## License",
)
_TECHNICAL_DOCS = "**Technical docs:**"
_MAP_TEXT = "Explore the interactive architecture map"
_MAP_PAGE = "docs/architecture/index.html"
_MAP_SOURCE = _ROOT / "docs" / "architecture" / "runtime.architecture.json"
_MAX_TAGLINE = 120
_MAX_BADGES = 3
_OUTPUT_CAPTION = "Real output:"
# Internal vocabulary and hype the owner banned from READMEs, plus the retired template.
_BANNED = (
    "northstar",
    "seam",
    "lego",
    "trust envelope",
    "fail-closed",
    "gate",
    "fleet",
    "portfolio",
    "production-ready",
    "robust",
    "blazing",
    "enterprise-grade",
    "seamless",
    "at a glance",
    "try it in 60 seconds",
    "below the fold",
)
_LINK_TARGET = re.compile(r"\]\(([^)\s]+)\)")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_CODE = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)


def _readme() -> str:
    return _README.read_text(encoding="utf-8")


def _lines() -> list[str]:
    return [line for line in _readme().splitlines() if line.strip()]


def _section(heading: str) -> str:
    after = _readme().split(f"\n{heading}\n", maxsplit=1)[1]
    return after.split("\n## ", maxsplit=1)[0]


def _fenced(text: str, language: str) -> str:
    block = re.search(rf"^```{language}\n(.*?)^```$", text, re.MULTILINE | re.DOTALL)
    assert block is not None, language
    return block.group(1)


def test_should_open_with_name_and_one_sentence_equal_to_both_package_descriptions() -> None:
    # Given the README and both package manifests
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((_ROOT / "ts" / "package.json").read_text(encoding="utf-8"))
    # When the title and first sentence are read
    title, tagline = _lines()[:2]
    # Then one short plain sentence is the single description everywhere
    assert title == "# Assay"
    assert len(tagline) <= _MAX_TAGLINE
    assert tagline.endswith(".")
    assert tagline == project["project"]["description"] == package["description"]


def test_should_put_the_fastest_try_line_in_bold_right_under_the_first_sentence() -> None:
    # Given the third non-blank line
    try_line = _lines()[2]
    # Then it is a bold one-line install a stranger can copy
    assert try_line.startswith("**")
    assert "`pip install assay-engine`" in try_line


def test_should_link_architecture_and_getting_started_before_try_it() -> None:
    # Given the intro above the first section
    intro = _readme().split(f"\n{_TRY}\n", maxsplit=1)[0]
    line = next(line for line in intro.splitlines() if line.startswith(_TECHNICAL_DOCS))
    # Then the technical-docs line points at the architecture and developer guide
    assert f"{_REPO}docs/ARCHITECTURE.md" in line
    assert f"{_REPO}docs/GETTING_STARTED.md" in line


def test_should_keep_the_sections_in_the_standard_order() -> None:
    # Given every second-level heading
    headings = tuple(line for line in _readme().splitlines() if line.startswith("## "))
    # Then they are exactly the standard sections, once each, in order
    assert headings == _SECTIONS


def test_should_keep_badges_bounded() -> None:
    # Given the intro above the first section
    intro = _readme().split(f"\n{_TRY}\n", maxsplit=1)[0]
    # Then at most CI, license, and version badges compete with the first sentence
    assert intro.count("[![") <= _MAX_BADGES


@pytest.mark.parametrize("word", _BANNED)
def test_should_not_use_banned_jargon_outside_code(word: str) -> None:
    # Given the README prose with code blocks and inline code removed
    prose = _CODE.sub("", _readme()).lower()
    # Then internal vocabulary and hype are absent
    assert re.search(rf"\b{re.escape(word)}\b", prose) is None


def test_should_link_every_technical_doc_from_more_detail() -> None:
    # Given the More detail section and the repository's technical documents
    section = _section("## More detail")
    documents = (
        *sorted(path.relative_to(_ROOT).as_posix() for path in (_ROOT / "docs").glob("*.md")),
        "QUICKSTART.md",
        "ts/README.md",
        "CHANGELOG.md",
        "SECURITY.md",
    )
    # Then each one is linked by its absolute repository URL
    missing = tuple(doc for doc in documents if f"]({_REPO}{doc})" not in section)
    assert missing == ()
    assert "docs/ARCHITECTURE.md" in documents
    assert "docs/GETTING_STARTED.md" in documents


def test_should_state_the_mit_license_last() -> None:
    # Given the final section
    section = _section("## License")
    # Then it names MIT and links the license file
    assert section.lstrip().startswith("MIT.")
    assert f"]({_REPO}LICENSE)" in section


def test_should_link_getting_started_from_develop() -> None:
    # Given the Develop section
    # Then a new developer is pointed at the step-by-step guide
    assert f"]({_REPO}docs/GETTING_STARTED.md)" in _section("## Develop")


def test_should_link_the_interactive_architecture_map_and_its_source() -> None:
    # Given every link labelled as the architecture map
    targets = re.findall(rf"\[{re.escape(_MAP_TEXT)}[^\]]*\]\(([^)\s]+)\)", _readme())
    # Then it points at the generated page, whose source exists on disk
    assert targets
    assert all(target.endswith(_MAP_PAGE) for target in targets)
    assert (_ROOT / _MAP_PAGE).is_file()
    assert _MAP_SOURCE.is_file()


def test_should_resolve_every_relative_link() -> None:
    # Given every link destination without a URL scheme or pure anchor
    targets = (target.split("#", maxsplit=1)[0] for target in _LINK_TARGET.findall(_readme()))
    relative = tuple(target for target in targets if target and not _SCHEME.match(target))
    # Then each one names a file or directory in this checkout
    assert tuple(target for target in relative if not (_ROOT / target).exists()) == ()


def test_should_print_exactly_the_documented_output_from_the_try_it_example() -> None:
    # Given the runnable Python example and the output printed under it
    section = _section(_TRY)
    source = _fenced(section, "python")
    documented = _fenced(section.split(_OUTPUT_CAPTION, maxsplit=1)[1], "text")
    # When the example runs against this checkout
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        exec(compile(source, "README.md", "exec"), {})  # noqa: S102 - README example is the subject
    # Then the README shows the real output
    assert printed.getvalue() == documented
