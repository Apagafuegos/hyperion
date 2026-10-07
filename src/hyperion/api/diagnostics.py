"""Authenticated resource-scoped reads; all collection executes in the host API."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from ..diagnostics.models import (
    EndpointListenersResult,
    ListenersInput,
    ProcessInput,
    ProcessResult,
    RuntimeInput,
    ServiceRuntimeResult,
)
from ..diagnostics.provider import DiagnosticRequestError, DiagnosticsProvider
from ..models import Catalog, Service
from .errors import ApiError
from .routes import authenticated_principal

router = APIRouter(prefix="/api/v1/diagnostics", tags=["diagnostics"])


def _service(request: Request, service_id: str) -> Service:
    catalog: Catalog | None = request.app.state.catalog
    if catalog is None:
        raise ApiError(503, "CATALOG_UNAVAILABLE", "Service scope cannot be established.", True)
    for service in catalog.services:
        if service.service_id == service_id:
            return service
    raise ApiError(404, "SERVICE_OUT_OF_SCOPE", "Discover the service's catalog ID first.", False)


def _request_error(exc: DiagnosticRequestError) -> ApiError:
    return ApiError(
        exc.status,
        exc.code,
        "Diagnostic resource is unavailable; rediscover its service process or endpoint.",
        False,
    )


@router.get("/service-runtime", response_model=ServiceRuntimeResult)
async def get_service_runtime(
    request: Request,
    response: Response,
    arguments: Annotated[RuntimeInput, Query()],
    principal: Annotated[str, Depends(authenticated_principal)],
) -> ServiceRuntimeResult:
    response.headers["Cache-Control"] = "private, no-store"
    provider: DiagnosticsProvider = request.app.state.diagnostics_provider
    return await provider.get_service_runtime(_service(request, arguments.service), principal)


@router.get("/endpoint-listeners", response_model=EndpointListenersResult)
async def get_endpoint_listeners(
    request: Request,
    response: Response,
    arguments: Annotated[ListenersInput, Query()],
    principal: Annotated[str, Depends(authenticated_principal)],
) -> EndpointListenersResult:
    response.headers["Cache-Control"] = "private, no-store"
    provider: DiagnosticsProvider = request.app.state.diagnostics_provider
    try:
        return await provider.get_endpoint_listeners(
            _service(request, arguments.service), principal, arguments.endpoint_id
        )
    except DiagnosticRequestError as exc:
        raise _request_error(exc) from exc


@router.get("/process", response_model=ProcessResult)
async def inspect_process(
    request: Request,
    response: Response,
    arguments: Annotated[ProcessInput, Query()],
    principal: Annotated[str, Depends(authenticated_principal)],
) -> ProcessResult:
    response.headers["Cache-Control"] = "private, no-store"
    provider: DiagnosticsProvider = request.app.state.diagnostics_provider
    try:
        return await provider.inspect_process(
            _service(request, arguments.service), principal, arguments.process_ref
        )
    except DiagnosticRequestError as exc:
        raise _request_error(exc) from exc
