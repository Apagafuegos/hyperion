# Hyperion v1 Implementation Plan — Part 3: Live Providers and Reconciliation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Continue from Part 2 (tasks 1.8–1.13 complete). Steps use checkbox (`- [ ]`) syntax for tracking.

**Part 3 covers Phase 2:** Task 2.1 (restricted Docker proxy + Docker provider), Task 2.2 (systemd provider + journal), Task 2.3 (route probe provider with streaks), Task 2.4 (live log gateway), Task 2.5 (supervisor wiring + production catalog + ownership verification).

---

## Phase 2 — Live providers and reconciliation

### Task 2.1: Restricted Docker proxy and Docker provider

**Files:**
- Create: `deploy/docker-proxy.compose.yaml`
- Create: `src/hyperion/providers/docker.py`
- Test: `tests/unit/test_providers_docker.py`

- [ ] **Step 1: Write the proxy compose file**

`deploy/docker-proxy.compose.yaml`:

```yaml
services:
  docker-proxy:
    image: ghcr.io/tecnativa/docker-socket-proxy:0.14.1
    container_name: docker-proxy
    restart: unless-stopped
    ports:
      - "127.0.0.1:2375:2375"
    environment:
      CONTAINERS: "1"
      INFO: "1"
      PING: "1"
      VERSION: "1"
      STATS: "1"
      LOGS: "1"
      POST: "0"
      PUT: "0"
      DELETE: "0"
      PATCH: "0"
      IMAGES: "0"
      NETWORKS: "0"
      VOLUMES: "0"
      TASKS: "0"
      NODES: "0"
      SERVICES: "0"
      SWARM: "0"
      SECRETS: "0"
      CONFIGS: "0"
      BUILD: "0"
      PLUGINS: "0"
      EVENTS: "0"
      EXEC: "0"
      AUTH: "0"
      COMMIT: "0"
      PRUNE: "0"
      ATTACH: "0"
      ALLOW_START: "0"
      ALLOW_STOP: "0"
      ALLOW_RESTART: "0"
      ALLOW_KILL: "0"
      ALLOW_UPDATE: "0"
      ALLOW_PAUSE: "0"
      ALLOW_UNPAUSE: "0"
      ALLOW_CREATE: "0"
      ALLOW_IMAGES: "0"
      ALLOW_LOADS: "0"
      ALLOW_SAVES: "0"
      ALLOW_PUSH: "0"
      ALLOW_PULL: "0"
      ALLOW_RENAME: "0"
      ALLOW_EXEC: "0"
      ALLOW_ATTACH: "0"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

Notes: `CONTAINERS`, `INFO`, `PING`, `VERSION`, `STATS`, `LOGS` are the only enabled sections; `POST=0` blocks mutations globally; the socket mount is read-only. The image digest is pinned at deploy time (Task 3.1) with:

```bash
docker image inspect --format '{{index .RepoDigests 0}}' ghcr.io/tecnativa/docker-socket-proxy:0.14.1
```

- [ ] **Step 2: Write the failing tests (fake SDK client)**

`tests/unit/test_providers_docker.py`:

```python
"""Docker provider maps SDK state to component evidence; exact table from section 6.4."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from hyperion.catalog import load_catalog
from hyperion.providers.base import LogBinding
from hyperion.providers.docker import DockerProvider

FIXTURES = Path(__file__).parents[1] / "fixtures"

NOW = datetime(2026, 8, 4, 6, 0, 0, tzinfo=timezone.utc)


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
            return b"2026-08-04T06:00:01.000000000Z hello out\n2026-08-04T06:00:02.000000000Z second out\n"
        if stderr:
            return b"2026-08-04T06:00:03.000000000Z oops\n"
        return b""


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

        def containers(self, all: bool = False):
            return self._containers

        def get(self, container_id: str):
            return next(c for c in self._containers if c.id == container_id)

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

        def containers(self, all: bool = False):
            raise ConnectionError("connection refused to proxy")

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
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    provider = DockerProvider(host="tcp://fake:2375", client_factory=make_client())
    binding = LogBinding(
        provider="docker", service_id="authentik", source="server", reference="authentik-server-1"
    )
    records = run(provider.read_logs(binding, tail=10, before=None))
    assert len(records) == 3
    streams = {r.stream for r in records}
    assert streams == {"stdout", "stderr"}
    assert all(r.timestamp is not None for r in records)
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/unit/test_providers_docker.py -x -q`
Expected: FAIL — import error.

- [ ] **Step 4: Write `src/hyperion/providers/docker.py`**

```python
"""Docker provider via the restricted localhost socket proxy."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from ..models import Catalog
from .base import (
    ComponentEvidence,
    LogBinding,
    LogRecordIn,
    ProviderObservation,
    UnmappedRuntimeEvidence,
)

logger = logging.getLogger("hyperion.docker")

_STATE_MAP: dict[str, str] = {
    "running": "running",
    "created": "starting",
    "restarting": "restarting",
    "paused": "paused",
    "exited": "stopped",
    "dead": "stopped",
    "removing": "stopped",
}

