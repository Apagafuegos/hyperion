"""Docker provider maps SDK state to component evidence; exact table from section 6.4."""

from __future__ import annotations

import asyncio
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


def make_client():
    containers = [
        FakeContainer(
            "c1", "authentik-server-1",
            {"com.docker.compose.project": "authentik", "com.docker.compose.service": "server"},
            "running", health="healthy", image="img:1", started="2026-08-03T06:00:00Z",
            restart_count=0, stats_result=STATS,
        ),
        FakeContainer(
            "c2", "authentik-worker-1",
            {"com.docker.compose.project": "authentik", "com.docker.compose.service": "worker"},
            "running", health="starting", image="img:1", stats_result=STATS,
        ),
        FakeContainer(
            "c3", "langfuse-langfuse-1",
            {"com.docker.compose.project": "langfuse", "com.docker.compose.service": "langfuse"},
            "exited", image="img:2", restart_count=4,
        ),
        FakeContainer(
            "c4", "rarecord-app-1",
            {"com.docker.compose.project": "rarecord", "com.docker.compose.service": "app"},
            "running", health="unhealthy", image="img:3", stats_result=STATS,
        ),
        FakeContainer(
            "c5", "caddy-1",
            {"com.docker.compose.project": "caddy", "com.docker.compose.service": "caddy"},
            "running", image="caddy:2",
        ),
    ]

    class FakeClient:
        def __init__(self, base_url: str) -> None:
            self._base_url = base_url
            self._containers = containers

        @property
        def containers(self):
            return _FakeCollection(self._containers)

    class _FakeCollection:
        def __init__(self, containers: list) -> None:
            self._containers = containers

        def list(self, all: bool = False):
            return self._containers

        def get(self, container_id: str):
            return next(
                c for c in self._containers if c.id == container_id or c.name == container_id
            )

    return FakeClient


def run(coro):
    return asyncio.run(coro)


def test_observe_maps_evidence() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    provider = DockerProvider(host="tcp://fake:2375", client_factory=make_client())
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


def test_observe_reports_missing_and_unmapped() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    provider = DockerProvider(host="tcp://fake:2375", client_factory=make_client())
    observation = run(provider.observe(catalog))
    by_key = {(c.service_id, c.selector): c for c in observation.components}
    assert by_key[("shared-postgres", "postgres")].state == "missing"
    references = {u.reference for u in observation.unmapped}
    assert references == {"caddy-1"}
    assert len(observation.unmapped) == 1


def test_observe_captures_provider_failure() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")

    class BrokenClient:
        def __init__(self, base_url: str) -> None:
            pass

        @property
        def containers(self):
            class _BrokenCollection:
                def list(self, all: bool = False):
                    raise ConnectionError("connection refused to proxy")

            return _BrokenCollection()

    provider = DockerProvider(host="tcp://fake:2375", client_factory=BrokenClient)
    observation = run(provider.observe(catalog))
    assert observation.state == "unavailable"
    assert observation.error is not None
    assert observation.components == []


def test_stats_collected_for_running_owned_components() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    provider = DockerProvider(host="tcp://fake:2375", client_factory=make_client())
    observation = run(provider.observe(catalog))
    server = next(c for c in observation.components if c.selector == "server")
    assert server.cpu_percent is not None and server.cpu_percent > 0
    assert server.memory_bytes == 104857600


def test_read_logs_demuxes_streams() -> None:
    provider = DockerProvider(host="tcp://fake:2375", client_factory=make_client())
    binding = LogBinding(
        provider="docker", service_id="authentik", source="server", reference="authentik-server-1"
    )
    records = run(provider.read_logs(binding, tail=10, before=None))
    assert len(records) == 3
    streams = {r.stream for r in records}
    assert streams == {"stdout", "stderr"}
    assert all(r.timestamp is not None for r in records)
