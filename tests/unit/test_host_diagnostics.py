"""Acceptance cases for scoped host diagnostics, races, and incomplete evidence."""

import asyncio
import json
import shutil
import time
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from hyperion.diagnostics.linux import LinuxDiagnostics, ReadFailure, endpoint_overlap
from hyperion.diagnostics.models import (
    EndpointListenersResult,
    ListenersInput,
    ProcessInput,
    ProcessResult,
    RuntimeInput,
    ServiceRuntimeResult,
)
from hyperion.diagnostics.provider import DiagnosticRequestError, DiagnosticsProvider
from hyperion.models import DiagnosticEndpoint
from hyperion.providers.systemd import SystemdProvider


def codes(result):
    return {i.code for i in result.issues}


def validate(result, model):
    Draft202012Validator(model.model_json_schema()).validate(result.model_dump(mode="json"))


async def test_current_pid_is_separate_from_failed_execution(diagnostics_host):
    host = diagnostics_host
    host.properties.update(
        MainPID="0",
        ExecMainPID="1330",
        ActiveState="activating",
        SubState="auto-restart",
        Result="exit-code",
        ExecMainCode="1",
        ExecMainStatus="1",
        ControlGroup="",
    )
    result = await host.provider.get_service_runtime(host.service, "principal")
    assert result.data.main_pid == 0 and result.data.main_process_ref is None
    assert result.data.last_execution.pid == 1330 and result.data.last_execution.exit_status == 1
    assert result.data.processes == []
    assert result.data.active_state == "activating" and result.data.restart_count == 7
    assert result.collection.atomic is False
    assert result.collection.started_at <= result.collection.completed_at
    assert result.data.last_execution.started_at.isoformat() == "2026-10-06T12:00:00.000001+00:00"
    validate(result, ServiceRuntimeResult)


async def test_listener_child_is_in_same_unit_despite_main_pid_mismatch(diagnostics_host):
    host = diagnostics_host
    host.add_process(22, "/system.slice/t3code.service/workers", inodes=(77,))
    host.add_socket()
    runtime = await host.provider.get_service_runtime(host.service, "principal")
    assert {p.pid for p in runtime.data.processes} == {11, 22}
    assert runtime.data.main_process_ref
    listeners = await host.provider.get_endpoint_listeners(host.service, "principal", "primary")
    process = await host.provider.inspect_process(
        host.service, "principal", listeners.data.listeners[0].owners[0].process_ref
    )
    assert process.data.pid != runtime.data.main_pid
    assert process.data.relationship_to_service.kind == "same_unit"
    assert process.data.relationship_to_service.method == "unit_control_group"
    assert process.data.owner.unit_name == "t3code.service"
    assert process.data.parent.pid == 11 and process.data.parent.identity.process_ref is None
    assert process.data.parent.observation == "current_parent"
    assert process.collection.consistency == "stable"
    validate(process, ProcessResult)


@pytest.mark.parametrize(
    "group,owner,scope",
    [
        ("/system.slice/other.service", "other.service", "system"),
        (
            "/user.slice/user-1000.slice/user@1000.service/app.slice/t3code.service",
            "t3code.service",
            "user",
        ),
    ],
)
async def test_same_application_owned_by_different_launcher(diagnostics_host, group, owner, scope):
    host = diagnostics_host
    host.add_process(33, group, inodes=(77,))
    host.add_socket()
    host.properties.update(MainPID="0", ControlGroup="")
    listeners = await host.provider.get_endpoint_listeners(host.service, "principal", "primary")
    result = await host.provider.inspect_process(
        host.service, "principal", listeners.data.listeners[0].owners[0].process_ref
    )
    assert result.data.owner.unit_name == owner and result.data.owner.manager_scope.kind == scope
    assert result.data.relationship_to_service.kind == "another_owner"
    assert "safe" not in result.model_dump_json()


