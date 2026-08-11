"""Canonical Pydantic models for the Hyperion catalog and API.

The JSON Schema generated from this module is normative and must match
`schema/services.schema.json`; the OpenAPI generated from the FastAPI app must
match `schema/openapi.yaml` (enforced by contract parity tests).

PEP 695 aliases (`type id = ...`) become `$defs` entries named after the alias,
so the shared string types and union discriminators mirror the committed
schema's `$defs` keys exactly. Plain `TypeAlias` assignments are inlined.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Shared field types (mirror schema/services.schema.json $defs)
# ---------------------------------------------------------------------------

type id = Annotated[
    str, Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", min_length=1, max_length=64)
]
# API-facing alias of `id` with identical constraints, duplicated because
# `type Id = id` makes FastAPI emit a lowercase `#/components/schemas/id` ref
# for path parameters; the catalog contract keeps its lowercase `$defs.id` key.
type Id = Annotated[
    str, Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", min_length=1, max_length=64)
]
type selector = Annotated[
    str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]*$", min_length=1, max_length=128)
]
SystemdUnit: TypeAlias = Annotated[  # noqa: UP040 - `type` keyword would emit a $defs entry
    str,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]*\.service$", min_length=9, max_length=128),
]
HttpsUrl: TypeAlias = Annotated[str, Field(pattern=r"^https://")]  # noqa: UP040 - inlined by design

TerritoryName = Literal["applications", "services", "foundations"]
ServiceKind = Literal["web", "api", "mcp", "worker", "infrastructure"]
ComponentRole = Literal["primary", "worker", "dependency", "sidecar", "shared"]
ComponentState = Literal[
    "running", "starting", "restarting", "paused", "stopped", "missing", "unknown"
]
HealthState = Literal["healthy", "starting", "unhealthy", "unconfigured", "unknown"]
type ServiceState = Literal["reachable", "degraded", "down", "dormant", "unknown"]


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------------
# Catalog manifest (schema/services.schema.json)
# ---------------------------------------------------------------------------


class ComponentBase(CatalogModel):
    selector: selector
    label: Annotated[str | None, Field(default=None, min_length=1, max_length=64)]
    role: ComponentRole
    required: bool


class DockerComponent(ComponentBase):
    """Compose service component; selector is a plain Compose service name."""


class SystemdComponent(ComponentBase):
    selector: SystemdUnit


class DockerRuntime(CatalogModel):
    provider: Literal["docker-compose"]
    project: selector
    components: Annotated[list[DockerComponent], Field(min_length=1)]


class SystemdRuntime(CatalogModel):
    provider: Literal["systemd"]
    components: Annotated[list[SystemdComponent], Field(min_length=1)]


type Runtime = DockerRuntime | SystemdRuntime


class OpenAction(CatalogModel):
    type: Literal["open"]
    url: HttpsUrl


class CopyAction(CatalogModel):
    type: Literal["copy"]
    url: HttpsUrl


class NoneAction(CatalogModel):
    type: Literal["none"]


type Action = OpenAction | CopyAction | NoneAction


class RouteProbe(CatalogModel):
    method: Literal["GET", "HEAD"]
    url: HttpsUrl
    accepted_status_classes: Annotated[
        list[Literal[2, 3, 4]],
        Field(
            alias="acceptedStatusClasses",
            default=[2, 3, 4],
            min_length=1,
            json_schema_extra={"uniqueItems": True},
        ),
    ]
    timeout_ms: Annotated[int, Field(alias="timeoutMs", default=3000, ge=250, le=10000)]
    slow_after_ms: Annotated[int, Field(alias="slowAfterMs", default=1200, ge=100, le=10000)]
    failure_threshold: Annotated[int, Field(alias="failureThreshold", default=2, ge=1, le=5)]
    follow_redirects: Annotated[bool, Field(alias="followRedirects", default=False)]


class Logs(CatalogModel):
    sources: Annotated[list[selector], Field(json_schema_extra={"uniqueItems": True})]
    default_tail: Annotated[Literal[50, 100, 250, 500], Field(alias="defaultTail", default=100)]

    @field_validator("sources")
    @classmethod
    def _sources_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("sources must be unique")
        return value


class Service(CatalogModel):
    service_id: Annotated[id, Field(alias="id")]
    name: Annotated[str, Field(min_length=1, max_length=64)]
    description: Annotated[str, Field(min_length=1, max_length=160)]
    territory: TerritoryName
    kind: ServiceKind
    intent: Literal["active", "dormant"] = "active"
    action: Action
    runtime: Runtime
    route_probe: Annotated[RouteProbe | None, Field(alias="routeProbe", default=None)]
    logs: Logs
    dependencies: Annotated[list[id], Field(default=[], json_schema_extra={"uniqueItems": True})]

    @field_validator("dependencies")
    @classmethod
    def _dependencies_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("dependencies must be unique")
        return value


class Catalog(CatalogModel):
    model_config = ConfigDict(
        json_schema_extra={
            "description": "Version 1 manifest for logical services and their "
            "allowlisted runtime components."
        }
    )
    version: Literal[1]
    services: list[Service]


# ---------------------------------------------------------------------------
# API snapshot models (schema/openapi.yaml components)
# ---------------------------------------------------------------------------


def _to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class HealthResponse(ApiModel):
    status: Literal["ok", "ready"]


class ProviderStatus(ApiModel):
    provider: Literal["docker", "systemd", "probe"]
    state: Literal["available", "degraded", "unavailable"]
    observed_at: datetime | None
    message: str | None = Field(max_length=240)


class StateSummary(ApiModel):
    total: int = Field(ge=0)
    reachable: int = Field(ge=0)
    degraded: int = Field(ge=0)
    down: int = Field(ge=0)
    dormant: int = Field(ge=0)
    unknown: int = Field(ge=0)


class Territory(ApiModel):
    id: TerritoryName
    label: Literal["Applications", "Services", "Foundations"]
    order: Literal[1, 2, 3]


class OpenActionSnapshot(ApiModel):
    type: Literal["open"]
    url: Annotated[str, Field(json_schema_extra={"format": "uri"})]


class CopyActionSnapshot(ApiModel):
    type: Literal["copy"]
    url: Annotated[str, Field(json_schema_extra={"format": "uri"})]


class NoneActionSnapshot(ApiModel):
    type: Literal["none"]


type ServiceAction = OpenActionSnapshot | CopyActionSnapshot | NoneActionSnapshot


class StateReason(ApiModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    severity: Literal["info", "warning", "critical"]
    message: str = Field(max_length=240)
    component_key: str | None


class RouteSnapshot(ApiModel):
    state: Literal["reachable", "slow", "failed", "unknown"]
    status_code: int | None = Field(ge=100, le=599)
    latency_ms: int | None = Field(ge=0)
    consecutive_failures: int = Field(ge=0)
    observed_at: datetime | None
    error: Literal["dns", "timeout", "tls", "connection", "http", "unknown"] | None


class ComponentSnapshot(ApiModel):
    key: str = Field(max_length=128)
    label: str = Field(max_length=64)
    provider: Literal["docker", "systemd"]
    provider_ref: str | None = Field(max_length=128)
    role: ComponentRole
    required: bool
    state: ComponentState
    health: HealthState
    observed_at: datetime | None
    image: str | None = Field(max_length=256)
    uptime_seconds: int | None = Field(ge=0)
    restart_count: int | None = Field(ge=0)
    cpu_percent: float | None = Field(ge=0)
    memory_bytes: int | None = Field(ge=0)
    ambiguous: bool = False


class DependencySnapshot(ApiModel):
    service_id: Id
    state: ServiceState


class LogSource(ApiModel):
    key: str = Field(max_length=128)
    label: str = Field(max_length=64)
    provider: Literal["docker", "journald"]
    available: bool


class UnmappedRuntime(ApiModel):
    provider: Literal["docker", "systemd"]
    reference: str = Field(max_length=128)
    project: str | None = Field(max_length=128)
    component: str | None = Field(max_length=128)
    state: str = Field(max_length=64)


class Diagnostics(ApiModel):
    unmapped_runtimes: list[UnmappedRuntime]
    warnings: list[Annotated[str, Field(max_length=240)]]


class ServiceSnapshot(ApiModel):
    id: Id
    name: str = Field(max_length=64)
    description: str = Field(max_length=160)
    territory: TerritoryName
    kind: ServiceKind
    intent: Literal["active", "dormant"]
    action: ServiceAction
    state: ServiceState
    state_reasons: list[StateReason]
    observed_at: datetime
    route: RouteSnapshot | None
    components: list[ComponentSnapshot]
    dependencies: list[DependencySnapshot]
    log_sources: list[LogSource]


class AtlasSnapshot(ApiModel):
    schema_version: Literal[1]
    generated_at: datetime
    catalog_revision: str = Field(
        description="SHA-256 digest of the validated, normalized catalog."
    )
    fresh: bool
    providers: list[ProviderStatus]
    summary: StateSummary
    territories: list[Territory]
    services: list[ServiceSnapshot]
    diagnostics: Diagnostics


class LogRecord(ApiModel):
    timestamp: datetime | None
    source: str = Field(max_length=128)
    provider: Literal["docker", "journald"]
    stream: Literal["stdout", "stderr", "journal", "unknown"]
    severity: Literal[
        "debug", "info", "notice", "warning", "error", "critical", "alert", "emergency"
    ] | None
    message: str = Field(max_length=16384)
    truncated: bool


class LogsResponse(ApiModel):
    service_id: Id
    requested_at: datetime
    source: str | None
    records: list[LogRecord] = Field(max_length=500)
    truncated: bool


class ErrorDetail(ApiModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    message: str = Field(max_length=240)
    retryable: bool


class ErrorResponse(ApiModel):
    error: ErrorDetail


# ---------------------------------------------------------------------------
# Host telemetry models (operations-console Phase 2)
# ---------------------------------------------------------------------------

type FilesystemState = Literal["ok", "full", "inode_pressure", "degraded", "unavailable"]
type InterfaceState = Literal["up", "down", "unknown"]


class FilesystemEvidence(ApiModel):
    mount_point: str = Field(max_length=240)
    device: str = Field(max_length=240)
    fstype: str = Field(max_length=64)
    total_bytes: int | None = Field(ge=0)
    free_bytes: int | None = Field(ge=0)
    used_bytes: int | None = Field(ge=0)
    used_percent: float | None = Field(ge=0, le=100)
    total_inodes: int | None = Field(ge=0)
    free_inodes: int | None = Field(ge=0)
    inode_used_percent: float | None = Field(ge=0, le=100)
    state: FilesystemState


class NetworkInterfaceEvidence(ApiModel):
    name: str = Field(max_length=64)
    state: InterfaceState
    rx_bytes_total: int | None = Field(ge=0)
    tx_bytes_total: int | None = Field(ge=0)
    rx_bytes_per_second: float | None = Field(ge=0)
    tx_bytes_per_second: float | None = Field(ge=0)


class HostMemoryEvidence(ApiModel):
    total_bytes: int | None = Field(ge=0)
    available_bytes: int | None = Field(ge=0)
    used_bytes: int | None = Field(ge=0)
    used_percent: float | None = Field(ge=0, le=100)
    swap_total_bytes: int | None = Field(ge=0)
    swap_free_bytes: int | None = Field(ge=0)
    swap_used_percent: float | None = Field(ge=0, le=100)


class HostCpuEvidence(ApiModel):
    utilization_percent: float | None = Field(ge=0, le=100)
    load_average_1m: float | None = Field(ge=0)
    load_average_5m: float | None = Field(ge=0)
    load_average_15m: float | None = Field(ge=0)


class PressureEvidence(ApiModel):
    kind: Literal["cpu", "io", "memory"]
    some_avg_10: float | None = Field(ge=0)
    some_avg_300: float | None = Field(ge=0)
    full_avg_10: float | None = Field(ge=0)


class TemperatureEvidence(ApiModel):
    zone: str = Field(max_length=128)
    celsius: float | None


class HostEvidence(ApiModel):
    observed_at: datetime
    fresh: bool
    hostname: str | None = Field(default=None, max_length=64)
    uptime_seconds: float | None = Field(ge=0)
    boot_time: datetime | None
    cpu: HostCpuEvidence
    memory: HostMemoryEvidence
    filesystems: list[FilesystemEvidence]
    interfaces: list[NetworkInterfaceEvidence]
    pressure: list[PressureEvidence]
    temperatures: list[TemperatureEvidence]
    provider_state: Literal["available", "degraded", "unavailable"]
    provider_message: str | None = Field(default=None, max_length=240)


class HostHistoryResponse(ApiModel):
    window_seconds: int = Field(ge=0)
    observed_at: datetime
    samples: list[HostEvidence]


# ---------------------------------------------------------------------------
# Units inventory models (operations-console Phase 3)
# ---------------------------------------------------------------------------

type UnitState = Literal[
    "active", "activating", "reloading", "deactivating", "inactive", "failed", "unknown"
]
type UnitFileState = Literal[
    "enabled", "enabled-runtime", "linked", "masked", "disabled", "static",
    "indirect", "generated", "transient", "bad", "not-found", "unknown"
]


class UnitSnapshot(ApiModel):
    name: str = Field(max_length=128)
    description: str = Field(default="", max_length=256)
    load_state: Literal["loaded", "not-found", "error", "masked", "unknown"] = "unknown"
    active_state: UnitState = "unknown"
    sub_state: str = Field(default="unknown", max_length=64)
    enabled_state: UnitFileState = "unknown"
    active_entered: datetime | None
    main_pid: int | None = Field(ge=0)
    restart_count: int | None = Field(ge=0)
    memory_bytes: int | None = Field(ge=0)
    related_timer: str | None = Field(default=None, max_length=128)
    dependencies: list[str] = Field(default=[], max_length=64)
    related_service: str | None = Field(default=None, max_length=64)
    protection: Literal["protected", "allowlisted", "ordinary"] = "ordinary"
    curated: bool = False


class UnitInventoryResponse(ApiModel):
    generated_at: datetime
    fresh: bool
    counts: dict[str, int] = Field(description="Counts by active state.")
    units: list[UnitSnapshot]


class UnitLogsResponse(ApiModel):
    unit: str = Field(max_length=128)
    requested_at: datetime
    records: list[LogRecord] = Field(max_length=500)
    truncated: bool


# ---------------------------------------------------------------------------
# Schedule inventory models (operations-console Phase 3/6)
# ---------------------------------------------------------------------------

type ScheduleSource = Literal["systemd", "cron", "managed"]
type ScheduleResult = Literal["success", "failed", "not_observed", "pending", "unknown"]


class ScheduleSnapshot(ApiModel):
    id: str = Field(max_length=128)
    name: str = Field(max_length=128)
    human_readable: str = Field(default="", max_length=256)
    raw_expression: str = Field(default="", max_length=512)
    source: ScheduleSource
    next_run: datetime | None
    last_run: datetime | None
    last_result: ScheduleResult = "unknown"
    owner: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    target: str = Field(default="", max_length=256)
    provenance: str = Field(default="", max_length=256)
    related_service: str | None = Field(default=None, max_length=64)
    managed: bool = False


class ScheduleInventoryResponse(ApiModel):
    generated_at: datetime
    fresh: bool
    schedules: list[ScheduleSnapshot]


class ManagedScheduleDefinition(ApiModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$", min_length=1, max_length=64)
    description: str = Field(default="", max_length=256)
    on_calendar: str = Field(min_length=1, max_length=128)
    executable: str = Field(pattern=r"^/[^ \t]+$", min_length=1, max_length=256)
    arguments: list[str] = Field(default=[], max_length=64)
    user: str = Field(pattern=r"^[a-z_][a-z0-9_-]*$", default="root", max_length=64)
    working_directory: str | None = Field(default=None, pattern=r"^/[^ \t]*$", max_length=256)
    timeout_seconds: int = Field(default=3600, ge=1, le=86400)
    overlap_policy: Literal["allow", "drop", "queue"] = "drop"
    missed_run_behavior: Literal["ignore", "catch_up"] = "ignore"
    related_service: str | None = Field(default=None, max_length=64)


class ManagedScheduleView(ApiModel):
    definition: ManagedScheduleDefinition
    revision: int = Field(ge=0)
    enabled: bool = True
    service_unit: str = Field(max_length=128)
    timer_unit: str = Field(max_length=128)
    installed: bool = False
    last_result: ScheduleResult = "unknown"
    last_run: datetime | None


# ---------------------------------------------------------------------------
# Activity models (operations-console Phase 4)
# ---------------------------------------------------------------------------

type ActivityKind = Literal[
    "host_threshold",
    "unit_transition",
    "service_transition",
    "schedule_execution",
    "schedule_change",
    "operation",
]
type ActivityResult = Literal["success", "failure", "warning", "pending", "info", "denied"]


class ActivityRecord(ApiModel):
    id: str = Field(max_length=64)
    occurred_at: datetime
    kind: ActivityKind
    target_type: Literal["host", "unit", "service", "schedule", "system"] = "system"
    target: str = Field(max_length=128)
    identity: str = Field(default="system", max_length=64)
    result: ActivityResult = "info"
    message: str = Field(max_length=512)
    evidence: dict[str, str] = Field(default={}, max_length=32)


class ActivityResponse(ApiModel):
    since: datetime | None
    requested_at: datetime
    records: list[ActivityRecord] = Field(max_length=200)


# ---------------------------------------------------------------------------
# Operations models (operations-console Phase 5)
# ---------------------------------------------------------------------------

type OperationKind = Literal["start", "stop", "restart", "enable", "disable", "trigger"]
type OperationState = Literal[
    "pending", "success", "partial", "timeout", "failed", "denied", "stale",
    "helper_unavailable",
]


class OperationRequest(ApiModel):
    operation: OperationKind
    expected_state: UnitState | None = None
    reason: str = Field(default="", max_length=240)


class OperationResult(ApiModel):
    id: str = Field(max_length=64)
    unit: str = Field(max_length=128)
    operation: OperationKind
    state: OperationState
    message: str = Field(default="", max_length=512)
    requested_at: datetime
    reconciled_at: datetime | None
    evidence: dict[str, str] = Field(default={}, max_length=32)
