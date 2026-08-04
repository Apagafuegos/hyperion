"""HTTP routes: health, readiness, snapshot, and logs."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import APIKeyHeader
from pydantic import BeforeValidator

from ..models import AtlasSnapshot, HealthResponse, LogsResponse
from ..services.logs import LogGateway
from .errors import ApiError

router = APIRouter()

_identity_header = APIKeyHeader(name="X-Authentik-Username", auto_error=False)


def _as_int(value: object) -> object:
    """Coerce a query-string digit into an int before the Literal allowlist."""
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return value


def require_identity(identity: Annotated[str | None, Depends(_identity_header)]) -> str:
    if identity is None or not identity.strip():
        raise ApiError(
            401, "AUTHENTICATION_REQUIRED", "Authentik identity header is required.", False
        )
    return identity


@router.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    """Process liveness check."""
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=HealthResponse)
def readyz(request: Request) -> HealthResponse:
    """Catalog and initial snapshot readiness check."""
    if request.app.state.snapshot_store.get() is None:
        raise ApiError(
            503, "SNAPSHOT_UNAVAILABLE", "The atlas has not published a valid snapshot yet.", True
        )
    return HealthResponse(status="ready")


@router.get("/api/v1/snapshot", response_model=AtlasSnapshot)
def get_snapshot(
    request: Request, identity: Annotated[str, Depends(require_identity)]
) -> Response:
    """Get the complete normalized atlas snapshot."""
    pair = request.app.state.snapshot_store.get()
    if pair is None:
        raise ApiError(
            503, "SNAPSHOT_UNAVAILABLE", "The atlas has not published a valid snapshot yet.", True
        )
    snapshot, etag = pair
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    headers = {"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"}
    payload = snapshot.model_dump(mode="json", by_alias=True)
    return Response(content=json.dumps(payload), media_type="application/json", headers=headers)


@router.get("/api/v1/services/{service_id}/logs", response_model=LogsResponse)
async def get_service_logs(
    request: Request,
    response: Response,
    service_id: str,
    source: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    tail: Annotated[Literal[50, 100, 250, 500], BeforeValidator(_as_int), Query()] = 100,
    before: Annotated[datetime | None, Query()] = None,
    _: Annotated[str, Depends(require_identity)] = "",
) -> LogsResponse:
    """Get bounded recent logs for one allowlisted service."""
    response.headers["Cache-Control"] = "private, no-store"
    gateway: LogGateway = request.app.state.log_gateway
    return await gateway.read(service_id, source, tail, before)
