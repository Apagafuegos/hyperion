"""Schedule provider: normalizes systemd timers and cron definitions.

Two sources converge on one schedule model. Cron limitations are explicit:
where last result or next execution cannot be established reliably, the model
carries `not_observed`/None rather than inferred evidence.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..models import ScheduleInventoryResponse, ScheduleResult, ScheduleSnapshot
from .cron import CronParseError, parse_cron_expression

logger = logging.getLogger("hyperion.schedules")

_SUBPROCESS_TIMEOUT = 3.0

CRON_PATHS = (
    "/etc/crontab",
    "/etc/cron.d",
    "/etc/cron.hourly",
    "/etc/cron.daily",
    "/etc/cron.weekly",
    "/etc/cron.monthly",
)

ExecRunner = Callable[[list[str]], Coroutine[Any, Any, tuple[int, bytes, bytes]]]


async def _subprocess_runner(
    argv: list[str], timeout_seconds: float = _SUBPROCESS_TIMEOUT
) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            stdout, stderr = await process.communicate()
    except (TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise
    return process.returncode or 0, stdout, stderr


class ScheduleProvider:
    """Read-only schedule inventory from systemd timers and cron files."""

    def __init__(
        self,
        executor: ExecRunner | None = None,
        cron_root: Path | None = None,
        related: dict[str, str] | None = None,
    ) -> None:
        self._executor = executor or _subprocess_runner
        self._cron_root = cron_root
        self._related = related or {}

    async def inventory(self) -> ScheduleInventoryResponse:
        observed_at = datetime.now(UTC)
        schedules: list[ScheduleSnapshot] = []
        try:
            timers = await self._systemd_timers()
            schedules.extend(timers)
        except Exception as exc:  # provider boundary
            logger.warning("systemd timer inventory failed: %s", exc)
        schedules.extend(self._cron_schedules(observed_at))
        return ScheduleInventoryResponse(
            generated_at=observed_at,
            fresh=True,
            schedules=schedules,
        )

    async def _systemd_timers(self) -> list[ScheduleSnapshot]:
        argv = [
            "systemctl", "list-timers", "--all", "--no-pager", "--output=json",
        ]
        returncode, stdout, stderr = await self._executor(argv)
        if returncode != 0:
            raise RuntimeError(
                stderr.decode("utf-8", errors="replace").strip() or "systemctl list-timers failed"
            )
        entries = _parse_json_lines(stdout)
        snapshots: list[ScheduleSnapshot] = []
        for entry in entries:
            unit = entry.get("unit")
            if not isinstance(unit, str) or not unit.endswith(".timer"):
                continue
            timer_id = unit[:-len(".timer")]
            activate = entry.get("activates")
            if not isinstance(activate, str):
                activate = unit.replace(".timer", ".service")
            raw_calendar = entry.get("onCalendar")
            raw_expression = raw_calendar if isinstance(raw_calendar, str) else ""
            raw_owner = entry.get("owner")
            owner = raw_owner if isinstance(raw_owner, str) else None
            snapshots.append(
                ScheduleSnapshot(
                    id=timer_id,
                    name=timer_id,
                    human_readable=_humanize_timer(entry),
                    raw_expression=raw_expression,
                    source="systemd",
                    next_run=_parse_epoch_micros(entry.get("nextElapseUSecRealtime")),
                    last_run=_parse_epoch_micros(entry.get("lastTriggerUSecRealtime")),
                    last_result=_last_result(entry),
                    owner=owner,
                    enabled=not _is_blank(entry.get("unitFileState")),
                    target=activate,
                    provenance=f"systemd timer {unit}",
                    related_service=self._related.get(activate) or self._related.get(unit),
                )
            )
        return snapshots

    def _cron_schedules(self, observed_at: datetime) -> list[ScheduleSnapshot]:
        schedules: list[ScheduleSnapshot] = []
        for path, content in self._read_cron_files():
            for schedule in self._parse_cron_file(path, content, observed_at):
                schedules.append(schedule)
        return schedules

    def _read_cron_files(self) -> list[tuple[Path, str]]:
        root = self._cron_root or Path("/")
        results: list[tuple[Path, str]] = []
        candidates: list[Path] = [Path(p) for p in CRON_PATHS]
        if root != Path("/"):
            candidates = [root / p.lstrip("/") for p in CRON_PATHS]
        for path in candidates:
            if path.is_file():
                try:
                    results.append((path, path.read_text(encoding="utf-8", errors="replace")))
                except OSError as exc:
                    logger.warning("cron file unreadable %s: %s", path, exc)
            elif path.is_dir():
                for child in sorted(path.iterdir()):
                    if not child.is_file():
                        continue
                    if re.search(r"[~#]$", child.name) or child.name.startswith("."):
                        continue
                    try:
                        results.append((child, child.read_text(encoding="utf-8", errors="replace")))
                    except OSError as exc:
                        logger.warning("cron file unreadable %s: %s", child, exc)
        return results

    def _parse_cron_file(
        self, path: Path, content: str, observed_at: datetime
    ) -> list[ScheduleSnapshot]:
        schedules: list[ScheduleSnapshot] = []
        # /etc/crontab and /etc/cron.d use the extended form with a user field
        # after the five schedule fields; user crontabs have none.
        system_crontab = path.name == "crontab" or path.parent.name == "cron.d"
        for line_no, raw_line in enumerate(content.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) < 5 or not _is_cron_field(fields[0]):
                continue
            expression = " ".join(fields[:5])
            rest = fields[5:]
            owner = None
            command = " ".join(rest)
            if system_crontab and rest:
                owner = rest[0]
                command = " ".join(rest[1:])
                if not command or not _is_owner(owner):
                    continue
            if not command:
                continue
            self._append_cron(schedules, path, line_no, expression, owner, command, observed_at)
        return schedules

    def _append_cron(
        self,
        schedules: list[ScheduleSnapshot],
        path: Path,
        line_no: int,
        expression: str,
        owner: str | None,
        command: str,
        observed_at: datetime,
    ) -> None:
        try:
            parsed = parse_cron_expression(expression)
        except CronParseError:
            # A cron line that does not parse is not evidence; skip it rather
            # than present a fabricated schedule.
            return
        schedule_id = _schedule_id(path, line_no)
        schedules.append(
            ScheduleSnapshot(
                id=schedule_id,
                name=_cron_name(command, schedule_id),
                human_readable=parsed.description,
                raw_expression=expression,
                source="cron",
                next_run=parsed.next_run,
                last_run=None,
                last_result="not_observed",
                owner=owner,
                enabled=True,
                target=command[:256],
                provenance=f"{path}:{line_no}",
                related_service=None,
            )
        )


def _parse_json_lines(stdout: bytes) -> list[dict[str, object]]:
    import json

    try:
        parsed = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [entry for entry in parsed if isinstance(entry, dict)]


def _parse_epoch_micros(value: object) -> datetime | None:
    if not isinstance(value, str) or value in ("", "0", "n/a"):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1_000_000, tz=UTC)
    except (ValueError, OSError, OverflowError):
        return None


def _last_result(entry: dict[str, object]) -> ScheduleResult:
    value = entry.get("lastResult")
    if isinstance(value, str) and value.lower() == "success":
        return "success"
    if isinstance(value, str) and value.lower() == "failed":
        return "failed"
    return "unknown"


def _humanize_timer(entry: dict[str, object]) -> str:
    calendar = entry.get("onCalendar")
    if isinstance(calendar, str) and calendar:
        return calendar
    unit = entry.get("unit")
    if isinstance(unit, str):
        return unit
    return "systemd timer"


def _is_cron_field(value: str) -> bool:
    return bool(re.fullmatch(r"(@[a-z]+|\*|[0-9*,/\-@A-Za-z]+)", value))


def _is_owner(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", value))


def _is_blank(value: object) -> bool:
    return not (isinstance(value, str) and value.strip())


def _schedule_id(path: Path, line_no: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", path.name.lower()).strip("-")
    return f"cron-{slug}-{line_no}"


def _cron_name(command: str, schedule_id: str) -> str:
    head = command.split()[0] if command else ""
    base = head.rstrip("/").split("/")[-1] if head else ""
    if base and len(base) <= 48:
        return base
    return schedule_id
