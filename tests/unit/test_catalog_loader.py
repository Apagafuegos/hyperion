"""Catalog loading, semantic validation, and revision hashing."""

from __future__ import annotations

from pathlib import Path

import pytest

from hyperion.catalog import CatalogError, catalog_revision, load_catalog

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "services.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_valid_manifest(fixture_catalog_path: Path) -> None:
    catalog = load_catalog(fixture_catalog_path)
    assert len(catalog.services) == 10
    langfuse = next(s for s in catalog.services if s.service_id == "langfuse")
    assert langfuse.intent == "active"
    assert langfuse.route_probe is not None
    assert langfuse.route_probe.accepted_status_classes == [2, 3, 4]
    assert langfuse.logs.default_tail == 100


def test_rejects_duplicate_ids(tmp_path: Path) -> None:
    body = """
version: 1
services:
  - id: alpha
    name: Alpha
    description: one
    territory: applications
    kind: web
    action: {type: open, url: "https://alpha.example.com"}
    runtime:
      provider: systemd
      components: [{selector: alpha.service, role: primary, required: true}]
    logs: {sources: [alpha.service]}
  - id: alpha
    name: Alpha Two
    description: two
    territory: services
    kind: api
    action: {type: none}
    runtime:
      provider: systemd
      components: [{selector: alpha2.service, role: primary, required: true}]
    logs: {sources: [alpha2.service]}
"""
    with pytest.raises(CatalogError, match="unique"):
        load_catalog(_write(tmp_path, body))


def test_rejects_missing_primary(tmp_path: Path) -> None:
    body = """
version: 1
services:
  - id: alpha
    name: Alpha
    description: one
    territory: applications
    kind: web
    action: {type: open, url: "https://alpha.example.com"}
    runtime:
      provider: systemd
      components: [{selector: alpha.service, role: worker, required: true}]
    logs: {sources: [alpha.service]}
"""
    with pytest.raises(CatalogError, match="primary"):
        load_catalog(_write(tmp_path, body))


def test_rejects_log_source_not_declared(tmp_path: Path) -> None:
    body = """
version: 1
services:
  - id: alpha
    name: Alpha
    description: one
    territory: applications
    kind: web
    action: {type: open, url: "https://alpha.example.com"}
    runtime:
      provider: systemd
      components: [{selector: alpha.service, role: primary, required: true}]
    logs: {sources: [alpha.service, ghost.service]}
"""
    with pytest.raises(CatalogError, match="not declared"):
        load_catalog(_write(tmp_path, body))


def test_rejects_unknown_dependency_and_self_reference(tmp_path: Path) -> None:
    body = """
version: 1
services:
  - id: alpha
    name: Alpha
    description: one
    territory: applications
    kind: web
    action: {type: open, url: "https://alpha.example.com"}
    runtime:
      provider: systemd
      components: [{selector: alpha.service, role: primary, required: true}]
    logs: {sources: [alpha.service]}
    dependencies: [alpha, ghost]
"""
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, body))


def test_rejects_shared_docker_ownership_without_shared_role(tmp_path: Path) -> None:
    def service(service_id: str, project: str) -> str:
        return f"""
  - id: {service_id}
    name: {service_id}
    description: one
    territory: applications
    kind: web
    action: {{type: open, url: "https://{service_id}.example.com"}}
    runtime:
      provider: docker-compose
      project: {project}
      components: [{{selector: web, role: primary, required: true}}]
    logs: {{sources: [web]}}
"""

    body = "version: 1\nservices:" + service("alpha", "p1") + service("beta", "p1")
    with pytest.raises(CatalogError, match="multiple services"):
        load_catalog(_write(tmp_path, body))


def test_unknown_properties_rejected(tmp_path: Path) -> None:
    body = """
version: 1
services:
  - id: alpha
    name: Alpha
    description: one
    territory: applications
    kind: web
    action: {type: open, url: "https://alpha.example.com"}
    runtime:
      provider: systemd
      components: [{selector: alpha.service, role: primary, required: true}]
    logs: {sources: [alpha.service]}
    invented: true
"""
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, body))


def test_follow_redirects_must_be_false(tmp_path: Path) -> None:
    body = """
version: 1
services:
  - id: alpha
    name: Alpha
    description: one
    territory: applications
    kind: web
    action: {type: open, url: "https://alpha.example.com"}
    runtime:
      provider: systemd
      components: [{selector: alpha.service, role: primary, required: true}]
    routeProbe:
      method: GET
      url: "https://alpha.example.com"
      followRedirects: true
    logs: {sources: [alpha.service]}
"""
    with pytest.raises(CatalogError, match="followRedirects"):
        load_catalog(_write(tmp_path, body))


def test_revision_is_stable_and_sensitive(fixture_catalog_path: Path) -> None:
    a = load_catalog(fixture_catalog_path)
    b = load_catalog(fixture_catalog_path)
    assert catalog_revision(a) == catalog_revision(b)
    assert len(catalog_revision(a)) == 64
    changed = a.model_copy(deep=True)
    changed.services[0].description = "changed"
    assert catalog_revision(changed) != catalog_revision(a)
