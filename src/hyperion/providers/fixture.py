"""Deterministic fixture providers used in dev and tests.

Fixture mode runs the real reconciler against canned evidence so the UI and
API can be built and verified before live providers are connected.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..models import Catalog
from .base import (
    LogBinding,
    LogRecordIn,
    ProbeEvidence,
    ProbeObservation,
    ProviderObservation,
)


class FixtureRuntimeProvider:
    """Serves canned Docker/systemd evidence from a JSON file."""

    def __init__(self, evidence_path: Path, logs_path: Path) -> None:
        self._evidence: dict[str, Any] = json.loads(evidence_path.read_text(encoding="utf-8"))
        self._logs: dict[str, Any] = json.loads(logs_path.read_text(encoding="utf-8"))

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

    async def observe(self, probes: tuple[str, ...]) -> ProbeObservation:
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