async def test_shared_socket_has_all_visible_owners(diagnostics_host):
    host = diagnostics_host
    host.add_process(22, "/system.slice/t3code.service", inodes=(77,))
    host.add_process(33, "/system.slice/t3code.service", inodes=(77,))
    host.add_socket()
    result = await host.provider.get_endpoint_listeners(host.service, "principal", "primary")
    assert result.status == "complete"
    assert result.data.coverage.owner_resolution == "complete"
    assert {o.pid for o in result.data.listeners[0].owners} == {22, 33}
    assert result.data.listeners[0].incompatible_bind == "unknown"
    assert result.data.listeners[0].socket_id.endswith("/tcp/77")
    validate(result, EndpointListenersResult)


async def test_unshared_thread_descriptors_are_owners_and_remain_inspectable(diagnostics_host):
    host = diagnostics_host
    host.add_process(22, "/system.slice/other.service")
    host.add_process(33, "/system.slice/t3code.service", inodes=(77,))
    thread_fds = host.root / "22/task/23/fd"
    thread_fds.mkdir(parents=True)
    (thread_fds / "3").symlink_to("socket:[77]")
    host.add_socket()
    result = await host.provider.get_endpoint_listeners(host.service, "principal", "primary")
    assert result.status == "complete"
    assert {o.pid for o in result.data.listeners[0].owners} == {22, 33}
    reference = next(o.process_ref for o in result.data.listeners[0].owners if o.pid == 22)
    inspected = await host.provider.inspect_process(host.service, "principal", reference)
    assert inspected.data.relationship_to_service.kind == "another_owner"


async def test_task_enumeration_limit_preserves_partial_owners(diagnostics_host, monkeypatch):
    host = diagnostics_host
    host.add_process(22, "/system.slice/t3code.service", inodes=(77,))
    host.add_socket()
    monkeypatch.setattr("hyperion.diagnostics.linux.MAX_SCAN_TASKS", 1)
    result = await host.provider.get_endpoint_listeners(host.service, "principal", "primary")
    assert result.status == "partial"
    assert result.data.coverage.truncated
    assert result.data.coverage.owner_resolution != "complete"
    assert "OWNER_ENUMERATION_TRUNCATED" in codes(result)


@pytest.mark.parametrize(
    "requested,observed,overlap",
    [
        ("127.0.0.1", "0.0.0.0", "wildcard"),
        ("127.0.0.1", "::", "potential_ipv6"),
        ("127.0.0.1", "::ffff:127.0.0.1", "potential_ipv6"),
        ("127.0.0.1", "::1", None),
        ("127.0.0.1", "127.0.0.2", None),
        ("::1", "::", "wildcard"),
        ("::", "127.0.0.1", "potential_ipv6"),
    ],
)
def test_wildcard_and_ipv6_address_semantics(requested, observed, overlap):
    endpoint = DiagnosticEndpoint(namespace="host", protocol="tcp", address=requested, port=3773)
    assert endpoint_overlap(endpoint, observed) == overlap


async def test_socket_tables_include_only_endpoint_overlaps(diagnostics_host):
    host = diagnostics_host
    host.add_socket("0.0.0.0", 77)
    host.add_socket("::", 78)
    host.add_socket("::1", 79)
    host.add_socket("127.0.0.1", 80, 3774)
    host.add_process(22, "/system.slice/t3code.service", inodes=(77, 78, 79, 80))
    result = await host.provider.get_endpoint_listeners(host.service, "principal", "primary")
    assert {s.inode for s in result.data.listeners} == {77, 78}


async def test_permission_denied_owners_never_look_like_ownerless_socket(
    diagnostics_host, monkeypatch
):
    host = diagnostics_host
    host.add_socket()
    original = host.linux.process

    def denied(pid):
        if pid == 11:
            raise PermissionError()
        return original(pid)

    monkeypatch.setattr(host.linux, "process", denied)
    result = await host.provider.get_endpoint_listeners(host.service, "principal", "primary")
    assert result.status == "partial"
    assert result.data.listeners[0].owners is None
    assert result.data.coverage.socket_enumeration == "complete"
    assert result.data.coverage.owner_resolution == "unavailable"
    assert "OWNER_PERMISSION_DENIED" in codes(result)