_HEALTH_MAP: dict[str, str] = {
    "healthy": "healthy",
    "starting": "starting",
    "unhealthy": "unhealthy",
}


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _cpu_percent(stat: dict[str, Any]) -> float | None:
    cpu = stat.get("cpu_stats", {})
    pre = stat.get("precpu_stats", {})
    try:
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        system_delta = cpu["system_cpu_usage"] - pre["system_cpu_usage"]
    except KeyError:
        return None
    if system_delta <= 0 or cpu_delta <= 0:
        return None
    num_cpus = len(cpu.get("cpu_usage", {}).get("percpu_usage", [1]))
    return round(cpu_delta / system_delta * num_cpus * 100, 2)


class DockerProvider:
    """Synchronous docker SDK calls run in a bounded thread pool."""

    def __init__(
        self,
        host: str,
        client_factory: Callable[[str], Any] | None = None,
        pool_size: int = 4,
    ) -> None:
        import docker  # imported lazily so unit tests can inject fakes

        self._host = host
        self._pool = ThreadPoolExecutor(max_workers=pool_size)
        self._client_factory = client_factory or (
            lambda url: docker.DockerClient(base_url=url, timeout=10)
        )

    async def _run_sync(self, fn: Callable[[], Any]) -> Any:
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn)

    async def close(self) -> None:
        self._pool.shutdown(wait=False)

    async def observe(self, catalog: Catalog) -> ProviderObservation:
        observed_at = datetime.now(timezone.utc)
        try:
            client = await self._run_sync(lambda: self._client_factory(self._host))
            containers = await self._run_sync(lambda: client.containers.list(all=True))
        except Exception as exc:  # provider boundary
            logger.warning("docker observation failed: %s", exc)
            return ProviderObservation(
                provider="docker",
                state="unavailable",
                observed_at=observed_at,
                error=str(exc)[:240],
            )

        owned: dict[tuple[str, str], str] = {}
        for service in catalog.services:
            runtime = service.runtime
            if runtime.provider != "docker-compose":
                continue
            for component in runtime.components:
                owned[(runtime.project, component.selector)] = component.selector

        matches: dict[tuple[str, str], list[dict[str, Any]]] = {}
        unmapped: list[UnmappedRuntimeEvidence] = []
        for container in containers:
            attrs = container.attrs
            labels = attrs.get("Config", {}).get("Labels", {}) or {}
            project = labels.get("com.docker.compose.project")
            service_name = labels.get("com.docker.compose.service")
            reference = (attrs.get("Name") or "/").lstrip("/")
            state = str(attrs.get("State", {}).get("Status", "unknown"))[:64]
            if project is not None and service_name is not None:
                if (project, service_name) in owned:
                    matches.setdefault((project, service_name), []).append(attrs)
                else:
                    unmapped.append(
                        UnmappedRuntimeEvidence(
                            provider="docker",
                            reference=reference[:128],
                            project=project[:128],
                            component=service_name[:128],
                            state=state,
                        )
                    )
            else:
                unmapped.append(
                    UnmappedRuntimeEvidence(
                        provider="docker",
                        reference=reference[:128],
                        project=None,
                        component=None,
                        state=state,
                    )
                )

        conflicts: list[str] = []
        components: list[ComponentEvidence] = []
        for service in catalog.services:
            runtime = service.runtime
            if runtime.provider != "docker-compose":
                continue
            for declared in runtime.components:
                attrs_list = matches.get((runtime.project, declared.selector), [])
                if len(attrs_list) == 0:
                    components.append(
                        ComponentEvidence(
                            service_id=service.service_id,
                            selector=declared.selector,
                            provider="docker",
                            state="missing",
                            health="unknown",
                            observed_at=observed_at,
                        )
                    )
                    continue
                if len(attrs_list) > 1:
                    conflicts.append(
                        f"{runtime.project}/{declared.selector}: {len(attrs_list)} containers matched"
                    )
                    components.append(
                        ComponentEvidence(
                            service_id=service.service_id,
                            selector=declared.selector,
                            provider="docker",
                            state="unknown",
                            health="unknown",
                            reference=str(attrs_list[0].get("Id", ""))[:12],
                            ambiguous=True,
                            observed_at=observed_at,
                        )
                    )
                    continue
                components.append(
                    await self._build_evidence(service, declared, attrs_list[0], observed_at)
                )

        state = "degraded" if conflicts else "available"
        return ProviderObservation(
            provider="docker",
            state=state,
            observed_at=observed_at,
            components=components,
            unmapped=unmapped,
            conflicts=conflicts,
        )

    async def _build_evidence(self, service, declared, attrs: dict[str, Any], observed_at: datetime) -> ComponentEvidence:
        state_info = attrs.get("State", {})
        raw_state = str(state_info.get("Status", "unknown"))
        docker_state = _STATE_MAP.get(raw_state, "unknown")
        raw_health = (state_info.get("Health") or {}).get("Status")
        health = (
            "unconfigured"
            if raw_health is None
            else _HEALTH_MAP.get(str(raw_health), "unknown")
        )
        if docker_state == "running" and health == "starting":
            docker_state = "starting"

        reference = (attrs.get("Name") or "/").lstrip("/")
        image = attrs.get("Config", {}).get("Image")
        restart_count = attrs.get("RestartCount")
        started = _parse_timestamp(str(state_info.get("StartedAt", "")))
        uptime = None
        if started is not None and docker_state in ("running", "starting", "restarting"):
            uptime = max(0, int((observed_at - started).total_seconds()))

        cpu = None
        memory = None
        if docker_state == "running":
            stats = await self._stats(attrs.get("Id", ""))
            if stats is not None:
                cpu = _cpu_percent(stats)
                memory = stats.get("memory_stats", {}).get("usage")

        return ComponentEvidence(
            service_id=service.service_id,
            selector=declared.selector,
            provider="docker",
            state=docker_state,  # type: ignore[arg-type]
            health=health,  # type: ignore[arg-type]
            reference=reference[:128],
            image=str(image)[:256] if image else None,
            uptime_seconds=uptime,
            restart_count=restart_count if isinstance(restart_count, int) else None,
            cpu_percent=cpu,
            memory_bytes=memory,
            observed_at=observed_at,
        )

    async def _stats(self, container_id: str) -> dict[str, Any] | None:
        try:
            client = await self._run_sync(lambda: self._client_factory(self._host))
            container = await self._run_sync(lambda: client.get(container_id))
            stats = await self._run_sync(lambda: container.stats(stream=False))
            return stats
        except Exception as exc:  # stats are optional evidence
            logger.debug("stats unavailable for %s: %s", container_id, exc)
            return None

    async def read_logs(
        self, binding: LogBinding, tail: int, before: datetime | None
    ) -> list[LogRecordIn]:
        until = int(before.timestamp()) if before is not None else None
        try:
            client = await self._run_sync(lambda: self._client_factory(self._host))
            container = await self._run_sync(lambda: client.get(binding.reference))
            tty = bool((container.attrs.get("Config") or {}).get("Tty"))
        except Exception as exc:
            raise RuntimeError(f"docker log read failed: {exc}") from exc

        records: list[LogRecordIn] = []
        streams: list[tuple[bool, bool, str]] = (
            [(True, True, "unknown")] if tty else [(True, False, "stdout"), (False, True, "stderr")]
        )
        for stdout, stderr, stream in streams:
            try:
                raw = await self._run_sync(
                    lambda: container.logs(
                        stdout=stdout,
                        stderr=stderr,
                        timestamps=True,
                        tail=tail,
                        until=until,
                        stream=False,
                    )
                )
            except Exception as exc:
                raise RuntimeError(f"docker log read failed: {exc}") from exc
            records.extend(_parse_log_lines(raw, binding.source, stream))
        records.sort(key=lambda record: record.timestamp or datetime.min)
        return records[-tail:]


