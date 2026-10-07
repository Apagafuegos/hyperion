"""Deterministic fixture providers used in dev and tests.

Fixture mode runs the real reconciler against canned evidence so the UI and
API can be built and verified before live providers are connected.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..models import (
    Catalog,
    HostEvidence,
    LogRecord,
    ScheduleInventoryResponse,
    ScheduleSnapshot,
    UnitInventoryResponse,
    UnitSnapshot,
)
from .base import (
    LogBinding,
    LogRecordIn,
    ProbeEvidence,
    ProbeObservation,
    ProviderObservation,
)
from .cron import parse_cron_expression


class FixtureRuntimeProvider:
    """Serves canned Docker/systemd evidence from a JSON file."""

    def __init__(self, evidence_path: Path, logs_path: Path) -> None:
        self._evidence: dict[str, Any] = json.loads(evidence_path.read_text(encoding="utf-8"))
        self._logs: dict[str, Any] = json.loads(logs_path.read_text(encoding="utf-8"))

    async def discover_catalog(self, base: Catalog) -> Catalog:
        return base

    async def observe(self, catalog: Catalog) -> list[ProviderObservation]:
        now = datetime.now(UTC)
        observations: list[ProviderObservation] = []
        for provider in ("docker", "systemd"):
            observation = ProviderObservation.model_validate(self._evidence[provider])
            observation.observed_at = now
            for component in observation.components:
                component.observed_at = now
            observations.append(observation)
        return observations

    async def read_logs(
        self, binding: LogBinding, tail: int, before: datetime | None
    ) -> list[LogRecordIn]:
        now = datetime.now(UTC)
        records = [
            LogRecordIn(
                timestamp=now - timedelta(seconds=entry["offsetSeconds"]),
                source=entry["source"],
                provider="journald" if binding.provider == "systemd" else "docker",
                stream=entry["stream"],
                severity=entry["severity"],
                message=entry["message"],
            )
            for entry in self._logs.get(binding.service_id, [])
            if entry["source"] == binding.source
        ]
        records.sort(key=lambda record: record.timestamp or datetime.min)
        if before is not None:
            records = [r for r in records if r.timestamp is None or r.timestamp < before]
        return records[-tail:]


class FixtureProbeProvider:
    """Serves canned route-probe results from a JSON file."""

    def __init__(self, probes_path: Path) -> None:
        self._raw: dict[str, Any] = json.loads(probes_path.read_text(encoding="utf-8"))

    async def observe(
        self,
        probes: tuple[str, ...],
        config: dict[str, tuple[float, float]] | None = None,
    ) -> ProbeObservation:
        del config  # fixture results carry their own states; per-URL tuning not needed
        now = datetime.now(UTC)
        by_url = {result["url"]: result for result in self._raw["results"]}
        results: list[ProbeEvidence] = []
        for url in probes:
            result = by_url.get(url)
            if result is None:
                results.append(
                    ProbeEvidence(
                        url=url, state="unknown", observed_at=now, consecutive_failures=0
                    )
                )
                continue
            evidence = ProbeEvidence.model_validate(result)
            evidence.observed_at = now
            results.append(evidence)
        return ProbeObservation(
            provider="probe", state="available", observed_at=now, results=results
        )


class FixtureHostProvider:
    """Serves canned host evidence; rates are static per fixture."""

    def __init__(self, host_path: Path) -> None:
        self._raw: dict[str, Any] = json.loads(host_path.read_text(encoding="utf-8"))

    async def observe(self) -> HostEvidence:
        now = datetime.now(UTC)
        evidence = HostEvidence.model_validate(self._raw["host"])
        evidence.observed_at = now
        evidence.fresh = True
        if evidence.uptime_seconds is not None:
            evidence.boot_time = now - timedelta(seconds=evidence.uptime_seconds)
        return evidence


class FixtureUnitProvider:
    """Serves canned systemd unit inventory."""

    def __init__(self, units_path: Path, logs_path: Path) -> None:
        self._raw: dict[str, Any] = json.loads(units_path.read_text(encoding="utf-8"))
        self._logs: dict[str, Any] = json.loads(logs_path.read_text(encoding="utf-8"))
        self._unit_log_keys: dict[str, str] = {}
        for item in self._raw["units"]:
            unit_name = item.get("name")
            related = item.get("relatedService")
            if isinstance(unit_name, str) and isinstance(related, str):
                self._unit_log_keys[unit_name] = related

    async def inventory(self, related: dict[str, str] | None = None) -> UnitInventoryResponse:
        now = datetime.now(UTC)
        units = [UnitSnapshot.model_validate(item) for item in self._raw["units"]]
        if related:
            for unit in units:
                unit.related_service = related.get(unit.name, unit.related_service)
                if unit.name in related:
                    unit.curated = True
                    if unit.related_service is not None:
                        self._unit_log_keys[unit.name] = unit.related_service
        counts: dict[str, int] = {}
        for unit in units:
            counts[unit.active_state] = counts.get(unit.active_state, 0) + 1
        return UnitInventoryResponse(generated_at=now, fresh=True, counts=counts, units=units)

    async def read_logs(self, unit: str, tail: int = 100) -> list[dict[str, object]]:
        now = datetime.now(UTC)
        log_key = self._unit_log_keys.get(unit, unit)
        records = [
            LogRecord(
                timestamp=now - timedelta(seconds=entry["offsetSeconds"]),
                source=entry["source"],
                provider="journald",
                stream="journal",
                severity=entry["severity"],
                message=entry["message"],
                truncated=False,
            )
            for entry in self._logs.get(log_key, [])
        ]
        records.sort(key=lambda record: record.timestamp or datetime.min)
        return [record.model_dump(mode="json", by_alias=True) for record in records[-tail:]]


class FixtureScheduleProvider:
    """Serves canned schedule inventory with times relative to now."""

    def __init__(self, schedules_path: Path) -> None:
        self._raw: dict[str, Any] = json.loads(schedules_path.read_text(encoding="utf-8"))

    async def inventory(self) -> ScheduleInventoryResponse:
        now = datetime.now(UTC)
        schedules = [ScheduleSnapshot.model_validate(item) for item in self._raw["schedules"]]
        for schedule in schedules:
            if schedule.source == "cron":
                schedule.next_run = parse_cron_expression(schedule.raw_expression, now).next_run
            elif schedule.source == "systemd" and schedule.raw_expression.startswith("*-*-* "):
                # The fixture calendar is daily; anchor it to the calendar's
                # clock time rather than inventing a next run relative to now.
                hour, minute, second = map(int, schedule.raw_expression.split()[1].split(":"))
                today = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
                schedule.next_run = today if today > now else today + timedelta(days=1)
                if schedule.last_run is not None:
                    schedule.last_run = today if today <= now else today - timedelta(days=1)
        return ScheduleInventoryResponse(generated_at=now, fresh=True, schedules=schedules)
