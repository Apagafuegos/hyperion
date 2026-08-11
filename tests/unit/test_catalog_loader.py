"""Catalog loading, semantic validation, and revision hashing."""

from __future__ import annotations

from pathlib import Path

import pytest

from hyperion.catalog import (
    CatalogError,
    DiscoveredDockerComponent,
    catalog_revision,
    load_catalog,
    merge_discovered_docker,
)

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


def test_rejects_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "services.yaml"
    path.write_bytes(b"\xff\xfe\x00garbage")
    with pytest.raises(CatalogError):
        load_catalog(path)


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


def discovered(project: str, component: str, **labels: str) -> DiscoveredDockerComponent:
    return DiscoveredDockerComponent(project=project, component=component, labels=labels)


def test_docker_discovery_promotes_an_unknown_compose_project(
    fixture_catalog_path: Path,
) -> None:
    merged = merge_discovered_docker(
        load_catalog(fixture_catalog_path),
        [discovered("caddy", "caddy")],
    )
    service = next(item for item in merged.services if item.service_id == "caddy")
    assert service.name == "Caddy"
    assert service.runtime.provider == "docker-compose"
    assert service.runtime.project == "caddy"
    assert service.runtime.components[0].selector == "caddy"
    assert service.runtime.components[0].role == "primary"
    assert service.logs.sources == ["caddy"]


def test_docker_discovery_reads_service_metadata_labels(
    fixture_catalog_path: Path,
) -> None:
    merged = merge_discovered_docker(
        load_catalog(fixture_catalog_path),
        [
            discovered(
                "hybrid_rag",
                "api",
                **{
                    "hyperion.primary": "true",
                    "hyperion.id": "hybrid-rag",
                    "hyperion.name": "Hybrid RAG",
                    "hyperion.description": "Retrieval service",
                    "hyperion.territory": "services",
                    "hyperion.kind": "api",
                    "hyperion.url": "https://rag.example.com",
                    "hyperion.probe": "true",
                },
            ),
            discovered(
                "hybrid_rag",
                "qdrant",
                **{"hyperion.role": "dependency", "hyperion.required": "true"},
            ),
        ],
    )
    service = next(item for item in merged.services if item.service_id == "hybrid-rag")
    assert service.name == "Hybrid RAG"
    assert service.kind == "api"
    assert service.action.type == "open"
    assert service.route_probe is not None
    assert {item.selector: item.role for item in service.runtime.components} == {
        "api": "primary",
        "qdrant": "dependency",
    }


def test_docker_discovery_extends_existing_catalog_components(
    fixture_catalog_path: Path,
) -> None:
    merged = merge_discovered_docker(
        load_catalog(fixture_catalog_path),
        [discovered("authentik", "redis")],
    )
    service = next(item for item in merged.services if item.service_id == "authentik")
    assert service.runtime.provider == "docker-compose"
    redis = next(item for item in service.runtime.components if item.selector == "redis")
    assert redis.role == "sidecar"
    assert redis.required is False
    assert "redis" in service.logs.sources


def test_docker_discovery_honors_project_opt_out(fixture_catalog_path: Path) -> None:
    merged = merge_discovered_docker(
        load_catalog(fixture_catalog_path),
        [discovered("deploy", "docker-proxy", **{"hyperion.enabled": "false"})],
    )
    assert all(
        service.runtime.provider != "docker-compose" or service.runtime.project != "deploy"
        for service in merged.services
    )
