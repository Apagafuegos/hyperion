"""Systemd unit inventory provider and protected-unit classification."""

from __future__ import annotations

import json

from hyperion.providers.systemd_units import PROTECTED_UNIT_PATTERNS, SystemdUnitProvider

LIST_JSON = json.dumps([
    {"unit": "t3code.service"},
    {"unit": "docker.service"},
    {"unit": "backup.service"},
])

SHOW_PROPS = {
    "t3code.service": (
        "Names=t3code.service\n"
        "Description=T3 Code service\n"
        "LoadState=loaded\n"
        "ActiveState=active\n"
        "SubState=running\n"
        "UnitFileState=enabled\n"
        "ActiveEnterTimestamp=1789800000000000\n"
        "MainPID=1234\n"
        "NRestarts=0\n"
        "MemoryCurrent=155189248\n"
        "TriggeredBy=\n"
    ),
    "docker.service": (
        "Names=docker.service\n"
        "Description=Docker Application Container Engine\n"
        "LoadState=loaded\n"
        "ActiveState=active\n"
        "SubState=running\n"
        "UnitFileState=enabled\n"
        "ActiveEnterTimestamp=1789000000000000\n"
        "MainPID=890\n"
        "NRestarts=0\n"
        "MemoryCurrent=205783040\n"
        "TriggeredBy=\n"
    ),
    "backup.service": (
        "Names=backup.service\n"
        "Description=Nightly backup\n"
        "LoadState=loaded\n"
        "ActiveState=inactive\n"
        "SubState=dead\n"
        "UnitFileState=static\n"
        "ActiveEnterTimestamp=\n"
        "MainPID=0\n"
        "NRestarts=0\n"
        "MemoryCurrent=\n"
        "TriggeredBy=backup.timer\n"
    ),
}


async def _runner(argv: list[str]):
    if "list-units" in argv:
        return 0, LIST_JSON.encode("utf-8"), b""
    unit = argv[2]
    return 0, SHOW_PROPS[unit].encode("utf-8"), b""


def test_inventory_enumerates_and_classifies() -> None:
    import asyncio

    provider = SystemdUnitProvider(executor=_runner)
    inventory = asyncio.run(provider.inventory(related={"t3code.service": "t3-code"}))
    assert len(inventory.units) == 3
    by_name = {u.name: u for u in inventory.units}

    t3code = by_name["t3code.service"]
    assert t3code.active_state == "active"
    assert t3code.enabled_state == "enabled"
    assert t3code.related_service == "t3-code"
    assert t3code.curated is True
    assert t3code.main_pid == 1234

    docker = by_name["docker.service"]
    assert docker.protection == "protected"

    backup = by_name["backup.service"]
    assert backup.active_state == "inactive"
    assert backup.related_timer == "backup.timer"
    assert backup.main_pid is None


def test_protected_unit_patterns() -> None:
    protected = [
        "ssh.service", "sshd.service", "ssh.socket",
        "systemd-networkd.service", "NetworkManager.service", "networking.service",
        "network.service", "docker.service", "caddy.service",
        "authentik-server.service", "authentik-worker.service",
        "hyperion.service",
    ]
    ordinary = [
        "t3code.service", "backup.service", "nginx.service", "postgres.service",
    ]
    for name in protected:
        assert any(pattern.fullmatch(name) for pattern in PROTECTED_UNIT_PATTERNS), name
    for name in ordinary:
        assert not any(pattern.fullmatch(name) for pattern in PROTECTED_UNIT_PATTERNS), name


def test_read_logs_parses_journal_json() -> None:
    import asyncio

    lines = [
        json.dumps(
            {"__REALTIME_TIMESTAMP": "1789900000000000", "PRIORITY": "6", "MESSAGE": "started"}
        ),
        json.dumps(
            {"__REALTIME_TIMESTAMP": "1789900000001000", "PRIORITY": "3", "MESSAGE": "boom"}
        ),
    ]

    async def runner(argv: list[str]):
        return 0, ("\n".join(lines) + "\n").encode("utf-8"), b""

    provider = SystemdUnitProvider(executor=runner)
    records = asyncio.run(provider.read_logs("t3code.service", 100))
    assert len(records) == 2
    assert records[0]["severity"] == "info"
    assert records[1]["severity"] == "error"


def test_inventory_handles_provider_failure_gracefully() -> None:
    import asyncio

    async def runner(argv: list[str]):
        raise RuntimeError("systemctl unavailable")

    provider = SystemdUnitProvider(executor=runner)
    inventory = asyncio.run(provider.inventory())
    assert inventory.fresh is False
    assert inventory.units == []
