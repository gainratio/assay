"""The npm package publishes as @gainratio/assay; the old @edgeproc scope must not creep back."""

from __future__ import annotations

import json
from pathlib import Path

_OLD_FORMS = ("@edgeproc/", "%40edgeproc", "edgeproc-assay-")


def _release_surfaces() -> tuple[Path, ...]:
    workflows = tuple(sorted(Path(".github/workflows").glob("*.yml")))
    scripts = tuple(sorted(Path("scripts").glob("*.py"))) + tuple(Path("scripts").glob("*.sh"))
    dagger = tuple(sorted(Path(".dagger/src").rglob("*.py")))
    return (Path("ts/package.json"), *workflows, *scripts, *dagger)


def test_should_publish_under_gainratio_scope() -> None:
    # Given the npm manifest
    manifest = json.loads(Path("ts/package.json").read_text(encoding="utf-8"))
    # Then it declares the new scope
    assert manifest["name"] == "@gainratio/assay"


def test_should_not_name_old_scope_in_release_surfaces() -> None:
    # Given every file that decides what gets built, published or verified
    surfaces = _release_surfaces()
    # When each is searched for the retired scope in plain, URL-encoded or tarball form
    offenders = [
        str(path)
        for path in surfaces
        if any(form in path.read_text(encoding="utf-8") for form in _OLD_FORMS)
    ]
    # Then none still names it
    assert len(surfaces) > 5
    assert offenders == []