async def test_empty_complete_and_unavailable_are_different(diagnostics_host, monkeypatch):
    host = diagnostics_host
    empty = await host.provider.get_endpoint_listeners(host.service, "principal", "primary")
    assert empty.status == "complete" and empty.data.listeners == []

    def unavailable(*args):
        raise PermissionError()

    monkeypatch.setattr(host.linux, "listeners", unavailable)
    missing = await host.provider.get_endpoint_listeners(host.service, "principal", "primary")
    assert missing.status == "unavailable" and missing.data is None and missing.issues


@pytest.mark.parametrize("change", ["exited", "reused", "boot"])
async def test_references_validate_the_particular_instance(diagnostics_host, change):
    host = diagnostics_host
    runtime = await host.provider.get_service_runtime(host.service, "principal")
    reference = runtime.data.main_process_ref
    if change == "exited":
        shutil.rmtree(host.root / "11")
    elif change == "reused":
        host.add_process(11, "/system.slice/t3code.service", ticks=200)
    else:
        (host.root / "sys/kernel/random/boot_id").write_text("22222222-2222-4222-8222-222222222222")
    result = await host.provider.inspect_process(host.service, "principal", reference)
    assert result.status == "unavailable" and result.data is None
    assert result.collection.consistency == "changed"
    assert codes(result) & {"PROCESS_EXITED", "PROCESS_IDENTITY_CHANGED"}


async def test_identity_reuse_during_metadata_does_not_mix_instances(diagnostics_host, monkeypatch):
    host = diagnostics_host
    reference = (
        await host.provider.get_service_runtime(host.service, "principal")
    ).data.main_process_ref
    original = host.linux.command_summary

    def reused(pid):
        result = original(pid)
        host.add_process(pid, "/system.slice/t3code.service", ticks=999)
        return result

    monkeypatch.setattr(host.linux, "command_summary", reused)
    result = await host.provider.inspect_process(host.service, "principal", reference)
    assert result.data is None and "PROCESS_IDENTITY_CHANGED" in codes(result)


async def test_reference_is_scoped_expires_and_does_not_grant_parent_inspection(diagnostics_host):
    host = diagnostics_host
    reference = (
        await host.provider.get_service_runtime(host.service, "principal")
    ).data.main_process_ref
    with pytest.raises(DiagnosticRequestError, match="OUT_OF_SCOPE"):
        await host.provider.inspect_process(host.service, "another", reference)
    other = host.service.model_copy(update={"service_id": "other"})
    with pytest.raises(DiagnosticRequestError, match="OUT_OF_SCOPE"):
        await host.provider.inspect_process(other, "principal", reference)
    host.provider._grants[reference] = replace(host.provider._grants[reference], expires=0)
    with pytest.raises(DiagnosticRequestError, match="EXPIRED"):
        await host.provider.inspect_process(host.service, "principal", reference)


async def test_process_moving_out_of_service_scope_is_not_inspectable(diagnostics_host):
    host = diagnostics_host
    reference = (
        await host.provider.get_service_runtime(host.service, "principal")
    ).data.main_process_ref
    host.properties["MainPID"] = "0"
    (host.root / "11/cgroup").write_text("0::/system.slice/other.service\n")
    result = await host.provider.inspect_process(host.service, "principal", reference)
    assert result.data is None and "PROCESS_SCOPE_CHANGED" in codes(result)


async def test_manager_changes_and_fresh_samples_are_explicit(diagnostics_host):
    host = diagnostics_host

    class ChangingManager:
        calls = 0

        async def runtime_properties(self, unit):
            self.calls += 1
            result = dict(host.properties)
            result["NRestarts"] = str(self.calls)
            return result

    host.provider.systemd = ChangingManager()
    first = await host.provider.get_service_runtime(host.service, "principal")
    second = await host.provider.get_service_runtime(host.service, "principal")
    assert first.collection.consistency == "changed" and "UNIT_STATE_CHANGED" in codes(first)
    assert second.data.restart_count > first.data.restart_count
    assert second.snapshot_id != first.snapshot_id
    assert second.collection.started_at >= first.collection.completed_at
    assert first.data.main_process_ref is None


