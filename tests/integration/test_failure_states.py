"""Material failure states from TECHNICAL-DESIGN.md section 14.

The catalog-related states are reachable in fixture mode: the app stays up
with health green while readiness and the snapshot API report
SNAPSHOT_UNAVAILABLE until a valid snapshot exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hyperion import main as main_module
from hyperion.main import create_app
from hyperion.settings import Settings

FIXTURES = Path(__file__).parents[1] / "fixtures"

DUPLICATE_IDS = """\
version: 1
services:
  - id: dup
    name: Dup One
    description: First duplicate id entry
    territory: services
    kind: api
    action:
      type: none
    runtime:
      provider: systemd
      components:
        - selector: dup1.service
          role: primary
          required: true
    logs:
      sources: [dup1.service]
  - id: dup
    name: Dup Two
    description: Second duplicate id entry
    territory: services
    kind: api
    action:
      type: none
    runtime:
      provider: systemd
      components:
        - selector: dup2.service
          role: primary
          required: true
    logs:
      sources: [dup2.service]
"""


def _settings(catalog_path: Path) -> Settings:
    base = Settings.from_env()
    return Settings(
        catalog_path=catalog_path,
        docker_host=base.docker_host,
        bind_host=base.bind_host,
        bind_port=base.bind_port,
        log_level=base.log_level,
        fixture_mode=True,
    )


def get(client, path, **kwargs):
    headers = kwargs.pop("headers", {})
    headers["X-Authentik-Username"] = "owner"
    return client.get(path, headers=headers, **kwargs)


def _assert_unavailable(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200
    ready = client.get("/readyz")
    assert ready.status_code == 503
    assert ready.json()["error"]["code"] == "SNAPSHOT_UNAVAILABLE"
    snapshot = client.get("/api/v1/snapshot", headers={"X-Authentik-Username": "owner"})
    assert snapshot.status_code == 503
    assert snapshot.json()["error"]["code"] == "SNAPSHOT_UNAVAILABLE"


def test_missing_catalog_keeps_health_up_but_not_ready(tmp_path) -> None:
    settings = _settings(tmp_path / "missing.yaml")
    with TestClient(create_app(settings=settings)) as client:
        _assert_unavailable(client)


def test_valid_catalog_with_no_services(tmp_path) -> None:
    catalog = tmp_path / "empty.yaml"
    catalog.write_text("version: 1\nservices: []\n", encoding="utf-8")
    with TestClient(create_app(settings=_settings(catalog))) as client:
        payload = get(client, "/api/v1/snapshot").json()
        assert payload["summary"]["total"] == 0
        assert [t["id"] for t in payload["territories"]] == [
            "applications",
            "services",
            "foundations",
        ]


@pytest.mark.parametrize(
    "catalog_text",
    [
        pytest.param(DUPLICATE_IDS, id="duplicate-service-ids"),
        pytest.param("version: 1\nservices:\n  - broken: [\n", id="invalid-yaml"),
    ],
)
def test_invalid_catalog_keeps_health_up_but_not_ready(tmp_path, catalog_text) -> None:
    catalog = tmp_path / "invalid.yaml"
    catalog.write_text(catalog_text, encoding="utf-8")
    with TestClient(create_app(settings=_settings(catalog))) as client:
        _assert_unavailable(client)


class BrokenRuntime:
    """A runtime provider that is permanently unavailable."""

    async def observe(self, catalog):
        raise RuntimeError("docker unavailable")

    async def read_logs(self, binding, tail, before):
        raise RuntimeError("docker unavailable")


def test_provider_unavailable_keeps_health_up_but_not_ready(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "_build_runtime_provider", lambda settings: BrokenRuntime())
    with TestClient(
        create_app(settings=_settings(FIXTURES / "fixture-services.yaml"))
    ) as client:
        _assert_unavailable(client)


def test_provider_unavailable_logs_report_unavailable(monkeypatch) -> None:
    # t3-code is a systemd source: the unit name is the reference, so the
    # gateway reaches read_logs directly and the broken provider surfaces
    # as a bounded LOG_SOURCE_UNAVAILABLE (docker references cannot resolve
    # at all without a published snapshot, so systemd is the path that
    # exercises the provider boundary).
    monkeypatch.setattr(main_module, "_build_runtime_provider", lambda settings: BrokenRuntime())
    with TestClient(
        create_app(settings=_settings(FIXTURES / "fixture-services.yaml"))
    ) as client:
        response = client.get(
            "/api/v1/services/t3-code/logs?tail=50",
            headers={"X-Authentik-Username": "owner"},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "LOG_SOURCE_UNAVAILABLE"
