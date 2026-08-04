"""Docker provider via the restricted localhost socket proxy."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from typing import Any, Literal

from ..models import Catalog, ComponentState, DockerComponent, HealthState, Service
from .base import (
    ComponentEvidence,
    LogBinding,
    LogRecordIn,
    ProviderObservation,
    UnmappedRuntimeEvidence,
)

logger = logging.getLogger("hyperion.docker")

_STATE_MAP: dict[str, ComponentState] = {
    "running": "running",
    "created": "starting",
    "restarting": "restarting",
    "paused": "paused",
    "exited": "stopped",
    "dead": "stopped",
    "removing": "stopped",
}

_HEALTH_MAP: dict[str, HealthState] = {
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
    return round(float(cpu_delta) / float(system_delta) * num_cpus * 100, 2)


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
        observed_at = datetime.now(UTC)
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
                        f"{runtime.project}/{declared.selector}: "
                        f"{len(attrs_list)} containers matched"
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

        observation_state: Literal["available", "degraded", "unavailable"] = (
            "degraded" if conflicts else "available"
        )
        return ProviderObservation(
            provider="docker",
            state=observation_state,
            observed_at=observed_at,
            components=components,
            unmapped=unmapped,
            conflicts=conflicts,
        )

    async def _build_evidence(
        self,
        service: Service,
        declared: DockerComponent,
        attrs: dict[str, Any],
        observed_at: datetime,
    ) -> ComponentEvidence:
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
            state=docker_state,
            health=health,
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
            container = await self._run_sync(lambda: client.containers.get(container_id))
            stats = await self._run_sync(lambda: container.stats(stream=False))
        except Exception as exc:  # stats are optional evidence
            logger.debug("stats unavailable for %s: %s", container_id, exc)
            return None
        if not isinstance(stats, dict):
            return None
        return stats

    async def read_logs(
        self, binding: LogBinding, tail: int, before: datetime | None
    ) -> list[LogRecordIn]:
        until = int(before.timestamp()) if before is not None else None
        try:
            client = await self._run_sync(lambda: self._client_factory(self._host))
            container = await self._run_sync(lambda: client.containers.get(binding.reference))
            tty = bool((container.attrs.get("Config") or {}).get("Tty"))
        except Exception as exc:
            raise RuntimeError(f"docker log read failed: {exc}") from exc

        records: list[LogRecordIn] = []
        streams: list[tuple[bool, bool, Literal["stdout", "stderr", "unknown"]]] = (
            [(True, True, "unknown")] if tty else [(True, False, "stdout"), (False, True, "stderr")]
        )
        for stdout, stderr, stream in streams:
            try:
                raw = await self._run_sync(
                    partial(
                        container.logs,
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


def _parse_log_lines(
    raw: bytes, source: str, stream: Literal["stdout", "stderr", "unknown"]
) -> list[LogRecordIn]:
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
                stream=stream,
                severity=None,
                message=message.decode("utf-8", errors="replace")[:16384],
            )
        )
    return records
