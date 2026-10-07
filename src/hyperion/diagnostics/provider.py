"""Host-owned diagnostics: resource scope, process grants, budgets, and evidence."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..models import Service
from ..providers.systemd import SystemdProvider
from .linux import (
    LinuxDiagnostics,
    Process,
    ReadFailure,
    error_code,
    in_unit,
    issue,
    manager_cgroup_paths,
)
from .models import (
    Collection,
    ControlGroup,
    EndpointListenersResult,
    Issue,
    LastExecution,
    Listener,
    ListenerCoverage,
    ListenersData,
    ManagerScope,
    Owner,
    Parent,
    ProcessCoverage,
    ProcessData,
    ProcessIdentity,
    ProcessResult,
    Relationship,
    RuntimeData,
    Scope,
    ServiceRuntimeResult,
)

COLLECTION_SECONDS = 5.0
REFERENCE_SECONDS = 120.0
MAX_REFERENCES = 4096
MAX_RESULT_CHARACTERS = 12000


class DiagnosticRequestError(ValueError):
    def __init__(self, code: str, status: int = 403) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(frozen=True)
class ProcessGrant:
    principal: str
    service: str
    scope: Scope
    process: Process
    endpoint_id: str | None
    expires: float


def _primary_unit(service: Service) -> str | None:
    if service.runtime.provider != "systemd":
        return None
    return next(c.selector for c in service.runtime.components if c.role == "primary")


def _number(props: dict[str, str], name: str) -> int | None:
    value = props.get(name, "")
    return int(value) if value.isdigit() else None


def _timestamp(props: dict[str, str], name: str) -> datetime | None:
    value = props.get(name, "")
    if not value or value in {"0", "n/a"}:
        return None
    if value.isdigit():
        try:
            return datetime.fromtimestamp(int(value) / 1_000_000, UTC)
        except (ValueError, OSError, OverflowError):
            return None
    for pattern in ("%a %Y-%m-%d %H:%M:%S.%f UTC", "%a %Y-%m-%d %H:%M:%S UTC"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


class DiagnosticsProvider:
    def __init__(
        self,
        linux: LinuxDiagnostics | None = None,
        systemd: SystemdProvider | None = None,
        *,
        enabled: bool = True,
    ) -> None:
        self.linux = linux or LinuxDiagnostics()
        self.systemd = systemd or SystemdProvider()
        self.enabled = enabled
        self._grants: OrderedDict[str, ProcessGrant] = OrderedDict()

    def _grant(
        self,
        principal: str,
        service: Service,
        scope: Scope,
        process: Process,
        endpoint_id: str | None = None,
    ) -> ProcessIdentity:
        now = time.monotonic()
        while self._grants and (
            next(iter(self._grants.values())).expires <= now or len(self._grants) >= MAX_REFERENCES
        ):
            self._grants.popitem(last=False)
        reference = "proc_" + secrets.token_urlsafe(24)
        self._grants[reference] = ProcessGrant(
            principal, service.service_id, scope, process, endpoint_id, now + REFERENCE_SECONDS
        )
        return ProcessIdentity(
            pid=process.pid,
            process_ref=reference,
            name=process.name,
            start_ticks=process.start_ticks,
        )

    def _resolve(self, principal: str, service: Service, reference: str) -> ProcessGrant:
        grant = self._grants.get(reference)
        if grant is None or grant.principal != principal or grant.service != service.service_id:
            raise DiagnosticRequestError("PROCESS_REFERENCE_OUT_OF_SCOPE")
        if grant.expires <= time.monotonic():
            self._grants.pop(reference, None)
            raise DiagnosticRequestError("PROCESS_REFERENCE_EXPIRED", 410)
        return grant

    async def _collect(
        self,
        service: Service,
        collect: Callable[[Scope, float, list[Issue]], Awaitable[Any]],
    ) -> dict[str, Any]:
        started = datetime.now(UTC)
        snapshot_id = "diag_" + uuid4().hex
        scope = Scope(
            host_ref=self.linux.host_ref,
            boot_id=None,
            pid_namespace_ref=None,
            network_namespace_ref=None,
        )
        issues: list[Issue] = []
        data = None
        consistency = "unknown"
        try:
            if not self.enabled:
                raise ReadFailure("DIAGNOSTICS_DISABLED")
            deadline = time.monotonic() + COLLECTION_SECONDS
            async with asyncio.timeout(COLLECTION_SECONDS):
                scope = await asyncio.to_thread(self.linux.scope)
                data, changed = await collect(scope, deadline, issues)
                final_scope = await asyncio.to_thread(self.linux.scope)
                if final_scope != scope:
                    issues.append(issue("HOST_IDENTITY_CHANGED", "scope", retryable=True))
                    changed = True
                consistency = (
                    "changed"
                    if changed
                    else (
                        "unknown"
                        if any("collection.consistency" in i.fields for i in issues)
                        else "stable"
                    )
                )
        except TimeoutError:
            issues.append(issue("COLLECTION_TIMEOUT", "data", retryable=True))
        except (OSError, ValueError, ReadFailure) as exc:
            issues.append(issue(error_code(exc, "COLLECTION"), "data", retryable=True))
        unique = list({(i.code, tuple(i.fields)): i for i in issues}.values())
        result = {
            "schema_version": "1.0",
            "snapshot_id": snapshot_id,
            "service": service.service_id,
            "status": "unavailable" if data is None else "partial" if unique else "complete",
            "scope": scope.model_dump(mode="json"),
            "collection": Collection(
                started_at=started,
                completed_at=datetime.now(UTC),
                atomic=False,
                consistency=consistency,  # type: ignore[arg-type]
            ).model_dump(mode="json"),
            "data": data.model_dump(mode="json") if data is not None else None,
            "issues": [i.model_dump(mode="json") for i in unique],
            "source_uri": f"hyperion://diagnostics/{scope.host_ref}/{snapshot_id}",
        }
        self._bound_result(result)
        return result

    @staticmethod
    def _bound_result(result: dict[str, Any]) -> None:
        # Remove only bounded evidence lists, recording reduced coverage. Never
        # trim owners silently or add schema-breaking fields to structured evidence.
        while len(json.dumps(result, ensure_ascii=False)) > MAX_RESULT_CHARACTERS:
            data = result["data"]
            arrays: list[list[Any]] = []
            if isinstance(data, dict):
                for field in ("processes", "listeners"):
                    if isinstance(data.get(field), list) and data[field]:
                        arrays.append(data[field])
            if not arrays:
                result["data"] = None
                result["status"] = "unavailable"
                result["issues"] = [issue("RESULT_BUDGET_EXCEEDED", "data").model_dump()]
                break
            max(arrays, key=lambda a: len(json.dumps(a))).pop()
            data["coverage"]["truncated"] = True
            field = "process_enumeration" if "processes" in data else "socket_enumeration"
            data["coverage"][field] = "partial"
            result["status"] = "partial"
            budget_issue = issue("RESULT_TRUNCATED", "data." + field).model_dump()
            if budget_issue not in result["issues"]:
                result["issues"].append(budget_issue)

    async def get_service_runtime(self, service: Service, principal: str) -> ServiceRuntimeResult:
        async def collect(scope: Scope, deadline: float, issues: list[Issue]) -> tuple[Any, bool]:
            unit = _primary_unit(service)
            if unit is None:
                raise ReadFailure("RUNTIME_PROVIDER_UNSUPPORTED")
            props = await self.systemd.runtime_properties(unit)
            main_pid = _number(props, "MainPID")
            group = props.get("ControlGroup")
            processes: list[ProcessIdentity] | None = None
            coverage = ProcessCoverage(process_enumeration="unavailable", truncated=False)
            changed = False
            if group and group != "/":
                scan = await asyncio.to_thread(
                    self.linux.unit_processes, group, main_pid or 0, deadline
                )
                issues.extend(scan.issues)
                changed = scan.changed
                if changed:
                    issues.append(issue("PROCESS_SET_CHANGED", "processes", retryable=True))
                if scan.truncated:
                    issues.append(issue("PROCESS_ENUMERATION_TRUNCATED", "processes"))
                processes = [self._grant(principal, service, scope, p) for p in scan.processes]
                coverage = ProcessCoverage(
                    process_enumeration="partial"
                    if scan.issues or scan.truncated or changed
                    else "complete",
                    truncated=scan.truncated,
                )
            elif group == "" and main_pid == 0:
                processes = []
                coverage.process_enumeration = "complete"
            else:
                issues.append(issue("UNIT_CGROUP_UNAVAILABLE", "control_group", "processes"))
            main_ref = next((p.process_ref for p in processes or [] if p.pid == main_pid), None)
            if main_pid and main_ref is None:
                try:
                    process = await asyncio.to_thread(self.linux.process, main_pid)
                    confirm = await asyncio.to_thread(self.linux.process, main_pid)
                    if process.start_ticks != confirm.start_ticks:
                        changed = True
                        raise ReadFailure("PROCESS_IDENTITY_CHANGED")
                    main_ref = self._grant(principal, service, scope, process).process_ref
                except (OSError, ValueError, ReadFailure) as exc:
                    issues.append(
                        issue(error_code(exc, "PROCESS"), "main_process_ref", retryable=True)
                    )
            for prop, field in (("ControlGroup", "control_group"), ("NRestarts", "restart_count")):
                if prop not in props:
                    issues.append(issue("MANAGER_PROPERTY_UNAVAILABLE", field))
            for prop, field in (("MainPID", "main_pid"), ("NRestarts", "restart_count")):
                if _number(props, prop) is None:
                    issues.append(issue("MANAGER_PROPERTY_UNAVAILABLE", field))
            execution = LastExecution(
                pid=_number(props, "ExecMainPID"),
                result=props.get("Result"),
                exit_code=_number(props, "ExecMainCode"),
                exit_status=_number(props, "ExecMainStatus"),
                started_at=_timestamp(props, "ExecMainStartTimestamp"),
                exited_at=_timestamp(props, "ExecMainExitTimestamp"),
                started_monotonic_us=_number(props, "ExecMainStartTimestampMonotonic"),
                exited_monotonic_us=_number(props, "ExecMainExitTimestampMonotonic"),
            )
            for key, value in execution.model_dump().items():
                if value is None:
                    issues.append(issue("EXECUTION_FIELD_UNAVAILABLE", "last_execution." + key))
            data = RuntimeData(
                runtime_provider="systemd",
                unit_name=props.get("Id") or unit,
                manager_scope=ManagerScope(kind="system", uid=None),
                load_state=props.get("LoadState"),
                active_state=props.get("ActiveState"),
                sub_state=props.get("SubState"),
                main_pid=main_pid,
                main_process_ref=main_ref,
                control_group=group,
                restart_count=_number(props, "NRestarts"),
                last_execution=execution,
                processes=processes,
                coverage=coverage,
                endpoints=service.diagnostics.endpoints if service.diagnostics else {},
            )
            try:
                confirm_props = await self.systemd.runtime_properties(unit)
                if confirm_props != props:
                    changed = True
                    issues.append(issue("UNIT_STATE_CHANGED", "data", retryable=True))
                    data.main_process_ref = None
            except (OSError, ValueError, ReadFailure, TimeoutError) as exc:
                issues.append(issue(error_code(exc, "MANAGER_CHECK"), "collection.consistency"))
                data.main_process_ref = None
            return data, changed

        return ServiceRuntimeResult.model_validate(await self._collect(service, collect))

    async def get_endpoint_listeners(
        self,
        service: Service,
        principal: str,
        endpoint_id: str,
    ) -> EndpointListenersResult:
        if service.diagnostics is None or endpoint_id not in service.diagnostics.endpoints:
            raise DiagnosticRequestError("ENDPOINT_OUT_OF_SCOPE", 404)
        endpoint = service.diagnostics.endpoints[endpoint_id]

        async def collect(scope: Scope, deadline: float, issues: list[Issue]) -> tuple[Any, bool]:
            scan = await asyncio.to_thread(self.linux.listeners, endpoint, deadline)
            issues.extend(scan.issues)
            if scan.truncated:
                issues.append(issue("SOCKET_ENUMERATION_TRUNCATED", "listeners"))
            if scan.tables_read == 0 and not scan.sockets:
                raise ReadFailure("SOCKET_ENUMERATION_UNAVAILABLE")
            owners = await asyncio.to_thread(
                self.linux.socket_owners, {s.inode for s in scan.sockets}, deadline
            )
            issues.extend(owners.issues)
            if owners.truncated:
                issues.append(issue("OWNER_ENUMERATION_TRUNCATED", "listeners.owners"))
            if owners.changed:
                issues.append(issue("OWNER_SET_CHANGED", "listeners.owners", retryable=True))
            listeners = [
                Listener(
                    socket_id=f"{scope.boot_id}/{scope.network_namespace_ref}/tcp/{s.inode}",
                    inode=s.inode,
                    family=s.family,
                    protocol="tcp",
                    network_namespace_ref=scope.network_namespace_ref or "unknown",
                    local_address=s.address,
                    local_port=s.port,
                    state="LISTEN",
                    endpoint_overlap=s.overlap,
                    incompatible_bind="unknown",
                    owners=[
                        self._grant(principal, service, scope, p, endpoint_id)
                        for p in owners.owners[s.inode]
                    ]
                    or None,
                )
                for s in scan.sockets
            ]
            confirm = await asyncio.to_thread(self.linux.listeners, endpoint, deadline)
            issues.extend(confirm.issues)
            if confirm.issues or confirm.truncated:
                issues.append(issue("CONSISTENCY_CHECK_UNAVAILABLE", "collection.consistency"))
            socket_changed = set(confirm.sockets) != set(scan.sockets)
            changed = owners.changed or socket_changed
            if socket_changed:
                issues.append(issue("SOCKET_SET_CHANGED", "listeners", retryable=True))
            if confirm.truncated:
                issues.append(issue("SOCKET_ENUMERATION_TRUNCATED", "listeners"))
            data = ListenersData(
                endpoint_id=endpoint_id,
                endpoint=endpoint,
                listeners=listeners,
                coverage=ListenerCoverage(
                    socket_enumeration="partial"
                    if scan.issues
                    or scan.truncated
                    or confirm.issues
                    or confirm.truncated
                    or socket_changed
                    else "complete",
                    owner_resolution=(
                        "unavailable"
                        if scan.sockets and not any(owners.owners.values())
                        else "partial"
                        if owners.issues or owners.truncated or changed
                        else "complete"
                    ),
                    truncated=scan.truncated or owners.truncated or confirm.truncated,
                ),
            )
            return data, changed

        return EndpointListenersResult.model_validate(await self._collect(service, collect))

    async def inspect_process(
        self,
        service: Service,
        principal: str,
        process_ref: str,
    ) -> ProcessResult:
        grant = self._resolve(principal, service, process_ref)

        async def collect(scope: Scope, deadline: float, issues: list[Issue]) -> tuple[Any, bool]:
            if scope != grant.scope:
                issues.append(issue("PROCESS_IDENTITY_CHANGED", "data", retryable=True))
                return None, True
            try:
                process = await asyncio.to_thread(self.linux.process, grant.process.pid)
            except FileNotFoundError:
                issues.append(issue("PROCESS_EXITED", "data", retryable=True))
                return None, True
            if process.start_ticks != grant.process.start_ticks:
                issues.append(issue("PROCESS_IDENTITY_CHANGED", "data", retryable=True))
                return None, True
            unit = _primary_unit(service)
            props: dict[str, str] = {}
            if unit:
                try:
                    props = await self.systemd.runtime_properties(unit)
                except (OSError, ValueError, ReadFailure, TimeoutError) as exc:
                    issues.append(issue(error_code(exc, "MANAGER"), "relationship_to_service"))
            groups = None
            try:
                groups = await asyncio.to_thread(self.linux.control_groups, process.pid)
            except (OSError, ValueError, ReadFailure) as exc:
                issues.append(issue(error_code(exc, "PROCESS"), "control_groups", "owner"))
            same_unit = groups is not None and in_unit(groups, props.get("ControlGroup", ""))
            if grant.endpoint_id is None:
                if unit is not None and (
                    not props or (groups is None and _number(props, "MainPID") != process.pid)
                ):
                    raise ReadFailure("PROCESS_SCOPE_UNAVAILABLE")
                if not same_unit and _number(props, "MainPID") != process.pid:
                    issues.append(issue("PROCESS_SCOPE_CHANGED", "data", retryable=True))
                    return None, True
            else:
                # Reauthorize only this PID's descriptors, never expand a grant to
                # another process or inspect arbitrary relations recursively.
                if not await asyncio.to_thread(self._still_listener, service, grant, deadline):
                    issues.append(issue("PROCESS_SCOPE_CHANGED", "data", retryable=True))
                    return None, True
            metadata: dict[str, Any] = {}
            metadata_reads: dict[str, Callable[[], Any]] = {
                "executable": lambda: self.linux.path_metadata(process.pid, "exe"),
                "working_directory": lambda: self.linux.path_metadata(process.pid, "cwd"),
                "command_summary": lambda: self.linux.command_summary(process.pid),
                "started_at": lambda: self.linux.started_at(process.start_ticks),
            }
            for field, read in metadata_reads.items():
                try:
                    metadata[field] = await asyncio.to_thread(read)
                except (OSError, ValueError, ReadFailure) as exc:
                    metadata[field] = None
                    issues.append(issue(error_code(exc, "PROCESS_METADATA"), field))
            parent = None
            if process.parent_pid:
                parent_identity = None
                try:
                    observed = await asyncio.to_thread(self.linux.process, process.parent_pid)
                    confirmed = await asyncio.to_thread(self.linux.process, process.parent_pid)
                    if observed.start_ticks != confirmed.start_ticks:
                        raise ReadFailure("PARENT_IDENTITY_CHANGED")
                    # Parent identity is bounded context, not a new inspection grant.
                    parent_identity = ProcessIdentity(
                        pid=observed.pid,
                        process_ref=None,
                        name=observed.name,
                        start_ticks=observed.start_ticks,
                    )
                except (OSError, ValueError, ReadFailure) as exc:
                    issues.append(
                        issue(error_code(exc, "PARENT"), "parent.identity", retryable=True)
                    )
                parent = Parent(
                    pid=process.parent_pid, identity=parent_identity, observation="current_parent"
                )
            owner = _owner(groups)
            resolved_unit = props.get("Id") or unit
            relationship = Relationship(
                kind="unknown",
                method="unavailable",
                expected_unit=resolved_unit,
                expected_control_group=props.get("ControlGroup"),
            )
            if same_unit:
                relationship.kind = (
                    "main_process" if _number(props, "MainPID") == process.pid else "same_unit"
                )
                relationship.method = (
                    "systemd_main_pid_and_cgroup"
                    if relationship.kind == "main_process"
                    else "unit_control_group"
                )
            elif owner.kind != "unknown" and groups is not None and props.get("ControlGroup"):
                relationship.kind = "another_owner"
                relationship.method = "unit_control_group"
            elif (
                unit
                and owner.kind != "unknown"
                and (
                    owner.kind == "container"
                    or owner.unit_name != resolved_unit
                    or owner.manager_scope.kind != "system"
                )
                and (
                    owner.manager_scope.kind == "user"
                    or not any(
                        resolved_unit in path.split("/")
                        for path in manager_cgroup_paths(groups or [])
                    )
                )
            ):
                relationship.kind = "another_owner"
                relationship.method = "cgroup_unit_identity"
            if owner.kind == "unknown":
                issues.append(issue("PROCESS_OWNER_UNKNOWN", "owner"))
            if relationship.kind == "unknown":
                issues.append(issue("PROCESS_RELATIONSHIP_UNKNOWN", "relationship_to_service"))
            data = ProcessData(
                pid=process.pid,
                process_ref=process_ref,
                name=process.name,
                start_ticks=process.start_ticks,
                state=process.state,
                parent=parent,
                control_groups=groups,
                owner=owner,
                relationship_to_service=relationship,
                **metadata,
            )
            changed = False
            for field, read in metadata_reads.items():
                if metadata[field] is None or field == "started_at":
                    continue
                try:
                    if await asyncio.to_thread(read) != metadata[field]:
                        changed = True
                        issues.append(issue("PROCESS_METADATA_CHANGED", field, retryable=True))
                except (OSError, ValueError, ReadFailure) as exc:
                    issues.append(
                        issue(
                            error_code(exc, "PROCESS_METADATA_CHECK"),
                            field,
                            "collection.consistency",
                        )
                    )
            if groups is not None:
                try:
                    confirm_groups = await asyncio.to_thread(self.linux.control_groups, process.pid)
                    if confirm_groups != groups:
                        changed = True
                        issues.append(
                            issue(
                                "PROCESS_MEMBERSHIP_CHANGED",
                                "control_groups",
                                "relationship_to_service",
                                retryable=True,
                            )
                        )
                except FileNotFoundError:
                    issues.append(issue("PROCESS_EXITED", "data", retryable=True))
                    return None, True
                except (OSError, ValueError, ReadFailure) as exc:
                    issues.append(
                        issue(
                            error_code(exc, "PROCESS_MEMBERSHIP_CHECK"),
                            "control_groups",
                            "collection.consistency",
                        )
                    )
            if props and unit:
                try:
                    if await self.systemd.runtime_properties(unit) != props:
                        changed = True
                        issues.append(
                            issue("UNIT_STATE_CHANGED", "relationship_to_service", retryable=True)
                        )
                except (OSError, ValueError, ReadFailure, TimeoutError) as exc:
                    issues.append(issue(error_code(exc, "MANAGER_CHECK"), "collection.consistency"))
            try:
                confirm = await asyncio.to_thread(self.linux.process, process.pid)
            except FileNotFoundError:
                issues.append(issue("PROCESS_EXITED", "data", retryable=True))
                return None, True
            if confirm.start_ticks != process.start_ticks:
                issues.append(issue("PROCESS_IDENTITY_CHANGED", "data", retryable=True))
                return None, True
            if (confirm.parent_pid, confirm.name) != (process.parent_pid, process.name):
                changed = True
                issues.append(issue("PROCESS_METADATA_CHANGED", "parent", "name", retryable=True))
            return data, changed

        return ProcessResult.model_validate(await self._collect(service, collect))

    def _still_listener(self, service: Service, grant: ProcessGrant, deadline: float) -> bool:
        if service.diagnostics is None or grant.endpoint_id not in service.diagnostics.endpoints:
            return False
        endpoint = service.diagnostics.endpoints[grant.endpoint_id]
        sockets = self.linux.listeners(endpoint, deadline)
        if not sockets.sockets:
            if sockets.issues or sockets.truncated:
                raise ReadFailure("PROCESS_SCOPE_UNAVAILABLE")
            return False
        fds = self.linux.socket_fds(grant.process.pid, {s.inode for s in sockets.sockets}, deadline)
        if fds.matches:
            return True
        if sockets.issues or sockets.truncated or fds.issues or fds.truncated or fds.changed:
            raise ReadFailure("PROCESS_SCOPE_UNAVAILABLE")
        return False


def _owner(groups: list[ControlGroup] | None) -> Owner:
    unknown = Owner(
        kind="unknown",
        unit_name=None,
        manager_scope=ManagerScope(kind="unknown", uid=None),
        container_ref=None,
        method="unavailable",
    )
    if groups is None:
        return unknown
    for group in manager_cgroup_paths(groups):
        container = re.search(
            r"(?:docker[-/]|cri-containerd-)([0-9a-f]{12,64})(?:\.scope|/|$)", group
        )
        if container:
            return Owner(
                kind="container",
                unit_name=None,
                manager_scope=unknown.manager_scope,
                container_ref=container[1],
                method="cgroup_path",
            )
        parts = group.split("/")
        units = [p for p in parts if p.endswith((".service", ".scope"))]
        if units:
            user = re.search(r"/user-(\d+)\.slice/", group)
            return Owner(
                kind="systemd",
                unit_name=units[-1],
                container_ref=None,
                manager_scope=ManagerScope(
                    kind="user" if user else "system", uid=int(user[1]) if user else None
                ),
                method="cgroup_path",
            )
    return unknown