def _parse_log_lines(raw: bytes, source: str, stream: str) -> list[LogRecordIn]:
    records: list[LogRecordIn] = []
    for line in raw.splitlines():
        timestamp: datetime | None = None
        message = line
        parts = line.split(b" ", 1)
        if len(parts) == 2:
            try:
                timestamp = datetime.fromisoformat(parts[0].decode("ascii"))
            except ValueError:
                timestamp = None
            else:
                message = parts[1]
        records.append(
            LogRecordIn(
                timestamp=timestamp,
                source=source,
                provider="docker",
                stream=stream,  # type: ignore[arg-type]
                severity=None,
                message=message.decode("utf-8", errors="replace")[:16384],
            )
        )
    return records
```

Notes:
- `datetime.fromisoformat` accepts the `Z` suffix on Python 3.11+; Docker timestamps end in `Z`.
- `client.get(binding.reference)` looks up by container name — the reconciler passes the evidence `reference` through `LogBinding`.
- Per-source fetch uses `tail` lines each; the gateway merges and trims to `tail` total (documented behavior in §9).

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_providers_docker.py -v`
Expected: PASS.

- [ ] **Step 6: Lint, typecheck, and commit**

```bash
uv run ruff check src tests
uv run mypy src
git add deploy/docker-proxy.compose.yaml src/hyperion/providers/docker.py tests/unit/test_providers_docker.py
git commit -m "feat: docker provider over the restricted socket proxy"
```

---

### Task 2.2: Systemd provider and journal logs

