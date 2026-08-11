"""Systemd unit inventory provider: curated, validated enumeration.

Reads the unit table and per-unit properties through fixed-argv subprocesses.
The provider never exposes arbitrary systemctl invocations to the web layer:
callers pass exact unit names that are validated against the `SystemdUnit`
pattern, and the provider performs only its own fixed command set.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, Literal, cast

from ..models import (
    UnitFileState,
    UnitInventoryResponse,
    UnitSnapshot,
    UnitState,
)

logger = logging.getLogger("hyperion.units")

_SUBPROCESS_TIMEOUT = 3.0

# Protection classification: access-critical units are protected at the
# privileged boundary (Phase 5); this list drives the inventory classification
# and must match the privileged helper's allowlist independent of the UI.
PROTECTED_UNIT_PATTERNS = (
    re.compile(r"^(ssh|sshd)\.(service|socket)$"),
    re.compile(r"^(systemd-networkd|NetworkManager|networking|network)\.service$"),
    re.compile(r"^docker\.service$"),
    re.compile(r"^authentik.*\.service$"),
    re.compile(r"^caddy\.service$"),
    re.compile(r"^hyperion.*\.service$"),
)

ExecRunner = Callable[[list[str]], Coroutine[Any, Any, tuple[int, bytes, bytes]]]

_ENABLED_MAP: dict[str, UnitFileState] = {
    "enabled": "enabled",
    "enabled-runtime": "enabled-runtime",
    "linked": "linked",
    "linked-runtime": "linked",
    "masked": "masked",
    "masked-runtime": "masked",
    "disabled": "disabled",
    "static": "static",
    "indirect": "indirect",
    "generated": "generated",
    "transient": "transient",
    "bad": "bad",
}


def _as_state(value: str) -> UnitState:
    if value in ("active", "activating", "reloading", "deactivating", "inactive", "failed"):
        return value  # type: ignore[return-value]
    return "unknown"


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


class SystemdUnitProvider:
    """Enumerates systemd services and their properties."""

    def __init__(self, executor: ExecRunner | None = None) -> None:
        self._executor = executor or _subprocess_runner

    async def inventory(
        self,
        related: dict[str, str] | None = None,
    ) -> UnitInventoryResponse:
        """Return the full service inventory with property evidence."""
        observed_at = datetime.now(UTC)
        related = related or {}
        units: list[UnitSnapshot] = []
        try:
            names = await self._list_service_units()
            rows = await asyncio.gather(
                *(self._show_unit(name) for name in names),
                return_exceptions=True,
            )
        except Exception as exc:  # provider boundary
            logger.warning("unit inventory failed: %s", exc)
            return UnitInventoryResponse(
                generated_at=observed_at,
                fresh=False,
                counts={},
                units=[],
            )
        for name, row in zip(names, rows, strict=False):
            if isinstance(row, BaseException):
                logger.warning("unit property read failed for %s: %s", name, row)
                continue
            unit = _unit_from_properties(name, row, related)
            units.append(unit)
        counts: dict[str, int] = {}
        for unit in units:
            counts[unit.active_state] = counts.get(unit.active_state, 0) + 1
        return UnitInventoryResponse(
            generated_at=observed_at,
            fresh=True,
            counts=counts,
            units=units,
        )

    async def _list_service_units(self) -> list[str]:
        argv = [
            "systemctl", "list-units",
            "--type=service", "--all", "--no-pager",
            "--output=json",
        ]
        returncode, stdout, stderr = await self._executor(argv)
        if returncode != 0:
            raise RuntimeError(
                stderr.decode("utf-8", errors="replace").strip() or "systemctl list-units failed"
            )
        names: list[str] = []
        for entry in _parse_systemctl_json(stdout):
            unit = entry.get("unit")
            if isinstance(unit, str) and _UNIT_NAME.fullmatch(unit):
                names.append(unit)
        return names

    async def _show_unit(self, unit: str) -> dict[str, str]:
        argv = [
            "systemctl", "show", unit, "--no-pager",
            "--property=Names,Description,LoadState,ActiveState,SubState,"
            "UnitFileState,ActiveEnterTimestamp,MainPID,NRestarts,MemoryCurrent,"
            "TriggeredBy",
        ]
        returncode, stdout, stderr = await self._executor(argv)
        if returncode != 0:
            raise RuntimeError(
                stderr.decode("utf-8", errors="replace").strip() or f"systemctl show {unit} failed"
            )
        properties: dict[str, str] = {}
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                properties[key] = value
        return properties

    async def read_logs(
        self, unit: str, tail: int = 100
    ) -> list[dict[str, object]]:
        """Read bounded journal records for one unit as validated evidence."""
        from ..models import LogRecord

        argv = [
            "journalctl", "--unit", unit,
            "--lines", str(tail),
            "--output", "json", "--no-pager", "--quiet",
        ]
        returncode, stdout, stderr = await self._executor(argv)
        if returncode != 0:
            raise RuntimeError(
                stderr.decode("utf-8", errors="replace").strip() or "journalctl failed"
            )
        records: list[dict[str, object]] = []
        import json as _json
        from datetime import UTC

        for line in stdout.decode("utf-8", errors="replace").splitlines():
            try:
                entry = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            raw = entry.get("__REALTIME_TIMESTAMP")
            timestamp = None
            if isinstance(raw, str) and raw.isdigit():
                timestamp = datetime.fromtimestamp(int(raw) / 1_000_000, tz=UTC)
            priority = str(entry.get("PRIORITY", ""))
            severity = _severity(priority)
            message = entry.get("MESSAGE", "")
            if not isinstance(message, str):
                message = str(message)
            record = LogRecord(
                timestamp=timestamp,
                source=unit,
                provider="journald",
                stream="journal",
                severity=severity,
                message=message.rstrip("\n")[:16384],
                truncated=False,
            )
            records.append(record.model_dump(mode="json", by_alias=True))
        return records


_UNIT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]*\.service$")
_ACTIVE_ENTERED_EPOCH = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})")

_PRIORITY_MAP = {
    "7": "debug", "6": "info", "5": "notice", "4": "warning",
    "3": "error", "2": "critical", "1": "alert", "0": "emergency",
}

_Severity = Literal[
    "debug", "info", "notice", "warning", "error", "critical", "alert", "emergency"
]


def _severity(value: str) -> _Severity | None:
    mapped = _PRIORITY_MAP.get(value)
    return cast(_Severity | None, mapped)


def _parse_systemctl_json(stdout: bytes) -> list[dict[str, object]]:
    try:
        parsed = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [entry for entry in parsed if isinstance(entry, dict)]


def _parse_epoch_micros(value: str) -> datetime | None:
    if value == "" or value == "0":
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1_000_000, tz=UTC)
    except (ValueError, OSError, OverflowError):
        return None


def _unit_from_properties(
    name: str, props: dict[str, str], related: dict[str, str]
) -> UnitSnapshot:
    description = props.get("Description", "")
    raw_load_state = props.get("LoadState", "unknown")
    load_state: Literal["loaded", "not-found", "error", "masked", "unknown"]
    if raw_load_state in ("loaded", "not-found", "error", "masked"):
        load_state = cast(
            Literal["loaded", "not-found", "error", "masked", "unknown"], raw_load_state
        )
    else:
        load_state = "unknown"
    active_state = _as_state(props.get("ActiveState", "unknown"))
    sub_state = props.get("SubState", "unknown")
    raw_enabled = props.get("UnitFileState", "")
    enabled = _ENABLED_MAP.get(raw_enabled, "unknown")
    active_entered = _parse_epoch_micros(props.get("ActiveEnterTimestamp", ""))
    main_pid = _int_or_none(props.get("MainPID"))
    if main_pid == 0:
        main_pid = None
    restart_count = _int_or_none(props.get("NRestarts"))
    memory = _int_or_none(props.get("MemoryCurrent"))
    triggered_by = props.get("TriggeredBy", "")
    related_timer = next(
        (u for u in triggered_by.split() if u.endswith(".timer")), None
    )
    protection: Literal["protected", "allowlisted", "ordinary"] = "ordinary"
    if any(pattern.fullmatch(name) for pattern in PROTECTED_UNIT_PATTERNS):
        protection = "protected"
    return UnitSnapshot(
        name=name,
        description=description[:256],
        load_state=load_state,
        active_state=active_state,
        sub_state=sub_state[:64],
        enabled_state=enabled,
        active_entered=active_entered,
        main_pid=main_pid,
        restart_count=restart_count,
        memory_bytes=memory,
        related_timer=related_timer,
        dependencies=[],
        related_service=related.get(name),
        protection=protection,
        curated=name in related,
    )


def _int_or_none(value: str | None) -> int | None:
    if value is not None and value.isdigit():
        return int(value)
    return None
