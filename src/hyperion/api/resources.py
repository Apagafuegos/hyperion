"""HTTP routes: host, units, schedules, activity, and operations resources."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BeforeValidator

from ..models import (
    ActivityResponse,
    HostEvidence,
    HostHistoryResponse,
    LogRecord,
    ManagedScheduleDefinition,
    ManagedScheduleView,
    OperationRequest,
    OperationResult,
    ScheduleInventoryResponse,
    ScheduleSnapshot,
    UnitInventoryResponse,
    UnitLogsResponse,
)
from ..providers.schedules import ScheduleProvider
from ..providers.systemd_units import SystemdUnitProvider
from ..services.activity import ActivityStore, ActivityUnavailable
from ..services.host_sampler import HostSampler
from ..services.managed_schedules import ManagedScheduleError, ManagedScheduleManager
from ..services.operations import OperationCoordinator
from .errors import ApiError
from .routes import require_identity

logger = logging.getLogger("hyperion.api.resources")

router = APIRouter()


def _as_int(value: object) -> object:
    """Coerce a query-string digit into an int before the Literal allowlist."""
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return value


def _window_seconds(value: str) -> int:
    if value.endswith("m"):
        return int(value[:-1]) * 60
    if value.endswith("h"):
        return int(value[:-1]) * 3600
    if value.endswith("s"):
        return int(value[:-1])
    raise ValueError("window must end in s, m, or h")


@router.get(
    "/api/v1/host",
    response_model=HostEvidence,
    summary="Get current host evidence",
    responses={
        200: {"description": "Current host metric evidence.", "model": HostEvidence},
        401: {"description": "Authentik identity header is absent.", "model": None},
    },
)
def get_host(request: Request, _: Annotated[str, Depends(require_identity)]) -> HostEvidence:
    """Get the current bounded host evidence sample."""
    sampler: HostSampler = request.app.state.host_sampler
    sample = sampler.current()
    if sample is None:
        raise ApiError(503, "HOST_UNAVAILABLE", "No host sample has been collected yet.", True)
    return sample


@router.get(
    "/api/v1/host/history",
    response_model=HostHistoryResponse,
    summary="Get recent host history samples",
    responses={
        200: {
            "description": "Bounded recent host samples, oldest first.",
            "model": HostHistoryResponse,
        },
        401: {"description": "Authentik identity header is absent.", "model": None},
    },
)
def get_host_history(
    request: Request,
    window: Annotated[str, Query(pattern=r"^\d+[smh]$")] = "30m",
    _: Annotated[str, Depends(require_identity)] = "",
) -> HostHistoryResponse:
    """Get bounded recent host samples within the requested window."""
    sampler: HostSampler = request.app.state.host_sampler
    try:
        seconds = _window_seconds(window)
    except ValueError as exc:
        raise ApiError(400, "INVALID_WINDOW", str(exc), False) from exc
    samples = sampler.history(seconds)
    return HostHistoryResponse(
        window_seconds=seconds,
        observed_at=datetime.now().astimezone(),
        samples=samples,
    )


def _related_map(request: Request) -> dict[str, str]:
    """Map unit names to related Atlas service ids from the latest snapshot."""
    pair = request.app.state.snapshot_store.get()
    if pair is None:
        return {}
    snapshot, _ = pair
    related: dict[str, str] = {}
    for service in snapshot.services:
        for component in service.components:
            if component.provider == "systemd" and component.provider_ref is not None:
                related[component.provider_ref] = service.id
    return related


@router.get(
    "/api/v1/units",
    response_model=UnitInventoryResponse,
    summary="Get the systemd unit inventory",
    responses={
        200: {"description": "Curated systemd service inventory.", "model": UnitInventoryResponse},
        401: {"description": "Authentik identity header is absent.", "model": None},
    },
)
async def get_units(
    request: Request, _: Annotated[str, Depends(require_identity)]
) -> UnitInventoryResponse:
    """Get the systemd service inventory with per-unit property evidence."""
    provider: SystemdUnitProvider = request.app.state.unit_provider
    return await provider.inventory(related=_related_map(request))


@router.get(
    "/api/v1/units/{unitName}/logs",
    response_model=UnitLogsResponse,
    summary="Get bounded journal logs for one unit",
    responses={
        200: {
            "description": "Journal logs for the unit, oldest to newest.",
            "model": UnitLogsResponse,
        },
        401: {"description": "Authentik identity header is absent.", "model": None},
        404: {"description": "Unit is not in the server-enumerated inventory.", "model": None},
        503: {"description": "Journal is temporarily unavailable.", "model": None},
    },
)
async def get_unit_logs(
    request: Request,
    response: Response,
    unitName: str,
    tail: Annotated[Literal[50, 100, 250, 500], BeforeValidator(_as_int), Query()] = 100,
    _: Annotated[str, Depends(require_identity)] = "",
) -> UnitLogsResponse:
    """Get bounded journal logs for one server-enumerated unit."""
    response.headers["Cache-Control"] = "private, no-store"
    provider: SystemdUnitProvider = request.app.state.unit_provider
    inventory = await provider.inventory(related=_related_map(request))
    if not any(unit.name == unitName for unit in inventory.units):
        raise ApiError(
            404, "UNIT_NOT_FOUND", "Unit is not in the server-enumerated inventory.", False
        )
    try:
        rows = await provider.read_logs(unitName, tail)
    except Exception as exc:  # provider boundary
        raise ApiError(503, "JOURNAL_UNAVAILABLE", f"Journal read failed: {exc}", True) from exc
    return UnitLogsResponse(
        unit=unitName,
        requested_at=datetime.now().astimezone(),
        records=[LogRecord.model_validate(row) for row in rows],
        truncated=len(rows) == tail,
    )


@router.get(
    "/api/v1/schedules",
    response_model=ScheduleInventoryResponse,
    summary="Get the schedule inventory",
    responses={
                200: {
            "description": "Normalized systemd timer and cron schedule index.",
            "model": ScheduleInventoryResponse,
        },
        401: {"description": "Authentik identity header is absent.", "model": None},
    },
)
async def get_schedules(
    request: Request, _: Annotated[str, Depends(require_identity)]
) -> ScheduleInventoryResponse:
    """Get the unified schedule inventory from systemd timers and cron."""
    provider: ScheduleProvider = request.app.state.schedule_provider
    return await provider.inventory()


@router.get(
    "/api/v1/schedules/{scheduleId}",
    response_model=ScheduleSnapshot,
    summary="Get one schedule record",
    responses={
        200: {"description": "A single schedule record.", "model": ScheduleSnapshot},
        401: {"description": "Authentik identity header is absent.", "model": None},
        404: {"description": "Schedule is not in the inventory.", "model": None},
    },
)
async def get_schedule(
    request: Request, scheduleId: str, _: Annotated[str, Depends(require_identity)] = ""
) -> ScheduleSnapshot:
    """Get one schedule record by inventory identity."""
    provider: ScheduleProvider = request.app.state.schedule_provider
    inventory = await provider.inventory()
    for schedule in inventory.schedules:
        if schedule.id == scheduleId:
            return schedule
    raise ApiError(404, "SCHEDULE_NOT_FOUND", "Schedule is not in the inventory.", False)


@router.get(
    "/api/v1/activity",
    response_model=ActivityResponse,
    summary="Get recorded activity",
    responses={
        200: {"description": "Recorded events newest-first.", "model": ActivityResponse},
        401: {"description": "Authentik identity header is absent.", "model": None},
    },
)
def get_activity(
    request: Request,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    _: Annotated[str, Depends(require_identity)] = "",
) -> ActivityResponse:
    """Get recorded activity events, newest-first."""
    store: ActivityStore = request.app.state.activity_store
    try:
        return store.query(since=since, limit=limit)
    except ActivityUnavailable as exc:
        raise ApiError(503, "ACTIVITY_UNAVAILABLE", str(exc), True) from exc


@router.post(
    "/api/v1/units/{unitName}/operations",
    response_model=OperationResult,
    summary="Request a typed systemd operation",
    responses={
        200: {
            "description": "The operation result, pending or terminal.",
            "model": OperationResult,
        },
        401: {"description": "Authentik identity header is absent.", "model": None},
    },
)
async def post_operation(
    request: Request,
    unitName: str,
    body: OperationRequest,
    idempotencyKey: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=64)],
    origin: Annotated[str | None, Header(alias="Origin")] = None,
    identity: Annotated[str, Depends(require_identity)] = "",
) -> OperationResult:
    """Request one typed operation; idempotent per idempotency key."""
    _check_origin(request, origin)
    coordinator: OperationCoordinator = request.app.state.operation_coordinator
    provider: SystemdUnitProvider = request.app.state.unit_provider
    observed_state: str | None = None
    try:
        inventory = await provider.inventory(related=_related_map(request))
    except Exception:  # provider boundary
        inventory = None
    if inventory is not None:
        for unit in inventory.units:
            if unit.name == unitName:
                observed_state = unit.active_state
                break
    return await coordinator.submit(identity, unitName, body, idempotencyKey, observed_state)


@router.post(
    "/api/v1/schedules",
    response_model=ManagedScheduleView,
    status_code=201,
    summary="Create a Hyperion-managed schedule",
    responses={
        201: {"description": "The installed managed schedule.", "model": ManagedScheduleView},
        400: {"description": "Definition failed validation.", "model": None},
        401: {"description": "Authentik identity header is absent.", "model": None},
    },
)
async def create_schedule(
    request: Request,
    body: ManagedScheduleDefinition,
    origin: Annotated[str | None, Header(alias="Origin")] = None,
    identity: Annotated[str, Depends(require_identity)] = "",
) -> ManagedScheduleView:
    """Create a managed schedule as a systemd service/timer pair."""
    _check_origin(request, origin)
    manager: ManagedScheduleManager = request.app.state.managed_schedules
    try:
        return await manager.create(body, identity)
    except ManagedScheduleError as exc:
        raise ApiError(400, "SCHEDULE_INVALID", str(exc), False) from exc


@router.put(
    "/api/v1/schedules/{scheduleId}",
    response_model=ManagedScheduleView,
    summary="Update a Hyperion-managed schedule",
    responses={
        200: {"description": "The updated managed schedule.", "model": ManagedScheduleView},
        400: {"description": "Definition or revision failed validation.", "model": None},
        401: {"description": "Authentik identity header is absent.", "model": None},
    },
)
async def update_schedule(
    request: Request,
    scheduleId: str,
    body: ManagedScheduleDefinition,
    expectedRevision: Annotated[int, Query(ge=0)] = 0,
    origin: Annotated[str | None, Header(alias="Origin")] = None,
    identity: Annotated[str, Depends(require_identity)] = "",
) -> ManagedScheduleView:
    """Update a managed schedule with optimistic-concurrency revision control."""
    _check_origin(request, origin)
    manager: ManagedScheduleManager = request.app.state.managed_schedules
    try:
        return await manager.update(scheduleId, body, expectedRevision, identity)
    except ManagedScheduleError as exc:
        raise ApiError(400, "SCHEDULE_INVALID", str(exc), False) from exc


@router.delete(
    "/api/v1/schedules/{scheduleId}",
    status_code=204,
    summary="Delete a Hyperion-managed schedule",
    responses={
        204: {"description": "The managed schedule was deleted."},
        400: {"description": "Revision or existence validation failed.", "model": None},
        401: {"description": "Authentik identity header is absent.", "model": None},
    },
)
async def delete_schedule(
    request: Request,
    scheduleId: str,
    expectedRevision: Annotated[int, Query(ge=0)] = 0,
    origin: Annotated[str | None, Header(alias="Origin")] = None,
    identity: Annotated[str, Depends(require_identity)] = "",
) -> Response:
    """Delete a managed schedule; the timer and service units are removed."""
    _check_origin(request, origin)
    manager: ManagedScheduleManager = request.app.state.managed_schedules
    try:
        await manager.delete(scheduleId, expectedRevision, identity)
    except ManagedScheduleError as exc:
        raise ApiError(400, "SCHEDULE_INVALID", str(exc), False) from exc
    return Response(status_code=204)


def _check_origin(request: Request, origin: str | None) -> None:
    """Same-origin protection: write requests must carry a matching Origin."""
    host = request.headers.get("host", "")
    if origin is None:
        return
    from urllib.parse import urlparse

    parsed = urlparse(origin)
    if parsed.hostname and parsed.hostname == host.split(":")[0]:
        return
    raise ApiError(403, "CROSS_ORIGIN_DENIED", "Cross-origin write requests are refused.", False)