**Files:**
- Create: `src/hyperion/providers/systemd.py`
- Test: `tests/unit/test_providers_systemd.py`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_providers_systemd.py`:

```python
"""Systemd provider: fixed argv subprocesses, exact unit names only."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from hyperion.catalog import load_catalog
from hyperion.providers.base import LogBinding
from hyperion.providers.systemd import SystemdProvider

FIXTURES = Path(__file__).parents[1] / "fixtures"

ACTIVE_SHOW = (
    "ActiveState=active\n"
    "SubState=running\n"
    "MainPID=1234\n"
    "ExecMainStartTimestampMonotonic=432000000000\n"
    "NRestarts=2\n"
    "LoadState=loaded\n"
)


class FakeExecutor:
    def __init__(self, responses: dict[str, tuple[int, bytes, bytes]]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    async def run(self, argv: list[str]) -> tuple[int, bytes, bytes]:
        self.calls.append(argv)
        for needle, response in self.responses.items():
            if needle in argv:
                return response
        return (0, b"", b"")


def test_show_uses_fixed_argv() -> None:
    executor = FakeExecutor({"systemctl": (0, ACTIVE_SHOW.encode(), b"")})
    provider = SystemdProvider(executor=executor.run)
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    observations = asyncio.run(provider.observe(catalog))
    assert observations.state == "available"
    t3 = next(c for c in observations.components if c.selector == "t3code.service")
    assert t3.state == "running"
    assert t3.health == "healthy"
    assert t3.restart_count == 2
    systemctl_call = next(call for call in executor.calls if call[0] == "systemctl")
    assert "t3code.service" in systemctl_call
    assert "--no-pager" in systemctl_call
    assert (
        "--property=ActiveState,SubState,MainPID,ExecMainStartTimestampMonotonic,NRestarts,LoadState"
        in systemctl_call
    )


def test_not_found_unit_is_missing() -> None:
    executor = FakeExecutor(
        {"systemctl": (0, b"LoadState=not-found\nActiveState=inactive\n", b"")}
    )
    provider = SystemdProvider(executor=executor.run)
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    observations = asyncio.run(provider.observe(catalog))
    t3 = next(c for c in observations.components if c.selector == "t3code.service")
    assert t3.state == "missing"
    assert t3.health == "unknown"


def test_failed_unit_maps_to_stopped_unhealthy() -> None:
    executor = FakeExecutor(
        {"systemctl": (0, b"ActiveState=failed\nSubState=failed\nNRestarts=5\nLoadState=loaded\n", b"")}
    )
    provider = SystemdProvider(executor=executor.run)
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    observations = asyncio.run(provider.observe(catalog))
    t3 = next(c for c in observations.components if c.selector == "t3code.service")
    assert t3.state == "stopped"
    assert t3.health == "unhealthy"
    assert t3.restart_count == 5


def test_provider_failure_is_unavailable() -> None:
    async def failing(argv: list[str]) -> tuple[int, bytes, bytes]:
        raise OSError("journald missing")

    provider = SystemdProvider(executor=failing)
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    observations = asyncio.run(provider.observe(catalog))
    assert observations.state == "unavailable"
    assert observations.error is not None


def test_journal_reads_parsed_json() -> None:
    journal_line = (
        '{"__REALTIME_TIMESTAMP":"1783000000000000","PRIORITY":"6","MESSAGE":"hello from t3",'
        '"SYSLOG_IDENTIFIER":"t3coded"}'
    )
    executor = FakeExecutor({"journalctl": (0, (journal_line + "\n").encode(), b"")})
    provider = SystemdProvider(executor=executor.run)
    binding = LogBinding(
        provider="systemd", service_id="t3-code", source="t3code.service", reference="t3code.service"
    )
    records = asyncio.run(provider.read_logs(binding, tail=10, before=None))
    assert len(records) == 1
    assert records[0].message == "hello from t3"
    assert records[0].severity == "info"
    assert records[0].stream == "journal"
    call = next(call for call in executor.calls if call[0] == "journalctl")
    assert "--unit" in call and "t3code.service" in call
    assert "--output" in call and "json" in call


def test_journal_until_from_validated_timestamp() -> None:
    executor = FakeExecutor({"journalctl": (0, b"", b"")})
    provider = SystemdProvider(executor=executor.run)
    binding = LogBinding(
        provider="systemd", service_id="t3-code", source="t3code.service", reference="t3code.service"
    )
    before = datetime(2026, 8, 4, 6, 0, 0, tzinfo=timezone.utc)
    asyncio.run(provider.read_logs(binding, tail=10, before=before))
    call = next(call for call in executor.calls if call[0] == "journalctl")
    assert "--until" in call
    assert call[call.index("--until") + 1].startswith("2026-08-04")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_providers_systemd.py -x -q`
Expected: FAIL — import error.

- [ ] **Step 3: Write `src/hyperion/providers/systemd.py`**

```python
"""Systemd provider via allowlisted fixed-argv subprocesses."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from ..models import Catalog
from .base import ComponentEvidence, LogBinding, LogRecordIn, ProviderObservation

logger = logging.getLogger("hyperion.systemd")

_SUBPROCESS_TIMEOUT = 3.0

_SHOW_PROPERTIES = (
    "ActiveState,SubState,MainPID,ExecMainStartTimestampMonotonic,NRestarts,LoadState"
)

_ACTIVE_MAP = {
    "active": ("running", "healthy"),
    "activating": ("starting", "starting"),
    "reloading": ("starting", "starting"),
    "deactivating": ("stopped", "unknown"),
    "inactive": ("stopped", "unconfigured"),
    "failed": ("stopped", "unhealthy"),
}