async def test_partial_visibility_and_list_limits(diagnostics_host, monkeypatch):
    host = diagnostics_host
    (host.root / "mounts").write_text("proc /proc proc rw,hidepid=2 0 0\n")
    for pid in range(20, 55):
        host.add_process(pid, "/system.slice/t3code.service", inodes=(77,))
    host.add_socket()
    runtime = await host.provider.get_service_runtime(host.service, "principal")
    assert runtime.status == "partial" and len(runtime.data.processes) == 24
    assert runtime.data.coverage.truncated
    listeners = await host.provider.get_endpoint_listeners(host.service, "principal", "primary")
    assert listeners.status == "partial" and len(listeners.data.listeners[0].owners) == 12
    assert listeners.data.coverage.truncated and "OWNER_VISIBILITY_RESTRICTED" in codes(listeners)
    validate(listeners, EndpointListenersResult)


async def test_command_disclosure_omits_secrets_and_environment(diagnostics_host):
    host = diagnostics_host
    host.add_process(
        11,
        "/system.slice/t3code.service",
        command=b"node\0/srv/app/server.js\0--token\0SECRET\0password=PRIVATE\0",
    )
    (host.root / "11/environ").write_bytes(b"API_KEY=ENV_SECRET")
    runtime = await host.provider.get_service_runtime(host.service, "principal")
    result = await host.provider.inspect_process(
        host.service, "principal", runtime.data.main_process_ref
    )
    assert result.data.command_summary.script_name == "server.js"
    assert not result.data.command_summary.arguments_disclosed
    assert all(
        secret not in result.model_dump_json() for secret in ("SECRET", "PRIVATE", "ENV_SECRET")
    )


async def test_five_second_budget_and_bounded_serialization(diagnostics_host, monkeypatch):
    host = diagnostics_host

    async def slow(argv):
        await asyncio.sleep(1)
        return 0, b"", b""

    host.provider.systemd = SystemdProvider(slow)
    monkeypatch.setattr("hyperion.diagnostics.provider.COLLECTION_SECONDS", 0.02)
    start = time.monotonic()
    result = await host.provider.get_service_runtime(host.service, "principal")
    assert time.monotonic() - start < 0.2
    assert result.status == "unavailable" and "COLLECTION_TIMEOUT" in codes(result)


async def test_result_budget_reduces_coverage_without_corrupting_schema(
    diagnostics_host, monkeypatch
):
    host = diagnostics_host
    for pid in range(20, 40):
        host.add_process(pid, "/system.slice/t3code.service")
    monkeypatch.setattr("hyperion.diagnostics.provider.MAX_RESULT_CHARACTERS", 3000)
    result = await host.provider.get_service_runtime(host.service, "principal")
    assert len(json.dumps(result.model_dump(mode="json"), ensure_ascii=False)) <= 3000
    assert result.status == "partial" and result.data.coverage.truncated
    assert "RESULT_TRUNCATED" in codes(result)
    validate(result, ServiceRuntimeResult)


@pytest.mark.parametrize(
    "model,args",
    [
        (RuntimeInput, {"service": "t3-code", "unit": "ssh.service"}),
        (RuntimeInput, {"service": 11}),
        (ListenersInput, {"service": "t3-code", "endpoint_id": "primary", "port": 22}),
        (ProcessInput, {"service": "t3-code", "process_ref": "1330"}),
    ],
)
def test_only_typed_resources_are_accepted(model, args):
    with pytest.raises(ValidationError):
        model.model_validate(args)


async def test_out_of_scope_endpoint_is_an_input_error(diagnostics_host):
    host = diagnostics_host
    with pytest.raises(DiagnosticRequestError):
        await host.provider.get_endpoint_listeners(host.service, "principal", "other")


