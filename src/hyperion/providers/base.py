"""Provider contracts.

Providers return evidence; they never derive a service aggregate state
(TECHNICAL-DESIGN.md section 6.4). Provider exceptions are captured at the
provider boundary and never cross into the reconciler.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from ..models import Catalog, ComponentState, HealthState, id, selector


def _to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


class EvidenceModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="forbid")


class ComponentEvidence(EvidenceModel):
    service_id: id
    selector: selector
    provider: Literal["docker", "systemd"]
    state: ComponentState
    health: HealthState
    reference: str | None = None
    image: str | None = None
    uptime_seconds: int | None = None
    restart_count: int | None = None
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    observed_at: datetime
    ambiguous: bool = False


class UnmappedRuntimeEvidence(EvidenceModel):
    provider: Literal["docker", "systemd"]
    reference: str
    project: str | None = None
    component: str | None = None
    state: str


class ProviderObservation(EvidenceModel):
    provider: Literal["docker", "systemd"]
    state: Literal["available", "degraded", "unavailable"]
    observed_at: datetime
    components: list[ComponentEvidence] = []
    unmapped: list[UnmappedRuntimeEvidence] = []
    conflicts: list[str] = []
    error: str | None = None


class ProbeEvidence(EvidenceModel):
    url: str
    state: Literal["reachable", "slow", "failed", "unknown"]
    status_code: int | None = None
    latency_ms: int | None = None
    consecutive_failures: int = 0
    observed_at: datetime
    error: Literal["dns", "timeout", "tls", "connection", "http", "unknown"] | None = None


class ProbeObservation(EvidenceModel):
    provider: Literal["probe"]
    state: Literal["available", "degraded", "unavailable"]
    observed_at: datetime
    results: list[ProbeEvidence] = []
    error: str | None = None


class LogBinding(EvidenceModel):
    provider: Literal["docker", "systemd"]
    service_id: id
    source: selector
    reference: str  # container name/id or systemd unit


class LogRecordIn(EvidenceModel):
    timestamp: datetime | None = None
    source: str
    provider: Literal["docker", "journald"]
    stream: Literal["stdout", "stderr", "journal", "unknown"]
    severity: Literal[
        "debug", "info", "notice", "warning", "error", "critical", "alert", "emergency"
    ] | None = None
    message: str


class RuntimeProvider(Protocol):
    async def discover_catalog(self, base: Catalog) -> Catalog: ...

    async def observe(self, catalog: Catalog) -> list[ProviderObservation]: ...

    async def read_logs(
        self, binding: LogBinding, tail: int, before: datetime | None
    ) -> list[LogRecordIn]: ...


class RouteProbeProvider(Protocol):
    async def observe(
        self,
        probes: tuple[str, ...],
        config: dict[str, tuple[float, float]] | None = None,
    ) -> ProbeObservation: ...
