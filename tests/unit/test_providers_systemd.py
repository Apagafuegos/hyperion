"""Systemd provider: fixed argv subprocesses, exact unit names only."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from hyperion.catalog import load_catalog
from hyperion.providers.base import LogBinding
from hyperion.providers.systemd import SystemdProvider

FIXTURES = Path(__file__).parents[1] / "fixtures"

ACTIVE_SHOW = (
    "ActiveState=active\n"
    "SubState=running\n"
    "MainPID=1234\n"
    "ExecMainStartTimestampMonotonic=432000000000\n"
    "NRestarts=2\n"
    "LoadState=loaded\n"
)


class FakeExecutor:
    def __init__(self, responses: dict[str, tuple[int, bytes, bytes]]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    async def run(self, argv: list[str]) -> tuple[int, bytes, bytes]:
        self.calls.append(argv)
        for needle, response in self.responses.items():
            if needle in argv:
                return response
        return (0, b"", b"")


def test_show_uses_fixed_argv() -> None:
    executor = FakeExecutor({"systemctl": (0, ACTIVE_SHOW.encode(), b"")})
    provider = SystemdProvider(executor=executor.run)
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    observations = asyncio.run(provider.observe(catalog))
    assert observations.state == "available"
    t3 = next(c for c in observations.components if c.selector == "t3code.service")
    assert t3.state == "running"
    assert t3.health == "healthy"
    assert t3.restart_count == 2
    systemctl_call = next(call for call in executor.calls if call[0] == "systemctl")
    assert "t3code.service" in systemctl_call
    assert "--no-pager" in systemctl_call
    assert (
        "--property=ActiveState,SubState,MainPID,"
        "ExecMainStartTimestampMonotonic,NRestarts,LoadState"
        in systemctl_call
    )


def test_observes_every_declared_systemd_unit() -> None:
    executor = FakeExecutor({"systemctl": (0, ACTIVE_SHOW.encode(), b"")})
    provider = SystemdProvider(executor=executor.run)
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    observations = asyncio.run(provider.observe(catalog))
    systemctl_calls = [call for call in executor.calls if call[0] == "systemctl"]
    assert len(systemctl_calls) == 2
    units = {call[2] for call in systemctl_calls}
    assert units == {"t3code.service", "demoworker.service"}
    worker = next(c for c in observations.components if c.selector == "demoworker.service")
    assert worker.service_id == "demo-worker"
    assert worker.reference == "demoworker.service"


def test_not_found_unit_is_missing() -> None:
    executor = FakeExecutor(
        {"systemctl": (0, b"LoadState=not-found\nActiveState=inactive\n", b"")}
    )
    provider = SystemdProvider(executor=executor.run)
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    observations = asyncio.run(provider.observe(catalog))
    t3 = next(c for c in observations.components if c.selector == "t3code.service")
    assert t3.state == "missing"
    assert t3.health == "unknown"


def test_failed_unit_maps_to_stopped_unhealthy() -> None:
    executor = FakeExecutor(
        {
            "systemctl": (
                0,
                b"ActiveState=failed\nSubState=failed\nNRestarts=5\nLoadState=loaded\n",
                b"",
            )
        }
    )
    provider = SystemdProvider(executor=executor.run)
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    observations = asyncio.run(provider.observe(catalog))
    t3 = next(c for c in observations.components if c.selector == "t3code.service")
    assert t3.state == "stopped"
    assert t3.health == "unhealthy"
    assert t3.restart_count == 5


def test_activating_unit_maps_to_starting() -> None:
    executor = FakeExecutor(
        {
            "systemctl": (
                0,
                b"ActiveState=activating\nSubState=start\nNRestarts=0\nLoadState=loaded\n",
                b"",
            )
        }
    )
    provider = SystemdProvider(executor=executor.run)
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    observations = asyncio.run(provider.observe(catalog))
    t3 = next(c for c in observations.components if c.selector == "t3code.service")
    assert t3.state == "starting"
    assert t3.health == "starting"


def test_inactive_unit_maps_to_stopped_unconfigured() -> None:
    executor = FakeExecutor(
        {
            "systemctl": (
                0,
                b"ActiveState=inactive\nSubState=dead\nNRestarts=0\nLoadState=loaded\n",
                b"",
            )
        }
    )
    provider = SystemdProvider(executor=executor.run)
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    observations = asyncio.run(provider.observe(catalog))
    t3 = next(c for c in observations.components if c.selector == "t3code.service")
    assert t3.state == "stopped"
    assert t3.health == "unconfigured"


def test_non_numeric_nrestarts_is_none() -> None:
    executor = FakeExecutor(
        {"systemctl": (0, b"ActiveState=active\nNRestarts=NaN\nLoadState=loaded\n", b"")}
    )
    provider = SystemdProvider(executor=executor.run)
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    observations = asyncio.run(provider.observe(catalog))
    t3 = next(c for c in observations.components if c.selector == "t3code.service")
    assert t3.state == "running"
    assert t3.restart_count is None


def test_provider_failure_is_unavailable() -> None:
    async def failing(argv: list[str]) -> tuple[int, bytes, bytes]:
        raise OSError("journald missing")

    provider = SystemdProvider(executor=failing)
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    observations = asyncio.run(provider.observe(catalog))
    assert observations.state == "unavailable"
    assert observations.error is not None


def test_journal_reads_parsed_json() -> None:
    journal_line = (
        '{"__REALTIME_TIMESTAMP":"1783000000000000","PRIORITY":"6","MESSAGE":"hello from t3",'
        '"SYSLOG_IDENTIFIER":"t3coded"}'
    )
    executor = FakeExecutor({"journalctl": (0, (journal_line + "\n").encode(), b"")})
    provider = SystemdProvider(executor=executor.run)
    binding = LogBinding(
        provider="systemd",
        service_id="t3-code",
        source="t3code.service",
        reference="t3code.service",
    )
    records = asyncio.run(provider.read_logs(binding, tail=10, before=None))
    assert len(records) == 1
    assert records[0].message == "hello from t3"
    assert records[0].severity == "info"
    assert records[0].stream == "journal"
    call = next(call for call in executor.calls if call[0] == "journalctl")
    assert "--unit" in call and "t3code.service" in call
    assert "--output" in call and "json" in call


def test_journal_until_from_validated_timestamp() -> None:
    executor = FakeExecutor({"journalctl": (0, b"", b"")})
    provider = SystemdProvider(executor=executor.run)
    binding = LogBinding(
        provider="systemd",
        service_id="t3-code",
        source="t3code.service",
        reference="t3code.service",
    )
    before = datetime(2026, 8, 4, 6, 0, 0, tzinfo=UTC)
    asyncio.run(provider.read_logs(binding, tail=10, before=before))
    call = next(call for call in executor.calls if call[0] == "journalctl")
    assert "--until" in call
    assert call[call.index("--until") + 1].startswith("2026-08-04")


def test_journal_missing_priority_has_no_severity() -> None:
    journal_line = '{"__REALTIME_TIMESTAMP":"1783000000000000","MESSAGE":"no priority"}'
    executor = FakeExecutor({"journalctl": (0, (journal_line + "\n").encode(), b"")})
    provider = SystemdProvider(executor=executor.run)
    binding = LogBinding(
        provider="systemd",
        service_id="t3-code",
        source="t3code.service",
        reference="t3code.service",
    )
    records = asyncio.run(provider.read_logs(binding, tail=10, before=None))
    assert len(records) == 1
    assert records[0].severity is None


def test_journal_bad_json_line_is_skipped() -> None:
    executor = FakeExecutor({"journalctl": (0, b"not json at all\n", b"")})
    provider = SystemdProvider(executor=executor.run)
    binding = LogBinding(
        provider="systemd",
        service_id="t3-code",
        source="t3code.service",
        reference="t3code.service",
    )
    records = asyncio.run(provider.read_logs(binding, tail=10, before=None))
    assert records == []


def test_journal_message_trailing_newline_is_rstripped() -> None:
    journal_line = (
        '{"__REALTIME_TIMESTAMP":"1783000000000000","PRIORITY":"6",'
        '"MESSAGE":"trail\\n\\n"}'
    )
    executor = FakeExecutor({"journalctl": (0, (journal_line + "\n").encode(), b"")})
    provider = SystemdProvider(executor=executor.run)
    binding = LogBinding(
        provider="systemd",
        service_id="t3-code",
        source="t3code.service",
        reference="t3code.service",
    )
    records = asyncio.run(provider.read_logs(binding, tail=10, before=None))
    assert records[0].message == "trail"