async def test_fixture_provider_never_reads_live_host(diagnostics_host, monkeypatch):
    def forbidden(*args):
        raise AssertionError("Live host read in fixture mode")

    monkeypatch.setattr(LinuxDiagnostics, "scope", forbidden)
    result = await DiagnosticsProvider(enabled=False).get_service_runtime(
        diagnostics_host.service, "principal"
    )
    assert result.status == "unavailable" and "DIAGNOSTICS_DISABLED" in codes(result)


async def test_manager_output_is_bounded_and_fixed(diagnostics_host):
    async def oversized(argv):
        assert argv[1] == "show" and len(argv) == 6
        return 0, b"x" * 65537, b""

    with pytest.raises(ReadFailure, match="OUTPUT_LIMIT"):
        await SystemdProvider(oversized).runtime_properties("t3code.service")
    with pytest.raises(ValueError):
        await SystemdProvider(oversized).runtime_properties("--no-block")


async def test_missing_manager_scope_is_unavailable_not_a_scope_change(
    diagnostics_host, monkeypatch
):
    host = diagnostics_host
    reference = (
        await host.provider.get_service_runtime(host.service, "principal")
    ).data.main_process_ref

    async def unavailable(unit):
        raise ReadFailure("MANAGER_UNAVAILABLE")

    monkeypatch.setattr(host.provider.systemd, "runtime_properties", unavailable)
    result = await host.provider.inspect_process(host.service, "principal", reference)
    assert result.status == "unavailable" and result.collection.consistency == "unknown"
    assert "PROCESS_SCOPE_UNAVAILABLE" in codes(result)
    assert "PROCESS_SCOPE_CHANGED" not in codes(result)


async def test_missing_endpoint_reads_do_not_claim_listener_stopped(diagnostics_host, monkeypatch):
    from hyperion.diagnostics.linux import SocketScan, issue

    host = diagnostics_host
    host.add_process(22, "/system.slice/t3code.service", inodes=(77,))
    host.add_socket()
    listeners = await host.provider.get_endpoint_listeners(host.service, "principal", "primary")
    reference = listeners.data.listeners[0].owners[0].process_ref
    monkeypatch.setattr(
        host.linux,
        "listeners",
        lambda *args: SocketScan(
            issues=[issue("SOCKET_ENUMERATION_PERMISSION_DENIED", "listeners")]
        ),
    )
    result = await host.provider.inspect_process(host.service, "principal", reference)
    assert result.status == "unavailable" and result.collection.consistency == "unknown"
    assert "PROCESS_SCOPE_CHANGED" not in codes(result)


async def test_last_identity_check_runs_after_metadata_rechecks(diagnostics_host, monkeypatch):
    host = diagnostics_host
    reference = (
        await host.provider.get_service_runtime(host.service, "principal")
    ).data.main_process_ref
    original = host.linux.path_metadata
    reads = 0

    def reused(pid, name):
        nonlocal reads
        if name == "cwd":
            reads += 1
            if reads == 2:
                host.add_process(pid, "/system.slice/t3code.service", ticks=999)
        return original(pid, name)

    monkeypatch.setattr(host.linux, "path_metadata", reused)
    result = await host.provider.inspect_process(host.service, "principal", reference)
    assert result.data is None and "PROCESS_IDENTITY_CHANGED" in codes(result)


async def test_exec_metadata_change_is_reported_without_replacing_process_identity(
    diagnostics_host, monkeypatch
):
    host = diagnostics_host
    reference = (
        await host.provider.get_service_runtime(host.service, "principal")
    ).data.main_process_ref
    original = host.linux.path_metadata
    reads = 0

    def changed(pid, name):
        nonlocal reads
        if name == "cwd":
            reads += 1
            if reads == 2:
                return "/srv/another-checkout"
        return original(pid, name)

    monkeypatch.setattr(host.linux, "path_metadata", changed)
    result = await host.provider.inspect_process(host.service, "principal", reference)
    assert result.status == "partial" and result.collection.consistency == "changed"
    assert result.data.process_ref == reference
    assert "PROCESS_METADATA_CHANGED" in codes(result)


