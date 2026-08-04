"""Log gateway resolves docker references from the latest snapshot evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hyperion.api.errors import ApiError
from hyperion.catalog import load_catalog
from hyperion.providers.base import LogBinding, LogRecordIn
from hyperion.services.logs import LogGateway

FIXTURES = Path(__file__).parents[1] / "fixtures"

NOW = datetime(2026, 8, 4, 6, 0, 0, tzinfo=UTC)


class FakeRuntime:
    """RuntimeProvider that records bindings and serves canned records."""

    def __init__(self, records_by_reference: dict[str, list[LogRecordIn]]) -> None:
        self.records_by_reference = records_by_reference
        self.bindings: list[LogBinding] = []

    async def observe(self, catalog):
        return []

    async def read_logs(self, binding, tail, before):
        self.bindings.append(binding)
        return self.records_by_reference.get(binding.reference, [])


def rec(reference: str, message: str = "line") -> LogRecordIn:
    return LogRecordIn(
        timestamp=NOW,
        source="s",
        provider="docker",
        stream="stdout",
        severity="info",
        message=message,
    )


def make_gateway(references, records) -> tuple[LogGateway, FakeRuntime]:
    runtime = FakeRuntime(records)
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    gateway = LogGateway(runtime, catalog, resolve_references=lambda: references)
    return gateway, runtime


async def test_docker_binding_uses_container_reference() -> None:
    references = {("authentik", "server"): "authentik-server-1"}
    records = {"authentik-server-1": [rec("authentik-server-1", "hello")]}
    gateway, runtime = make_gateway(references, records)
    response = await gateway.read("authentik", "server", 100, None)
    assert response.records[0].message == "hello"
    assert runtime.bindings[0].provider == "docker"
    assert runtime.bindings[0].reference == "authentik-server-1"


async def test_systemd_binding_uses_unit_selector() -> None:
    runtime = FakeRuntime({"t3code.service": [rec("t3code.service", "unit log")]})
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    gateway = LogGateway(runtime, catalog, resolve_references=lambda: {})
    response = await gateway.read("t3-code", "t3code.service", 100, None)
    assert response.records[0].message == "unit log"
    assert runtime.bindings[0].provider == "systemd"
    assert runtime.bindings[0].reference == "t3code.service"


async def test_docker_without_resolved_reference_raises_unavailable() -> None:
    gateway, runtime = make_gateway({}, {})
    with pytest.raises(ApiError) as exc:
        await gateway.read("authentik", "server", 100, None)
    assert exc.value.status_code == 503
    assert exc.value.code == "LOG_SOURCE_UNAVAILABLE"
    assert exc.value.retryable is True
    assert runtime.bindings == []


async def test_resolve_references_builds_map_from_snapshot_store() -> None:
    from hyperion.main import _resolve_log_references
    from hyperion.providers.fixture import FixtureProbeProvider, FixtureRuntimeProvider
    from hyperion.services.reconciler import reconcile
    from hyperion.services.snapshots import SnapshotStore

    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    runtime = FixtureRuntimeProvider(
        FIXTURES / "fixture-evidence.json", FIXTURES / "fixture-logs.json"
    )
    probe = FixtureProbeProvider(FIXTURES / "fixture-probes.json")
    store = SnapshotStore()
    urls = tuple(s.route_probe.url for s in catalog.services if s.route_probe is not None)
    store.publish(
        reconcile(
            catalog,
            await runtime.observe(catalog),
            await probe.observe(urls),
        )
    )
    references = _resolve_log_references(store)()
    assert references[("authentik", "server")] == "authentik-server-1"
    assert references[("authentik", "worker")] == "authentik-worker-1"
    assert ("t3-code", "t3code.service") not in references
    assert all(key[0] != "t3-code" for key in references)
