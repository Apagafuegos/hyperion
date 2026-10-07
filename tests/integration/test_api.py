"""HTTP contract: auth, health, snapshot, logs, and error envelope."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from hyperion import main as main_module
from hyperion.main import create_app
from hyperion.providers.fixture import FixtureProbeProvider, FixtureRuntimeProvider
from hyperion.settings import Settings

FIXTURES = Path(__file__).parents[1] / "fixtures"


def get(client, path, identity=True, **kwargs):
    headers = kwargs.pop("headers", {})
    if identity:
        headers["X-Authentik-Username"] = "owner"
        headers["X-Management-Proxy-Secret"] = "hyperion-test-proxy-proof"
        headers["X-Authentik-Groups"] = "authentik Admins"
    return client.get(path, headers=headers, **kwargs)


def test_healthz_does_not_require_identity(client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_ok_after_initial_snapshot(client) -> None:
    response = get(client, "/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_identity_required_for_snapshot(client) -> None:
    response = client.get("/api/v1/snapshot")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert body["error"]["retryable"] is False


def test_snapshot_shape_and_content(client) -> None:
    response = get(client, "/api/v1/snapshot")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=0, must-revalidate"
    payload = response.json()
    assert payload["schemaVersion"] == 1
    assert payload["fresh"] is True
    assert len(payload["services"]) == 10
    assert [t["id"] for t in payload["territories"]] == [
        "applications",
        "services",
        "foundations",
    ]
    summary = payload["summary"]
    assert summary["total"] == 10
    assert summary["dormant"] == 1
    states = {s["id"]: s["state"] for s in payload["services"]}
    assert states["t3-code"] == "reachable"
    assert states["demo-dormant"] == "dormant"
    assert states["librechat"] == "down"
    assert states["langfuse"] == "down"
    assert states["authentik"] == "degraded"  # slow route
    assert states["rarecord"] == "degraded"  # unhealthy dependency
    assert states["mcp-observatory"] == "degraded"  # health starting
    assert states["the-vault"] == "reachable"
    assert states["shared-postgres"] == "reachable"
    assert len(payload["diagnostics"]["unmappedRuntimes"]) == 3


def test_snapshot_etag_round_trip(client) -> None:
    first = get(client, "/api/v1/snapshot")
    etag = first.headers["etag"]
    second = get(client, "/api/v1/snapshot", headers={"If-None-Match": etag})
    assert second.status_code == 304


def test_logs_endpoint(client) -> None:
    response = get(client, "/api/v1/services/authentik/logs?tail=50")
    assert response.status_code == 200
    payload = response.json()
    assert payload["serviceId"] == "authentik"
    assert payload["records"]
    assert payload["records"][0]["provider"] == "docker"
    assert response.headers["cache-control"] == "private, no-store"


def test_logs_source_filter(client) -> None:
    response = get(client, "/api/v1/services/authentik/logs?source=worker&tail=50")
    payload = response.json()
    assert payload["source"] == "worker"
    assert all(r["source"] == "worker" for r in payload["records"])


def test_logs_unknown_service(client) -> None:
    response = get(client, "/api/v1/services/nope/logs")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SERVICE_NOT_FOUND"


def test_logs_unknown_source(client) -> None:
    response = get(client, "/api/v1/services/authentik/logs?source=ghost")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "LOG_SOURCE_NOT_FOUND"


def test_logs_invalid_tail(client) -> None:
    response = get(client, "/api/v1/services/authentik/logs?tail=999")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_TAIL"


def test_logs_invalid_before(client) -> None:
    response = get(client, "/api/v1/services/authentik/logs?before=not-a-date")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_BEFORE"


def test_logs_invalid_source_hits_generic_invalid_request(client) -> None:
    response = get(client, "/api/v1/services/authentik/logs?source=" + "a" * 200)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_index_served_under_identity(client) -> None:
    response = get(client, "/")
    assert response.status_code == 200
    assert "Hyperion" in response.text


class FlakyRuntime:
    """Fails the first observe call, then delegates to the fixture provider."""

    def __init__(self) -> None:
        self._inner = FixtureRuntimeProvider(
            FIXTURES / "fixture-evidence.json", FIXTURES / "fixture-logs.json"
        )
        self._calls = 0

    async def observe(self, catalog):
        self._calls += 1
        if self._calls == 1:
            raise RuntimeError("transient docker failure")
        return await self._inner.observe(catalog)

    async def read_logs(self, binding, tail, before):
        return await self._inner.read_logs(binding, tail, before)


def _fixture_app_settings(state_dir: Path) -> Settings:
    settings = Settings.from_env()
    return Settings(
        catalog_path=FIXTURES / "fixture-services.yaml",
        docker_host=settings.docker_host,
        bind_host=settings.bind_host,
        bind_port=settings.bind_port,
        log_level=settings.log_level,
        fixture_mode=True,
        state_dir=state_dir,
    )


def test_snapshot_becomes_ready_after_transient_initial_failure(monkeypatch, tmp_path) -> None:
    """A failed initial cycle must not prevent the supervisor from self-healing."""
    monkeypatch.setattr(main_module, "_build_runtime_provider", lambda settings: FlakyRuntime())
    monkeypatch.setattr(
        main_module,
        "_build_probe_provider",
        lambda settings: FixtureProbeProvider(FIXTURES / "fixture-probes.json"),
    )
    app = create_app(settings=_fixture_app_settings(tmp_path / "state"))
    with TestClient(app) as client:
        deadline = time.monotonic() + 5
        response = None
        while time.monotonic() < deadline:
            response = get(client, "/readyz")
            if response.status_code == 200:
                break
            time.sleep(0.05)
        assert response is not None and response.status_code == 200
        snapshot = get(client, "/api/v1/snapshot")
        assert snapshot.status_code == 200
        assert snapshot.json()["fresh"] is True
