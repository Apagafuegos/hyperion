"""Combined provider: one observation per inner provider; delegates log reads."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from hyperion.catalog import load_catalog
from hyperion.providers.base import LogBinding, LogRecordIn, ProviderObservation
from hyperion.providers.combined import CombinedRuntimeProvider

FIXTURES = Path(__file__).parents[1] / "fixtures"


class RecordingProvider:
    def __init__(self, provider: Literal["docker", "systemd"]) -> None:
        self._provider = provider
        self.observe_calls = 0
        self.log_reads: list[Literal["docker", "systemd"]] = []
        self.last_observation: ProviderObservation | None = None

    async def observe(self, catalog) -> ProviderObservation:
        self.observe_calls += 1
        observation = ProviderObservation(
            provider=self._provider,
            state="available",
            observed_at=datetime.now(UTC),
        )
        self.last_observation = observation
        return observation

    async def read_logs(self, binding, tail, before) -> list[LogRecordIn]:
        self.log_reads.append(binding.provider)
        return []


def test_observe_returns_exactly_one_observation_per_inner_provider() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    docker = RecordingProvider("docker")
    systemd = RecordingProvider("systemd")
    combined = CombinedRuntimeProvider(docker, systemd)

    observations = asyncio.run(combined.observe(catalog))

    # Regression: the old adapter unpacked the pydantic models' fields
    # (`[*docker_obs, *systemd_obs]`), which would yield 14 raw field values
    # and no ProviderObservation instances at all.
    assert len(observations) == 2
    assert all(isinstance(o, ProviderObservation) for o in observations)
    assert {o.provider for o in observations} == {"docker", "systemd"}
    assert observations[0] is docker.last_observation
    assert observations[1] is systemd.last_observation
    assert docker.observe_calls == 1
    assert systemd.observe_calls == 1


def test_read_logs_delegates_by_binding_provider() -> None:
    docker = RecordingProvider("docker")
    systemd = RecordingProvider("systemd")
    combined = CombinedRuntimeProvider(docker, systemd)

    asyncio.run(
        combined.read_logs(
            LogBinding(
                provider="docker", service_id="t3-code", source="s", reference="r"
            ),
            tail=10,
            before=None,
        )
    )
    asyncio.run(
        combined.read_logs(
            LogBinding(
                provider="systemd", service_id="demo-worker", source="s", reference="r"
            ),
            tail=10,
            before=None,
        )
    )
    assert docker.log_reads == ["docker"]
    assert systemd.log_reads == ["systemd"]