_PRIORITY_TO_SEVERITY = {
    "7": "debug", "6": "info", "5": "notice", "4": "warning",
    "3": "error", "2": "critical", "1": "alert", "0": "emergency",
}

ExecRunner = Callable[[list[str]], Awaitable[tuple[int, bytes, bytes]]]


async def _subprocess_runner(argv: list[str]) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=_SUBPROCESS_TIMEOUT)
    return process.returncode or 0, stdout, stderr


def _read_uptime_seconds() -> float:
    try:
        return float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, IndexError, ValueError):
        return 0.0


class SystemdProvider:
    """Reads state only for exact unit names declared in the catalog."""

    def __init__(self, executor: ExecRunner | None = None) -> None:
        self._executor = executor or _subprocess_runner

    async def observe(self, catalog: Catalog) -> ProviderObservation:
        observed_at = datetime.now(timezone.utc)
        bindings: list[tuple[str, str, str]] = []  # (service_id, selector, unit)
        for service in catalog.services:
            runtime = service.runtime
            if runtime.provider != "systemd":
                continue
            for component in runtime.components:
                bindings.append((service.service_id, component.selector, component.selector))

        components: list[ComponentEvidence] = []
        try:
            for service_id, selector, unit in bindings:
                components.append(
                    await self._observe_unit(service_id, selector, unit, observed_at)
                )
        except Exception as exc:  # provider boundary
            logger.warning("systemd observation failed: %s", exc)
            return ProviderObservation(
                provider="systemd",
                state="unavailable",
                observed_at=observed_at,
                error=str(exc)[:240],
            )
        return ProviderObservation(
            provider="systemd",
            state="available",
            observed_at=observed_at,
            components=components,
        )

    async def _observe_unit(
        self, service_id: str, selector: str, unit: str, observed_at: datetime
    ) -> ComponentEvidence:
        argv = [
            "systemctl", "show", unit, "--no-pager",
            f"--property={_SHOW_PROPERTIES}",
        ]
        returncode, stdout, stderr = await self._executor(argv)
        if returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(message or f"systemctl show {unit} failed")

        properties: dict[str, str] = {}
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                properties[key] = value

        load_state = properties.get("LoadState", "")
        if load_state == "not-found":
            return ComponentEvidence(
                service_id=service_id,
                selector=selector,
                provider="systemd",
                state="missing",
                health="unknown",
                reference=unit,
                observed_at=observed_at,
            )

        active_state = properties.get("ActiveState", "unknown")
        state, health = _ACTIVE_MAP.get(active_state, ("unknown", "unknown"))
        nrestarts = properties.get("NRestarts", "")
        restart_count = int(nrestarts) if nrestarts.isdigit() else None

        uptime = None
        start_mono = properties.get("ExecMainStartTimestampMonotonic", "")
        if start_mono.isdigit() and int(start_mono) > 0:
            uptime = max(0, int(_read_uptime_seconds() - int(start_mono) / 1_000_000))

        return ComponentEvidence(
            service_id=service_id,
            selector=selector,
            provider="systemd",
            state=state,  # type: ignore[arg-type]
            health=health,  # type: ignore[arg-type]
            reference=unit,
            uptime_seconds=uptime,
            restart_count=restart_count,
            observed_at=observed_at,
        )

    async def read_logs(
        self, binding: LogBinding, tail: int, before: datetime | None
    ) -> list[LogRecordIn]:
        argv = [
            "journalctl", "--unit", binding.reference,
            "--lines", str(tail),
            "--output", "json", "--no-pager", "--quiet",
        ]
        if before is not None:
            argv += ["--until", before.isoformat().replace("+00:00", "Z")]
        returncode, stdout, stderr = await self._executor(argv)
        if returncode != 0:
            raise RuntimeError(
                stderr.decode("utf-8", errors="replace").strip() or "journalctl failed"
            )
        records: list[LogRecordIn] = []
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            record = _parse_journal_line(line, binding.source)
            if record is not None:
                records.append(record)
        return records


