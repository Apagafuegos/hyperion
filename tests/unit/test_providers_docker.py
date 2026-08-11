"""Docker provider maps SDK state to component evidence; exact table from section 6.4."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hyperion.catalog import load_catalog
from hyperion.providers.base import LogBinding
from hyperion.providers.docker import DockerProvider

FIXTURES = Path(__file__).parents[1] / "fixtures"

STATS = {
    "cpu_stats": {
        "cpu_usage": {"total_usage": 2000, "percpu_usage": [1000, 1000]},
        "system_cpu_usage": 10000,
    },
    "precpu_stats": {
        "cpu_usage": {"total_usage": 1000, "percpu_usage": [500, 500]},
        "system_cpu_usage": 8000,
    },
    "memory_stats": {"usage": 104857600},
}

FIRST_SAMPLE = {
    "cpu_stats": STATS["cpu_stats"],
    "precpu_stats": {},
    "memory_stats": {"usage": 104857600},
}


def labels(project: str, service: str) -> dict:
    return {
        "com.docker.compose.project": project,
        "com.docker.compose.service": service,
    }


class FakeContainer:
    def __init__(
        self,
        container_id: str,
        name: str,
        labels: dict,
        state: str,
        health: str | None = None,
        image: str = "img",
        started: str = "",
        restart_count: int = 0,
        tty: bool = False,
        stats_result: dict | None = None,
    ) -> None:
        self.id = container_id
        self.name = name
        self._stats_result = stats_result
        self._log_calls: list[dict] = []
        self.attrs = {
            "Id": container_id,
            "Name": f"/{name}",
            "Config": {"Labels": labels, "Image": image, "Tty": tty},
            "State": {
                "Status": state,
                "StartedAt": started,
                "Health": {"Status": health} if health else None,
            },
            "RestartCount": restart_count,
        }

    def stats(self, stream: bool = False) -> dict:
        if self._stats_result is None:
            raise RuntimeError("stats unavailable")
        return self._stats_result

    def logs(self, **kwargs) -> bytes:
        self._log_calls.append(dict(kwargs))
        stdout = kwargs.get("stdout", False)
        stderr = kwargs.get("stderr", False)
        if stdout:
            return (
                b"2026-08-04T06:00:01.000000000Z hello out\n"
                b"2026-08-04T06:00:02.000000000Z second out\n"
            )
        if stderr:
            return b"2026-08-04T06:00:03.000000000Z oops\n"
        return b""


def default_containers() -> list[FakeContainer]:
    return [
        FakeContainer(
            "c1", "authentik-server-1", labels("authentik", "server"),
            "running", health="healthy", image="img:1", started="2026-08-03T06:00:00Z",
            restart_count=0, stats_result=STATS,
        ),
        FakeContainer(
            "c2", "authentik-worker-1", labels("authentik", "worker"),
            "running", health="starting", image="img:1", stats_result=STATS,
        ),
        FakeContainer(
            "c3", "langfuse-langfuse-1", labels("langfuse", "langfuse"),
            "exited", image="img:2", restart_count=4,
        ),
        FakeContainer(
            "c4", "rarecord-app-1", labels("rarecord", "app"),
            "running", health="unhealthy", image="img:3", stats_result=STATS,
        ),
        FakeContainer(
            "c5", "caddy-1", labels("caddy", "caddy"),
            "running", image="caddy:2",
        ),
    ]


def make_client(containers: list[FakeContainer] | None = None):
    containers = list(default_containers() if containers is None else containers)
    created: list = []

    class FakeClient:
        def __init__(self, base_url: str) -> None:
            self._base_url = base_url
            self._containers = containers
            self._collection = _FakeCollection(self._containers)
            self.closed = False

        @property
        def containers(self):
            return self._collection

        def close(self) -> None:
            self.closed = True

    class _FakeCollection:
        def __init__(self, containers: list) -> None:
            self._containers = containers
            self._list_calls: list[dict] = []

        def list(self, all: bool = False, ignore_removed: bool = False):
            self._list_calls.append({"all": all, "ignore_removed": ignore_removed})
            return self._containers

        def get(self, container_id: str):
            return next(
                c for c in self._containers if c.id == container_id or c.name == container_id
            )

    def factory(base_url: str) -> FakeClient:
        client = FakeClient(base_url)
        created.append(client)
        return client

    return factory, created


def run(coro):
    return asyncio.run(coro)


def test_observe_maps_evidence() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    factory, _ = make_client()
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    observation = run(provider.observe(catalog))
    assert observation.state == "available"
    by_key = {(c.service_id, c.selector): c for c in observation.components}
    server = by_key[("authentik", "server")]
    assert server.state == "running"
    assert server.health == "healthy"
    assert server.reference == "authentik-server-1"
    worker = by_key[("authentik", "worker")]
    assert worker.state == "starting"  # running + health starting -> Starting
    assert worker.health == "starting"
    langfuse = by_key[("langfuse", "langfuse")]
    assert langfuse.state == "stopped"
    assert langfuse.restart_count == 4
    assert langfuse.image == "img:2"


def test_observe_lists_with_ignore_removed() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    factory, created = make_client()
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    run(provider.observe(catalog))
    assert created[0].containers._list_calls == [{"all": True, "ignore_removed": True}]


def test_state_map_covers_created_paused_dead_removing() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    containers = [
        FakeContainer("m1", "authentik-server-1", labels("authentik", "server"), "created"),
        FakeContainer("m2", "authentik-worker-1", labels("authentik", "worker"), "paused"),
        FakeContainer("m3", "authentik-postgresql-1", labels("authentik", "postgresql"), "dead"),
        FakeContainer("m4", "langfuse-langfuse-1", labels("langfuse", "langfuse"), "removing"),
    ]
    factory, _ = make_client(containers)
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    observation = run(provider.observe(catalog))
    by_key = {(c.service_id, c.selector): c for c in observation.components}
    assert by_key[("authentik", "server")].state == "starting"  # created
    assert by_key[("authentik", "worker")].state == "paused"
    assert by_key[("authentik", "postgresql")].state == "stopped"  # dead
    assert by_key[("langfuse", "langfuse")].state == "stopped"  # removing


def test_health_unparseable_maps_unknown() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    containers = [
        FakeContainer("h1", "authentik-server-1", labels("authentik", "server"),
                      "running", health="weird"),
    ]
    factory, _ = make_client(containers)
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    observation = run(provider.observe(catalog))
    server = next(c for c in observation.components if c.selector == "server")
    assert server.state == "running"
    assert server.health == "unknown"


def test_stats_first_sample_has_no_cpu() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    containers = [
        FakeContainer("f1", "rarecord-app-1", labels("rarecord", "app"),
                      "running", stats_result=FIRST_SAMPLE),
    ]
    factory, _ = make_client(containers)
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    observation = run(provider.observe(catalog))
    app = next(c for c in observation.components if c.selector == "app")
    assert app.cpu_percent is None
    assert app.memory_bytes == 104857600


def test_read_logs_tty_uses_unknown_stream() -> None:
    containers = [
        FakeContainer("t1", "authentik-server-1", labels("authentik", "server"),
                      "running", tty=True),
    ]
    factory, _ = make_client(containers)
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    binding = LogBinding(
        provider="docker", service_id="authentik", source="server", reference="authentik-server-1"
    )
    records = run(provider.read_logs(binding, tail=10, before=None))
    assert len(records) == 2
    assert {r.stream for r in records} == {"unknown"}


def test_read_logs_passes_before_as_until() -> None:
    factory, created = make_client()
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    binding = LogBinding(
        provider="docker", service_id="authentik", source="server", reference="authentik-server-1"
    )
    before = datetime.now(UTC) - timedelta(minutes=5)
    run(provider.read_logs(binding, tail=3, before=before))
    container = created[0]._containers[0]
    assert len(container._log_calls) == 2
    assert all(call["until"] == int(before.timestamp()) for call in container._log_calls)
    assert all(call["tail"] == 3 for call in container._log_calls)


def test_unmapped_without_compose_labels() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    containers = [FakeContainer("n1", "orphan-1", {}, "running")]
    factory, _ = make_client(containers)
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    observation = run(provider.observe(catalog))
    assert len(observation.unmapped) == 1
    orphan = observation.unmapped[0]
    assert orphan.reference == "orphan-1"
    assert orphan.project is None
    assert orphan.component is None


def test_observe_reports_missing_and_unmapped() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    factory, _ = make_client()
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    observation = run(provider.observe(catalog))
    by_key = {(c.service_id, c.selector): c for c in observation.components}
    assert by_key[("shared-postgres", "postgres")].state == "missing"
    references = {u.reference for u in observation.unmapped}
    assert references == {"caddy-1"}
    assert len(observation.unmapped) == 1


def test_discover_catalog_promotes_unmapped_compose_projects() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    factory, _ = make_client()
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    enriched = run(provider.discover_catalog(catalog))
    assert any(service.service_id == "caddy" for service in enriched.services)
    observation = run(provider.observe(enriched))
    assert "caddy-1" not in {item.reference for item in observation.unmapped}
    assert any(component.service_id == "caddy" for component in observation.components)


def test_opted_out_compose_project_is_not_unmapped() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    excluded = labels("deploy", "docker-proxy") | {"hyperion.enabled": "false"}
    containers = [FakeContainer("p1", "docker-proxy", excluded, "running")]
    factory, _ = make_client(containers)
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    enriched = run(provider.discover_catalog(catalog))
    assert all(service.service_id != "deploy" for service in enriched.services)
    observation = run(provider.observe(enriched))
    assert observation.unmapped == []


def test_observe_captures_provider_failure() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")

    class BrokenClient:
        def __init__(self, base_url: str) -> None:
            pass

        @property
        def containers(self):
            class _BrokenCollection:
                def list(self, all: bool = False, ignore_removed: bool = False):
                    raise ConnectionError("connection refused to proxy")

            return _BrokenCollection()

    provider = DockerProvider(host="tcp://fake:2375", client_factory=BrokenClient)
    observation = run(provider.observe(catalog))
    assert observation.state == "unavailable"
    assert observation.error is not None
    assert observation.components == []


def test_observe_boundary_captures_attr_failures() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")

    class ExplodingContainer:
        @property
        def attrs(self) -> dict:
            raise RuntimeError("attrs unavailable")

    class ExplodingClient:
        def __init__(self, base_url: str) -> None:
            pass

        @property
        def containers(self):
            class _ExplodingCollection:
                def list(self, all: bool = False, ignore_removed: bool = False):
                    return [ExplodingContainer()]

            return _ExplodingCollection()

    provider = DockerProvider(host="tcp://fake:2375", client_factory=ExplodingClient)
    observation = run(provider.observe(catalog))
    assert observation.state == "unavailable"
    assert observation.error is not None
    assert observation.components == []


def test_stats_collected_for_running_owned_components() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    factory, _ = make_client()
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    observation = run(provider.observe(catalog))
    server = next(c for c in observation.components if c.selector == "server")
    assert server.cpu_percent is not None and server.cpu_percent > 0
    assert server.memory_bytes == 104857600


def test_observe_reuses_single_client() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    factory, created = make_client()
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    run(provider.observe(catalog))
    assert len(created) == 1


def test_observe_closes_client() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    factory, created = make_client()
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    run(provider.observe(catalog))
    assert created[0].closed is True


def test_read_logs_closes_client() -> None:
    factory, created = make_client()
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    binding = LogBinding(
        provider="docker", service_id="authentik", source="server", reference="authentik-server-1"
    )
    run(provider.read_logs(binding, tail=10, before=None))
    assert created[0].closed is True


def test_stats_collected_concurrently() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")

    class SlowContainer(FakeContainer):
        def stats(self, stream: bool = False) -> dict:
            time.sleep(0.05)
            return super().stats(stream=stream)

    containers = [
        SlowContainer("s1", "authentik-server-1", labels("authentik", "server"), "running",
                      stats_result=STATS),
        SlowContainer("s2", "authentik-worker-1", labels("authentik", "worker"), "running",
                      stats_result=STATS),
        SlowContainer("s3", "authentik-postgresql-1", labels("authentik", "postgresql"), "running",
                      stats_result=STATS),
        SlowContainer("s4", "rarecord-app-1", labels("rarecord", "app"), "running",
                      stats_result=STATS),
    ]
    factory, _ = make_client(containers)
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    start = time.monotonic()
    observation = run(provider.observe(catalog))
    elapsed = time.monotonic() - start
    assert elapsed < 0.15, f"stats collected sequentially: {elapsed:.3f}s"
    assert sum(1 for c in observation.components if c.cpu_percent is not None) == 4


def test_read_logs_demuxes_streams() -> None:
    factory, _ = make_client()
    provider = DockerProvider(host="tcp://fake:2375", client_factory=factory)
    binding = LogBinding(
        provider="docker", service_id="authentik", source="server", reference="authentik-server-1"
    )
    records = run(provider.read_logs(binding, tail=10, before=None))
    assert len(records) == 3
    streams = {r.stream for r in records}
    assert streams == {"stdout", "stderr"}
    assert all(r.timestamp is not None for r in records)
