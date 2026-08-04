"""Systemd provider via allowlisted fixed-argv subprocesses."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ..models import Catalog, ComponentState, HealthState
from .base import ComponentEvidence, LogBinding, LogRecordIn, ProviderObservation

logger = logging.getLogger("hyperion.systemd")

_SUBPROCESS_TIMEOUT = 3.0

_SHOW_PROPERTIES = (
    "ActiveState,SubState,MainPID,ExecMainStartTimestampMonotonic,NRestarts,LoadState"
)

_Severity = Literal[
    "debug", "info", "notice", "warning", "error", "critical", "alert", "emergency"
]

_ACTIVE_MAP: dict[str, tuple[ComponentState, HealthState]] = {
    "active": ("running", "healthy"),
    "activating": ("starting", "starting"),
    "reloading": ("starting", "starting"),
    "deactivating": ("stopped", "unknown"),
    "inactive": ("stopped", "unconfigured"),
    "failed": ("stopped", "unhealthy"),
}

_PRIORITY_TO_SEVERITY: dict[str, _Severity] = {
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
        observed_at = datetime.now(UTC)
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
            state=state,
            health=health,
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
        timestamp = datetime.fromtimestamp(int(raw) / 1_000_000, tz=UTC)
    severity = _PRIORITY_TO_SEVERITY.get(str(entry.get("PRIORITY", "")))
    message = entry.get("MESSAGE", "")
    if not isinstance(message, str):
        message = str(message)
    return LogRecordIn(
        timestamp=timestamp,
        source=source,
        provider="journald",
        stream="journal",
        severity=severity,
        message=message.rstrip("\n")[:16384],
    )
