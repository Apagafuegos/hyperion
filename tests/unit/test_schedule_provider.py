"""Schedule provider: systemd timer JSON and cron file normalization."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from hyperion.providers.schedules import ScheduleProvider

_NOW = datetime(2026, 8, 9, 9, 0, 0, tzinfo=UTC)

TIMER_JSON = json.dumps([
    {
        "unit": "backup.timer",
        "activates": "backup.service",
        "onCalendar": "*-*-* 02:00:00",
        "nextElapseUSecRealtime": "1799800000000000",
        "lastTriggerUSecRealtime": "1789900000000000",
        "lastResult": "success",
        "owner": "root",
        "unitFileState": "enabled",
    }
])


async def _runner(argv: list[str]):
    if "list-timers" in argv:
        return 0, TIMER_JSON.encode("utf-8"), b""
    return 1, b"", b"unexpected command"


def test_systemd_timers_normalize(tmp_path: Path) -> None:
    import asyncio

    provider = ScheduleProvider(
        executor=_runner,
        related={"backup.service": "backup"},
        cron_root=tmp_path / "no-cron",
    )
    inventory = asyncio.run(provider.inventory())
    assert len(inventory.schedules) == 1
    schedule = inventory.schedules[0]
    assert schedule.source == "systemd"
    assert schedule.target == "backup.service"
    assert schedule.related_service == "backup"
    assert schedule.enabled is True
    assert schedule.last_result == "success"


def test_cron_file_parsing(tmp_path: Path) -> None:
    import asyncio

    cron_dir = tmp_path / "etc" / "cron.d"
    cron_dir.mkdir(parents=True)
    (cron_dir / "cleanup").write_text(
        "*/15 * * * * root /usr/local/bin/cleanup-logs\n"
        "# comment line\n"
        "0 6 * * * root /usr/bin/apt-daily\n",
        encoding="utf-8",
    )
    provider = ScheduleProvider(cron_root=tmp_path)
    inventory = asyncio.run(provider.inventory())
    schedules = [s for s in inventory.schedules if s.source == "cron"]
    assert len(schedules) == 2
    by_name = {s.name: s for s in schedules}
    assert "cleanup-logs" in by_name
    assert by_name["cleanup-logs"].owner == "root"
    assert by_name["cleanup-logs"].raw_expression == "*/15 * * * *"
    assert by_name["cleanup-logs"].last_result == "not_observed"
    assert by_name["cleanup-logs"].next_run is not None


def test_cron_unreadable_files_are_skipped(tmp_path: Path) -> None:
    import asyncio

    cron_dir = tmp_path / "etc" / "cron.d"
    cron_dir.mkdir(parents=True)
    (cron_dir / "cleanup").write_text(
        "0 6 * * * root /bin/true\n", encoding="utf-8"
    )
    provider = ScheduleProvider(cron_root=tmp_path / "missing-root")
    inventory = asyncio.run(provider.inventory())
    assert all(s.source != "cron" for s in inventory.schedules)


def test_invalid_cron_lines_are_ignored(tmp_path: Path) -> None:
    import asyncio

    cron_dir = tmp_path / "etc" / "cron.d"
    cron_dir.mkdir(parents=True)
    (cron_dir / "messy").write_text(
        "not a cron line\n"
        "60 * * * * root /bin/false\n"  # invalid minute
        "* * * * * root /bin/true\n",
        encoding="utf-8",
    )
    provider = ScheduleProvider(cron_root=tmp_path)
    inventory = asyncio.run(provider.inventory())
    cron = [s for s in inventory.schedules if s.source == "cron"]
    assert len(cron) == 1
    assert cron[0].raw_expression == "* * * * *"


def test_provenance_carries_path_and_line(tmp_path: Path) -> None:
    import asyncio

    cron_dir = tmp_path / "etc" / "cron.d"
    cron_dir.mkdir(parents=True)
    (cron_dir / "job").write_text(
        "0 6 * * * root /bin/true\n", encoding="utf-8"
    )
    provider = ScheduleProvider(cron_root=tmp_path)
    inventory = asyncio.run(provider.inventory())
    cron = next(s for s in inventory.schedules if s.source == "cron")
    assert cron.provenance == f"{cron_dir / 'job'}:1"
