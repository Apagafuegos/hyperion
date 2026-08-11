"""Stable error envelope from TECHNICAL-DESIGN.md section 10."""

from __future__ import annotations

from typing import cast

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..models import ErrorDetail, ErrorResponse

ERROR_CODES = (
    "AUTHENTICATION_REQUIRED",
    "SNAPSHOT_UNAVAILABLE",
    "SERVICE_NOT_FOUND",
    "LOG_SOURCE_NOT_FOUND",
    "LOG_SOURCE_UNAVAILABLE",
    "INVALID_TAIL",
    "INVALID_BEFORE",
    "INVALID_REQUEST",
    "PROVIDER_TIMEOUT",
    "RESPONSE_TOO_LARGE",
    "HOST_UNAVAILABLE",
    "ACTIVITY_UNAVAILABLE",
    "INVALID_WINDOW",
    "UNIT_NOT_FOUND",
    "SCHEDULE_NOT_FOUND",
    "JOURNAL_UNAVAILABLE",
    "SCHEDULE_INVALID",
    "CROSS_ORIGIN_DENIED",
    "WORKSPACE_NOT_FOUND",
)


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, retryable: bool) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


def _payload(code: str, message: str, retryable: bool) -> dict[str, object]:
    error = ErrorResponse(error=ErrorDetail(code=code, message=message, retryable=retryable))
    return error.model_dump(by_alias=True)


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    api_error = cast(ApiError, exc)
    return JSONResponse(
        status_code=api_error.status_code,
        content=_payload(api_error.code, api_error.message, api_error.retryable),
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    validation_error = cast(RequestValidationError, exc)
    locations = [tuple(error["loc"]) for error in validation_error.errors()]
    if any("before" in loc for loc in locations):
        return JSONResponse(
            status_code=422, content=_payload("INVALID_BEFORE", "Invalid timestamp.", False)
        )
    if any("tail" in loc for loc in locations):
        return JSONResponse(
            status_code=422, content=_payload("INVALID_TAIL", "Invalid tail value.", False)
        )
    return JSONResponse(
        status_code=422, content=_payload("INVALID_REQUEST", "Invalid request.", False)
    )