async def test_host_network_container_process_view_is_rejected(diagnostics_host):
    host = diagnostics_host
    host.add_process(1, "/", name="node", parent=0)
    result = await host.provider.get_endpoint_listeners(host.service, "principal", "primary")
    assert result.status == "unavailable" and "HOST_SCOPE_UNAVAILABLE" in codes(result)


async def test_reference_store_is_bounded(diagnostics_host, monkeypatch):
    host = diagnostics_host
    monkeypatch.setattr("hyperion.diagnostics.provider.MAX_REFERENCES", 2)
    first = (
        await host.provider.get_service_runtime(host.service, "principal")
    ).data.main_process_ref
    await host.provider.get_service_runtime(host.service, "principal")
    await host.provider.get_service_runtime(host.service, "principal")
    assert len(host.provider._grants) == 2
    with pytest.raises(DiagnosticRequestError, match="OUT_OF_SCOPE"):
        await host.provider.inspect_process(host.service, "principal", first)


async def test_selected_metadata_uses_the_shared_redaction_policy(diagnostics_host):
    host = diagnostics_host
    host.add_process(
        11,
        "/system.slice/t3code.service",
        name="Bearer secret123",
        command=b"node\0/srv/Bearer secret123.js\0",
    )
    runtime = await host.provider.get_service_runtime(host.service, "principal")
    result = await host.provider.inspect_process(
        host.service, "principal", runtime.data.main_process_ref
    )
    assert "secret123" not in result.model_dump_json()
    assert "[REDACTED]" in result.data.name


async def test_controller_membership_does_not_override_systemd_ownership(diagnostics_host):
    host = diagnostics_host
    host.add_process(22, "/system.slice/other.service", inodes=(77,))
    (host.root / "22/cgroup").write_text(
        "2:cpu,cpuacct:/system.slice/t3code.service\n1:name=systemd:/system.slice/other.service\n"
    )
    host.add_socket()
    runtime = await host.provider.get_service_runtime(host.service, "principal")
    assert {p.pid for p in runtime.data.processes} == {11}
    listeners = await host.provider.get_endpoint_listeners(host.service, "principal", "primary")
    result = await host.provider.inspect_process(
        host.service, "principal", listeners.data.listeners[0].owners[0].process_ref
    )
    assert result.data.owner.unit_name == "other.service"
    assert result.data.relationship_to_service.kind == "another_owner"
    assert result.data.control_groups[0].controllers == ["cpu", "cpuacct"]
    assert result.data.control_groups[1].controllers == ["name=systemd"]
    validate(result, ProcessResult)


async def test_missing_numeric_properties_and_invalid_timestamp_preserve_other_facts(
    diagnostics_host,
):
    host = diagnostics_host
    host.properties.update(NRestarts="n/a", ExecMainStartTimestamp="18446744073709551615")
    result = await host.provider.get_service_runtime(host.service, "principal")
    assert result.status == "partial" and result.data.active_state == "active"
    assert result.data.restart_count is None and result.data.last_execution.started_at is None
    assert any(i.fields == ["restart_count"] for i in result.issues)
    validate(result, ServiceRuntimeResult)


async def test_container_child_with_missing_current_group_is_not_declared_another_owner(
    diagnostics_host,
):
    host = diagnostics_host
    host.add_process(22, "/system.slice/t3code.service/docker-" + "a" * 64 + ".scope", inodes=(77,))
    host.add_socket()
    host.properties.update(MainPID="0", ControlGroup="")
    listeners = await host.provider.get_endpoint_listeners(host.service, "principal", "primary")
    result = await host.provider.inspect_process(
        host.service, "principal", listeners.data.listeners[0].owners[0].process_ref
    )
    assert result.data.owner.kind == "container"
    assert result.data.relationship_to_service.kind == "unknown"
    assert "PROCESS_RELATIONSHIP_UNKNOWN" in codes(result)