def _parse_journal_line(line: str, source: str) -> LogRecordIn | None:
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(entry, dict):
        return None
    timestamp: datetime | None = None
    raw = entry.get("__REALTIME_TIMESTAMP")
    if isinstance(raw, str) and raw.isdigit():
        timestamp = datetime.fromtimestamp(int(raw) / 1_000_000, tz=timezone.utc)
    severity = _PRIORITY_TO_SEVERITY.get(str(entry.get("PRIORITY", "")))
    message = entry.get("MESSAGE", "")
    if not isinstance(message, str):
        message = str(message)
    return LogRecordIn(
        timestamp=timestamp,
        source=source,
        provider="journald",
        stream="journal",
        severity=severity,  # type: ignore[arg-type]
        message=message.rstrip("\n")[:16384],
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_providers_systemd.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check src tests
uv run mypy src
git add src/hyperion/providers/systemd.py tests/unit/test_providers_systemd.py
git commit -m "feat: systemd provider and journal log reader"
```

---

### Task 2.3: Route probe provider with failure streaks

**Files:**
- Create: `src/hyperion/providers/probe.py`
- Test: `tests/unit/test_providers_probe.py`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_providers_probe.py`:

```python
"""Route probes: bounded HTTPS checks, redirects disabled, streaks in memory."""

from __future__ import annotations

import asyncio

import httpx

from hyperion.providers.probe import ProbeProvider


def make_provider(handler) -> ProbeProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ProbeProvider(client_factory=lambda: client)


def test_reachable_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == "Hyperion/1"
        return httpx.Response(200, text="ok")

    provider = make_provider(handler)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    result = observation.results[0]
    assert result.state == "reachable"
    assert result.status_code == 200
    assert result.consecutive_failures == 0
    assert result.latency_ms is not None


def test_redirect_is_reachable_not_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://other.example.com"})

    provider = make_provider(handler)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    result = observation.results[0]
    assert result.state == "reachable"
    assert result.status_code == 302
    assert result.error is None


def test_5xx_and_transport_failures_increment_streak() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    provider = make_provider(handler)
    first = asyncio.run(provider.observe(("https://alpha.example.com",)))
    second = asyncio.run(provider.observe(("https://alpha.example.com",)))
    assert first.results[0].consecutive_failures == 1
    assert second.results[0].consecutive_failures == 2


def test_success_resets_streak() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503 if calls["n"] <= 2 else 200)

    provider = make_provider(handler)
    asyncio.run(provider.observe(("https://alpha.example.com",)))
    asyncio.run(provider.observe(("https://alpha.example.com",)))
    third = asyncio.run(provider.observe(("https://alpha.example.com",)))
    assert third.results[0].consecutive_failures == 0


def test_timeout_categorized() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ProbeProvider(client_factory=lambda: client)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    result = observation.results[0]
    assert result.state == "failed"
    assert result.error == "timeout"


def test_body_limit_reads_only_1kib() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x" * 4096)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ProbeProvider(client_factory=lambda: client)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    assert observation.results[0].status_code == 200


def test_provider_total_failure_unavailable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("client broken")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ProbeProvider(client_factory=lambda: client)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    assert observation.state == "unavailable"
    assert observation.error is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_providers_probe.py -x -q`
Expected: FAIL — import error.

- [ ] **Step 3: Write `src/hyperion/providers/probe.py`**

```python
"""Route probe provider: bounded HTTPS checks with in-memory failure streaks."""

from __future__ import annotations

import logging
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Callable

import httpx

from .base import ProbeEvidence, ProbeObservation

logger = logging.getLogger("hyperion.probe")

BODY_LIMIT = 1024
USER_AGENT = "Hyperion/1"
_CONCURRENT = 4


def _categorize_error(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        cause = exc.__cause__ or exc
        if isinstance(cause, socket.gaierror):
            return "dns"
        if isinstance(cause, ssl.SSLError) or "ssl" in str(cause).lower():
            return "tls"
        return "connection"
    if isinstance(exc, httpx.TransportError):
        return "connection"
    return "unknown"


class ProbeProvider:
    """Shared pooled client, bounded concurrency, streaks keyed by URL."""

    def __init__(self, client_factory: Callable[[], httpx.AsyncClient] | None = None) -> None:
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT},
                timeout=httpx.Timeout(3.0),
                limits=httpx.Limits(
                    max_connections=_CONCURRENT, max_keepalive_connections=_CONCURRENT
                ),
            )
        )
        self._client: httpx.AsyncClient | None = None
        self._streaks: dict[str, int] = {}

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def observe(self, probes: tuple[str, ...]) -> ProbeObservation:
        observed_at = datetime.now(timezone.utc)
        results: list[ProbeEvidence] = []
        try:
            client = await self._http()
            for url in probes:
                results.append(await self._probe_one(client, url, observed_at))
        except Exception as exc:  # provider boundary
            logger.warning("probe observation failed: %s", exc)
            return ProbeObservation(
                provider="probe",
                state="unavailable",
                observed_at=observed_at,
                error=str(exc)[:240],
            )
        return ProbeObservation(
            provider="probe",
            state="available",
            observed_at=observed_at,
            results=results,
        )

    async def _probe_one(
        self, client: httpx.AsyncClient, url: str, observed_at: datetime
    ) -> ProbeEvidence:
        started = time.monotonic()
        status_code: int | None = None
        error: str | None = None
        accepted = False
        try:
            async with client.stream("GET", url) as response:
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size >= BODY_LIMIT:
                        break
                status_code = response.status_code
                accepted = 100 <= status_code < 500
        except httpx.TimeoutException as exc:
            error = "timeout"
            logger.debug("probe timeout %s: %s", url, exc)
        except httpx.ConnectError as exc:
            error = _categorize_error(exc)
            logger.debug("probe connect error %s: %s", url, exc)
        except httpx.TransportError as exc:
            error = _categorize_error(exc)
            logger.debug("probe transport error %s: %s", url, exc)
        except Exception as exc:
            error = _categorize_error(exc)
            logger.debug("probe error %s: %s", url, exc)

        latency_ms = int((time.monotonic() - started) * 1000)
        if accepted:
            self._streaks[url] = 0
            state = "reachable"
        else:
            self._streaks[url] = self._streaks.get(url, 0) + 1
            state = "failed"
        return ProbeEvidence(
            url=url,
            state=state,  # type: ignore[arg-type]
            status_code=status_code,
            latency_ms=latency_ms,
            consecutive_failures=self._streaks[url],
            observed_at=observed_at,
            error=error,  # type: ignore[arg-type]
        )
```

Notes:
- Accepted status classes are 2xx/3xx/4xx by default (catalog-configurable in principle; §8 states classes 2,3,4 establish reachability). Redirects are never followed.
- Slow detection happens in the reconciler (route latency vs `slowAfterMs`); the probe records raw latency.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_providers_probe.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check src tests
uv run mypy src
git add src/hyperion/providers/probe.py tests/unit/test_providers_probe.py
git commit -m "feat: route probe provider with in-memory failure streaks"
```

---

### Task 2.4: Live log gateway paths

The gateway itself (allowlist resolution, merge, bounds) is already implemented and tested through fixture mode in Task 1.8. This task adds the live-provider behaviors and their tests: Docker TTY vs non-TTY parsing, journald severity mapping, `before` bounds, per-source timeouts, and the response-size cap.

**Note (reference resolution):** the gateway currently binds `LogBinding.reference` to `component.selector` — a Compose service name — but `DockerProvider.read_logs` will resolve `binding.reference` as a container name. The gateway must resolve selectors to live container references from provider observations: the reconciler evidence map already carries `reference` per component (`ComponentEvidence.reference` in `providers/base.py`). Either the gateway (via a shared component-reference registry built from the latest observations) or the Docker provider's own discovery must supply container names when building the `LogBinding`; without it, live Docker log reads will fail at runtime. Systemd bindings are unaffected (`reference` is the unit name).

**Files:**
- Test: `tests/unit/test_logs_gateway.py`

- [ ] **Step 1: Write the tests**

`tests/unit/test_logs_gateway.py`:

```python
"""Log gateway: allowlist, merge order, bounds, truncation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hyperion.api.errors import ApiError
from hyperion.catalog import load_catalog
from hyperion.providers.base import LogBinding, LogRecordIn
from hyperion.services.logs import LogGateway

FIXTURES = Path(__file__).parents[1] / "fixtures"

NOW = datetime(2026, 8, 4, 6, 0, 0, tzinfo=timezone.utc)


class FakeRuntime:
    def __init__(self, records: list[LogRecordIn]) -> None:
        self.records = records
        self.calls: list[tuple[LogBinding, int, datetime | None]] = []

    async def read_logs(self, binding, tail, before):
        self.calls.append((binding, tail, before))
        return [r for r in self.records if r.source == binding.source]


def rec(offset: int, source: str, message: str = "line") -> LogRecordIn:
    return LogRecordIn(
        timestamp=NOW + timedelta_seconds(offset),
        source=source,
        provider="docker",
        stream="stdout",
        severity="info",
        message=message,
    )


def timedelta_seconds(offset: int):
    from datetime import timedelta

    return timedelta(seconds=offset)


def catalog():
    return load_catalog(FIXTURES / "fixture-services.yaml")


def test_unknown_service_raises() -> None:
    gateway = LogGateway(FakeRuntime([]), catalog())
    with pytest.raises(ApiError) as exc:
        asyncio.run(gateway.read("nope", None, 100, None))
    assert exc.value.code == "SERVICE_NOT_FOUND"


def test_unknown_source_raises() -> None:
    gateway = LogGateway(FakeRuntime([]), catalog())
    with pytest.raises(ApiError) as exc:
        asyncio.run(gateway.read("authentik", "ghost", 100, None))
    assert exc.value.code == "LOG_SOURCE_NOT_FOUND"


def test_merges_sources_chronologically_and_trims_tail() -> None:
    records = [rec(10, "server"), rec(5, "worker"), rec(20, "server"), rec(1, "worker")]
    gateway = LogGateway(FakeRuntime(records), catalog())
    response = asyncio.run(gateway.read("authentik", None, 2, None))
    assert [r.timestamp for r in response.records] == [NOW + timedelta_seconds(10), NOW + timedelta_seconds(20)]
    assert response.truncated is True


def test_single_source_bounds_before() -> None:
    runtime = FakeRuntime([rec(10, "server"), rec(30, "server")])
    gateway = LogGateway(runtime, catalog())
    response = asyncio.run(gateway.read("authentik", "server", 100, NOW + timedelta_seconds(15)))
    assert len(response.records) == 1
    assert runtime.calls[0][2] == NOW + timedelta_seconds(15)


def test_message_truncation_flag() -> None:
    long_message = "x" * 20000
    gateway = LogGateway(FakeRuntime([rec(1, "server", long_message)]), catalog())
    response = asyncio.run(gateway.read("authentik", "server", 100, None))
    assert len(response.records[0].message) == 16384
    assert response.records[0].truncated is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_logs_gateway.py -x -q`
Expected: FAIL — `hyperion.services.logs` import error (it is created in Part 2 Task 1.8; if Part 2 is complete this passes immediately — in that case this task is a verification-only step; proceed to step 3).

- [ ] **Step 3: Verify fixture-mode logs against the fixture files**

```bash
uv run pytest tests/integration/test_api.py -v
```

Expected: PASS — logs for authentik include the `GET /api/v3/core/users/` record from the fixture.

- [ ] **Step 4: Confirm the response-size cap path exists** (already in `LogGateway.read` via `truncated` flag when `len(records) > tail`). No code change required unless the tests above reveal a bug; fix inline if so.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check src tests
uv run mypy src
git add tests/unit/test_logs_gateway.py
git commit -m "test: log gateway merge order, bounds, and truncation"
```

---

### Task 2.5: Production catalog, ownership verification, and live wiring

**Files:**
- Create: `services.yaml` (production catalog from `services.example.yaml`)
- Modify: `src/hyperion/main.py` (verify live wiring; no changes expected)
- Test: `tests/unit/test_services_yaml_validates.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_services_yaml_validates.py`:

```python
"""The committed production catalog must validate structurally and semantically."""

from __future__ import annotations

from pathlib import Path

from hyperion.catalog import catalog_revision, load_catalog

PRODUCTION_CATALOG = Path(__file__).parents[2] / "services.yaml"


def test_production_catalog_validates() -> None:
    catalog = load_catalog(PRODUCTION_CATALOG)
    assert catalog.version == 1
    assert len(catalog.services) >= 8
    ids = [s.service_id for s in catalog.services]
    assert ids == sorted(ids) or len(ids) == len(set(ids))
    for service in catalog.services:
        assert service.runtime.components


def test_production_catalog_revision_is_stable() -> None:
    a = load_catalog(PRODUCTION_CATALOG)
    b = load_catalog(PRODUCTION_CATALOG)
    assert catalog_revision(a) == catalog_revision(b)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_services_yaml_validates.py -x -q`
Expected: FAIL — `services.yaml` missing.

- [ ] **Step 3: Create `services.yaml`** — copy `services.example.yaml` verbatim and append the shared-foundation note service. Use the example file as the base; only adjust copy when the owner approves (per handoff, the current copy is intentionally generic and acceptable):

```bash
cp services.example.yaml services.yaml
```

Then verify it loads: `uv run python -c "from pathlib import Path; from hyperion.catalog import load_catalog; print(len(load_catalog(Path('services.yaml')).services))"` — expected `8`.

- [ ] **Step 4: Live ownership verification (requires Docker proxy + real VPS runtimes)**

```bash
docker compose -f deploy/docker-proxy.compose.yaml up -d
curl -s http://127.0.0.1:2375/_ping && echo OK
```

Then run Hyperion against the production catalog and inspect the snapshot:

```bash
HYPERION_CATALOG_PATH=/home/ubuntu/projects/hyperion/services.yaml \
  uv run uvicorn hyperion.main:app --host 127.0.0.1 --port 8787 --workers 1 &
sleep 3
curl -s -H "X-Authentik-Username: owner" http://127.0.0.1:8787/api/v1/snapshot | uv run python -m json.tool | head -60
```

Expected: every declared component resolves to exactly one container (`providerRef` set, no `missing` states for the current VPS fleet); unexpected containers appear under `diagnostics.unmappedRuntimes`. Verify T3 Code specifically resolves through the systemd provider (`provider: systemd`, `state: running`). If any selector is wrong (e.g., a Compose service renamed), fix the selector in `services.yaml` AND `services.example.yaml` AND `schema` docs if the contract changes — in the same commit (discipline).

- [ ] **Step 5: Live log spot-check**

```bash
curl -s -H "X-Authentik-Username: owner" "http://127.0.0.1:8787/api/v1/services/t3-code/logs?tail=50" | head -c 400
curl -s -H "X-Authentik-Username: owner" "http://127.0.0.1:8787/api/v1/services/langfuse/logs?tail=50&source=langfuse" | head -c 400
```

Expected: journal records for t3-code (severity mapped), docker stdout/stderr for langfuse.

- [ ] **Step 6: Kill the server, run the full Python suite, and commit**

```bash
kill %1
uv run pytest
uv run ruff check src tests
uv run mypy src
git add services.yaml tests/unit/test_services_yaml_validates.py
git commit -m "feat: production catalog with live ownership verification"
```

**Phase 2 gate:** live providers produce a correct snapshot against the real VPS; T3 Code works through systemd; every ownership selector resolves exactly once; logs return real bounded records.

---

**End of Part 3.** Continue with `2026-08-04-hyperion-v1-part4.md` (Phase 3: deployment and hardening — systemd unit, Caddy route, security verification, acceptance run, visual comparison, CI).
