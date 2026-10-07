"""HTTP routes: health, readiness, snapshot, and logs."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.security import APIKeyHeader
from pydantic import BeforeValidator

from ..models import AtlasSnapshot, ErrorResponse, HealthResponse, Id, LogsResponse
from ..services.logs import LogGateway
from .errors import ApiError

router = APIRouter()

_identity_header = APIKeyHeader(name="X-Authentik-Username", auto_error=False)


def _as_int(value: object) -> object:
    """Coerce a query-string digit into an int before the Literal allowlist."""
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return value


def require_identity(
    request: Request, identity: Annotated[str | None, Depends(_identity_header)]
) -> str:
    read_token = os.environ.get("HYPERION_READ_TOKEN", "")
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if read_token and hmac.compare_digest(supplied, read_token):
        if request.method not in {"GET", "HEAD"}:
            raise ApiError(403, "READ_ONLY", "This credential permits reading only.", False)
        principal = "connector:" + hashlib.sha256(read_token.encode()).hexdigest()
        request.state.authenticated_principal = principal
        return principal
    secret = os.environ.get("MANAGEMENT_PROXY_SECRET", "")
    proof = request.headers.get("X-Management-Proxy-Secret", "")
    groups = request.headers.get("X-Authentik-Groups", "").split("|")
    if not secret or not hmac.compare_digest(proof, secret) or not identity:
        raise ApiError(401, "AUTHENTICATION_REQUIRED", "Trusted authentication is required.", False)
    if "authentik Admins" not in groups:
        raise ApiError(403, "ADMIN_REQUIRED", "Administrator access is required.", False)
    request.state.authenticated_principal = "admin:" + identity
    return identity


def authenticated_principal(request: Request, _: Annotated[str, Depends(require_identity)]) -> str:
    """Private reference scope; identity display names cannot collide with credentials."""
    return str(request.state.authenticated_principal)


@router.get(
    "/healthz",
    response_model=HealthResponse,
    summary="Process liveness check",
    responses={200: {"description": "The process is alive."}},
)
def healthz() -> HealthResponse:
    """Process liveness check."""
    return HealthResponse(status="ok")


@router.get(
    "/readyz",
    response_model=HealthResponse,
    summary="Catalog and initial snapshot readiness check",
    responses={
        200: {"description": "The catalog is valid and an initial snapshot exists."},
        503: {
            "description": "The application cannot yet serve a valid atlas snapshot.",
            "model": ErrorResponse,
        },
    },
)
def readyz(request: Request) -> HealthResponse:
    """Catalog and initial snapshot readiness check."""
    if request.app.state.snapshot_store.get() is None:
        raise ApiError(
            503, "SNAPSHOT_UNAVAILABLE", "The atlas has not published a valid snapshot yet.", True
        )
    return HealthResponse(status="ready")


@router.get(
    "/api/v1/snapshot",
    response_model=AtlasSnapshot,
    summary="Get the complete normalized atlas snapshot",
    responses={
        200: {
            "description": "Current normalized service snapshot.",
            "model": AtlasSnapshot,
            "headers": {
                "ETag": {"required": True, "schema": {"type": "string"}},
                "Cache-Control": {
                    "required": True,
                    "schema": {"type": "string", "const": "private, max-age=0, must-revalidate"},
                },
            },
        },
        304: {"description": "Snapshot has not changed."},
        401: {"description": "Authentik identity header is absent.", "model": ErrorResponse},
        503: {
            "description": "Required provider data is temporarily unavailable.",
            "model": ErrorResponse,
        },
    },
)
def get_snapshot(
    request: Request,
    identity: Annotated[str, Depends(require_identity)],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    """Get the complete normalized atlas snapshot."""
    pair = request.app.state.snapshot_store.get()
    if pair is None:
        raise ApiError(
            503, "SNAPSHOT_UNAVAILABLE", "The atlas has not published a valid snapshot yet.", True
        )
    snapshot, etag = pair
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    headers = {"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"}
    payload = snapshot.model_dump(mode="json", by_alias=True)
    return Response(content=json.dumps(payload), media_type="application/json", headers=headers)


@router.get(
    "/api/v1/services/{serviceId}/logs",
    response_model=LogsResponse,
    summary="Get bounded recent logs for one allowlisted service",
    responses={
        200: {
            "description": "Logs sorted oldest to newest.",
            "model": LogsResponse,
            "headers": {
                "Cache-Control": {
                    "required": True,
                    "schema": {"type": "string", "const": "private, no-store"},
                },
            },
        },
        401: {"description": "Authentik identity header is absent.", "model": ErrorResponse},
        404: {
            "description": "Service or log source is not in the validated catalog.",
            "model": ErrorResponse,
        },
        503: {
            "description": "Required provider data is temporarily unavailable.",
            "model": ErrorResponse,
        },
    },
)
async def get_service_logs(
    request: Request,
    response: Response,
    serviceId: Id,
    source: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=128,
            description="Allowlisted component selector. Omit to aggregate all configured sources.",
        ),
    ] = None,
    tail: Annotated[Literal[50, 100, 250, 500], BeforeValidator(_as_int), Query()] = 100,
    before: Annotated[
        datetime | None,
        Query(description="Exclusive RFC 3339 upper timestamp for bounded backward pagination."),
    ] = None,
    _: Annotated[str, Depends(require_identity)] = "",
) -> LogsResponse:
    """Get bounded recent logs for one allowlisted service."""
    response.headers["Cache-Control"] = "private, no-store"
    gateway: LogGateway = request.app.state.log_gateway
    return await gateway.read(serviceId, source, tail, before)
