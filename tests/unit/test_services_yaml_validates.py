"""The committed production catalog must validate structurally and semantically."""

from __future__ import annotations

from pathlib import Path

from hyperion.catalog import catalog_revision, load_catalog

PRODUCTION_CATALOG = Path(__file__).parents[2] / "services.yaml"


def test_production_catalog_validates() -> None:
    catalog = load_catalog(PRODUCTION_CATALOG)
    assert catalog.version == 1
    assert len(catalog.services) >= 8
    for service in catalog.services:
        assert service.runtime.components
        assert len([c for c in service.runtime.components if c.role == "primary"]) == 1


def test_production_catalog_revision_is_stable() -> None:
    a = load_catalog(PRODUCTION_CATALOG)
    b = load_catalog(PRODUCTION_CATALOG)
    assert catalog_revision(a) == catalog_revision(b)
