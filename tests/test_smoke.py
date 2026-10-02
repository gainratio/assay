"""Smoke test: the package imports and exposes a semantic version.

The cheapest possible failure signal — if this breaks, the package does not import at
all and every richer test below it is reporting on nothing."""

from __future__ import annotations

import assay


def test_should_expose_development_version_when_imported() -> None:
    # Given the installed assay package
    # When reading its version string
    version = assay.__version__
    # Then it matches the scoring-only split candidate
    assert version == "0.5.0.dev4"
