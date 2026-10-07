"""Canonical HTTP and MCP diagnostics contracts. Unknown facts remain explicit nulls."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ..models import DiagnosticEndpoint, Id

ProcessRef = Annotated[
    str, Field(pattern=r"^proc_[A-Za-z0-9_-]{32}$", min_length=37, max_length=37)
]
EndpointId = Annotated[
    str, Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", min_length=1, max_length=64)
]


class DiagnosticModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeInput(DiagnosticModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    service: Id


class ListenersInput(RuntimeInput):
    endpoint_id: EndpointId


class ProcessInput(RuntimeInput):
    process_ref: ProcessRef


DIAGNOSTIC_INPUTS: dict[str, type[RuntimeInput]] = {
    "get_service_runtime": RuntimeInput,
    "get_endpoint_listeners": ListenersInput,
    "inspect_process": ProcessInput,
}


class Issue(DiagnosticModel):
    code: str = Field(max_length=64)
    fields: list[Annotated[str, Field(max_length=128)]] = Field(max_length=16)
    retryable: bool


class Scope(DiagnosticModel):
    host_ref: str = Field(max_length=64)
    boot_id: str | None
    pid_namespace_ref: str | None
    network_namespace_ref: str | None


class Collection(DiagnosticModel):
    started_at: AwareDatetime
    completed_at: AwareDatetime
    atomic: Literal[False]
    consistency: Literal["stable", "changed", "unknown"]


class Evidence[T](DiagnosticModel):
    schema_version: Literal["1.0"]
    snapshot_id: str
    service: Id
    status: Literal["complete", "partial", "unavailable"]
    scope: Scope
    collection: Collection
    data: T | None
    issues: list[Issue]
    source_uri: str


class ManagerScope(DiagnosticModel):
    kind: Literal["system", "user", "unknown"]
    uid: int | None


class ProcessIdentity(DiagnosticModel):
    pid: int = Field(ge=1)
    process_ref: ProcessRef | None
    name: str = Field(max_length=64)
    start_ticks: int = Field(ge=0)


class LastExecution(DiagnosticModel):
    pid: int | None
    result: str | None
    exit_code: int | None
    exit_status: int | None
    started_at: AwareDatetime | None
    exited_at: AwareDatetime | None
    started_monotonic_us: int | None
    exited_monotonic_us: int | None


class ProcessCoverage(DiagnosticModel):
    process_enumeration: Literal["complete", "partial", "unavailable"]
    truncated: bool


class RuntimeData(DiagnosticModel):
    runtime_provider: Literal["systemd"]
    unit_name: str
    manager_scope: ManagerScope
    load_state: str | None
    active_state: str | None
    sub_state: str | None
    main_pid: int | None
    main_process_ref: ProcessRef | None
    control_group: str | None
    restart_count: int | None
    last_execution: LastExecution
    processes: list[ProcessIdentity] | None = Field(max_length=24)
    coverage: ProcessCoverage
    endpoints: dict[str, DiagnosticEndpoint]


class Listener(DiagnosticModel):
    socket_id: str
    inode: int = Field(ge=1)
    family: Literal["ipv4", "ipv6"]
    protocol: Literal["tcp"]
    network_namespace_ref: str
    local_address: str
    local_port: int
    state: Literal["LISTEN"]
    endpoint_overlap: Literal["exact", "wildcard", "potential_ipv6"]
    incompatible_bind: Literal["unknown"]
    owners: list[ProcessIdentity] | None = Field(max_length=12)


class ListenerCoverage(DiagnosticModel):
    socket_enumeration: Literal["complete", "partial", "unavailable"]
    owner_resolution: Literal["complete", "partial", "unavailable"]
    truncated: bool


class ListenersData(DiagnosticModel):
    endpoint_id: EndpointId
    endpoint: DiagnosticEndpoint
    listeners: list[Listener] | None = Field(max_length=24)
    coverage: ListenerCoverage


class CommandSummary(DiagnosticModel):
    executable_name: str | None
    script_name: str | None
    arguments_disclosed: Literal[False]


class Owner(DiagnosticModel):
    kind: Literal["systemd", "container", "unknown"]
    unit_name: str | None
    manager_scope: ManagerScope
    container_ref: str | None
    method: Literal["cgroup_path", "unavailable"]


class Relationship(DiagnosticModel):
    kind: Literal["main_process", "same_unit", "another_owner", "unknown"]
    method: Literal[
        "systemd_main_pid_and_cgroup", "unit_control_group", "cgroup_unit_identity", "unavailable"
    ]
    expected_unit: str | None
    expected_control_group: str | None


class Parent(DiagnosticModel):
    pid: int = Field(ge=1)
    identity: ProcessIdentity | None
    observation: Literal["current_parent"]


class ControlGroup(DiagnosticModel):
    hierarchy_id: int = Field(ge=0)
    controllers: list[Annotated[str, Field(max_length=64)]] = Field(max_length=16)
    path: str = Field(max_length=256)


class ProcessData(ProcessIdentity):
    state: str
    started_at: AwareDatetime | None
    parent: Parent | None
    executable: str | None
    working_directory: str | None
    command_summary: CommandSummary | None
    control_groups: list[ControlGroup] | None = Field(max_length=8)
    owner: Owner
    relationship_to_service: Relationship


class ServiceRuntimeResult(Evidence[RuntimeData]):
    pass


class EndpointListenersResult(Evidence[ListenersData]):
    pass


class ProcessResult(Evidence[ProcessData]):
    pass
