"""Managed schedules: validation, privileged writes, revision control, rollback.

The manager never touches systemctl or systemd paths directly; every privileged
action crosses the ops-helper boundary via `request`. Tests use a recording
helper that performs write/remove against a real temp directory so file-level
behavior and rollback remain observable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hyperion.models import ManagedScheduleDefinition
from hyperion.services.activity import ActivityStore
from hyperion.services.managed_schedules import ManagedScheduleError, ManagedScheduleManager


class RecordingHelper:
    """Helper double: records requests and mirrors write/remove on a temp dir."""

    def __init__(self, unit_dir: Path, fail_daemon_reload: bool = False) -> None:
        self.unit_dir = Path(unit_dir)
        self.fail_daemon_reload = fail_daemon_reload
        self.requests: list[tuple[str, str, str | None]] = []

    async def request(
        self, unit: str, operation: str, content: str | None = None
    ) -> dict[str, object]:
        self.requests.append((unit, operation, content))
        if operation == "write-unit":
            path = self.unit_dir / unit
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content or "", encoding="utf-8")
        elif operation == "remove-unit":
            (self.unit_dir / unit).unlink(missing_ok=True)
        elif operation == "daemon-reload" and self.fail_daemon_reload:
            return {"ok": False, "error": "reload failed", "returncode": 1}
        return {"ok": True, "returncode": 0}


class BrokenHelper:
    """Helper whose socket is unreachable."""

    async def request(
        self, unit: str, operation: str, content: str | None = None
    ) -> dict[str, object]:
        raise ConnectionError("no ops socket")


def _definition(**overrides) -> ManagedScheduleDefinition:
    values = dict(
        name="daily-backup",
        description="Daily backup",
        on_calendar="*-*-* 02:00:00",
        executable="/usr/local/bin/backup",
        arguments=[],
        user="root",
        working_directory="/srv/backups",
        timeout_seconds=3600,
        overlap_policy="drop",
        missed_run_behavior="ignore",
        related_service=None,
    )
    values.update(overrides)
    return ManagedScheduleDefinition.model_validate(values)


def _manager(tmp_path: Path) -> ManagedScheduleManager:
    activity = ActivityStore(tmp_path / "activity.db")
    helper = RecordingHelper(tmp_path / "systemd")
    return ManagedScheduleManager(
        tmp_path / "state",
        activity,
        helper=helper,
        managed_unit_dir=tmp_path / "systemd",
    )


async def test_create_writes_service_and_timer(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    view = await manager.create(_definition(), "owner")
    assert view.installed is True
    assert view.revision == 0
    service = manager._unit_dir() / "hyperion-daily-backup.service"
    timer = manager._unit_dir() / "hyperion-daily-backup.timer"
    assert service.exists()
    assert timer.exists()
    service_text = service.read_text(encoding="utf-8")
    assert "ExecStart=/usr/local/bin/backup" in service_text
    assert "User=root" in service_text
    timer_text = timer.read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 02:00:00" in timer_text
    written = [u for (u, op, _) in manager._helper.requests if op == "write-unit"]
    assert written == ["hyperion-daily-backup.service", "hyperion-daily-backup.timer"]
    operations = [op for (_, op, _) in manager._helper.requests]
    assert operations[-1] == "enable-now"


async def test_update_requires_matching_revision(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    await manager.create(_definition(), "owner")
    with pytest.raises(ManagedScheduleError, match="stale revision"):
        await manager.update("daily-backup", _definition(description="changed"), 3, "owner")
    view = await manager.update("daily-backup", _definition(description="changed"), 0, "owner")
    assert view.revision == 1


def test_invalid_name_rejected() -> None:
    # The model's name pattern is the first line of defense.
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        _definition(name="../../etc/passwd")
    with pytest.raises(pydantic.ValidationError):
        _definition(name="bad name")


async def test_invalid_calendar_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with pytest.raises(ManagedScheduleError):
        await manager.create(_definition(on_calendar="/etc/hosts"), "owner")
    # The on_calendar field is a plain string in the model, so path/command
    # fragments must be caught by the manager's own validation.
    definition = ManagedScheduleDefinition.model_validate(
        {**_definition().model_dump(), "on_calendar": "rm -rf /; poweroff"}
    )
    with pytest.raises(ManagedScheduleError):
        await manager.create(definition, "owner")


def test_executable_must_be_absolute() -> None:
    # The model enforces the absolute-path pattern.
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        _definition(executable="backup")


async def test_arguments_with_newlines_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with pytest.raises(ManagedScheduleError):
        await manager.create(_definition(arguments=["--config\n--evil"]), "owner")


async def test_delete_removes_units(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    await manager.create(_definition(), "owner")
    await manager.delete("daily-backup", 0, "owner")
    assert manager.get("daily-backup") is None
    assert not (manager._unit_dir() / "hyperion-daily-backup.service").exists()


async def test_write_failure_rolls_back_to_previous(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    await manager.create(_definition(), "owner")

    helper = RecordingHelper(tmp_path / "systemd", fail_daemon_reload=True)
    activity = ActivityStore(tmp_path / "activity2.db")
    failing = ManagedScheduleManager(
        tmp_path / "state",
        activity,
        helper=helper,
        managed_unit_dir=tmp_path / "systemd",
    )
    with pytest.raises(ManagedScheduleError, match="rolled back"):
        await failing.update(
            "daily-backup", _definition(description="should-not-persist"), 0, "owner"
        )

    # The previous valid definition remains installed.
    service = failing._unit_dir() / "hyperion-daily-backup.service"
    assert service.exists()
    assert "Daily backup" in service.read_text(encoding="utf-8")
    assert "should-not-persist" not in service.read_text(encoding="utf-8")


async def test_helper_unavailable_surfaces_as_managed_error(tmp_path: Path) -> None:
    activity = ActivityStore(tmp_path / "activity.db")
    manager = ManagedScheduleManager(
        tmp_path / "state",
        activity,
        helper=BrokenHelper(),
        managed_unit_dir=tmp_path / "systemd",
    )
    with pytest.raises(ManagedScheduleError, match="helper unavailable"):
        await manager.create(_definition(), "owner")


async def test_delete_disables_timer_before_removal(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    await manager.create(_definition(), "owner")
    manager._helper.requests.clear()
    await manager.delete("daily-backup", 0, "owner")
    ops = [op for (_, op, _) in manager._helper.requests]
    assert "disable" in ops
    assert ops.index("disable") < ops.index("remove-unit")


async def test_privileged_stderr_surfaces_on_failure(tmp_path: Path) -> None:
    class StderrHelper:
        async def request(self, unit, operation, content=None):
            if operation == "enable-now":
                return {"ok": False, "returncode": 1, "stderr": "unit does not exist"}
            return {"ok": True, "returncode": 0}

    activity = ActivityStore(tmp_path / "activity.db")
    manager = ManagedScheduleManager(
        tmp_path / "state",
        activity,
        helper=StderrHelper(),
        managed_unit_dir=tmp_path / "systemd",
    )
    with pytest.raises(ManagedScheduleError, match="unit does not exist"):
        await manager.create(_definition(), "owner")


async def test_restore_reenables_previous_schedule(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    await manager.create(_definition(), "owner")

    helper = RecordingHelper(tmp_path / "systemd", fail_daemon_reload=True)
    failing = ManagedScheduleManager(
        tmp_path / "state",
        ActivityStore(tmp_path / "activity2.db"),
        helper=helper,
        managed_unit_dir=tmp_path / "systemd",
    )
    with pytest.raises(ManagedScheduleError, match="rolled back"):
        await failing.update(
            "daily-backup", _definition(description="should-not-persist"), 0, "owner"
        )
    enables = [u for (u, op, _) in helper.requests if op == "enable-now"]
    assert enables == ["hyperion-daily-backup.timer"]


async def test_list_is_sorted_by_name(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    await manager.create(_definition(name="b-schedule"), "owner")
    await manager.create(_definition(name="a-schedule"), "owner")
    names = [view.definition.name for view in manager.list()]
    assert names == ["a-schedule", "b-schedule"]
