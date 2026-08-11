"""Operation coordinator: web-request controls for systemd operations.

Every write request passes through here and requires: authenticated identity,
same-origin/CSRF protection, an exact target and typed operation, an
idempotency key, an expected current state, rate limiting, and a server-created
operation record. The privileged helper enforces allowlist and protected-unit
policy independently of this module.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from ..models import (
    ActivityResult,
    OperationKind,
    OperationRequest,
    OperationResult,
    OperationState,
)
from ..ops import OperationDenied, OperationHelperClient
from .activity import ActivityStore

logger = logging.getLogger("hyperion.operations")

_UNIT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]*\.service$")

_RATE_WINDOW_SECONDS = 60.0
_RATE_LIMIT = 20


@dataclass
class _RateBucket:
    window_start: float
    count: int = 0


class OperationCoordinator:
    """Validates, records, and executes typed unit operations."""

    def __init__(
        self,
        helper: OperationHelperClient,
        activity: ActivityStore,
        allowed_units: set[str] | None = None,
        protected_units: set[str] | None = None,
    ) -> None:
        self._helper = helper
        self._activity = activity
        self._allowed_units = set(allowed_units or ())
        self._protected_units = set(protected_units or ())
        self._completed: dict[str, OperationResult] = {}
        self._rate: dict[str, _RateBucket] = {}
        self._lock = threading.Lock()

    def configure_allowlist(self, units: set[str]) -> None:
        with self._lock:
            self._allowed_units = set(units)

    @property
    def allowed_units(self) -> set[str]:
        return set(self._allowed_units)

    def _check_rate(self, identity: str) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._rate.get(identity)
            if bucket is None or now - bucket.window_start >= _RATE_WINDOW_SECONDS:
                bucket = _RateBucket(window_start=now)
                self._rate[identity] = bucket
            bucket.count += 1
            return bucket.count <= _RATE_LIMIT

    async def submit(
        self,
        identity: str,
        unit: str,
        request: OperationRequest,
        idempotency_key: str,
        observed_state: str | None,
    ) -> OperationResult:
        """Validate and execute one idempotent operation."""
        requested_at = datetime.now(UTC)

        if not _UNIT_PATTERN.fullmatch(unit):
            return OperationResult(
                id=_new_id(),
                unit=unit,
                operation=request.operation,
                state="denied",
                message="Invalid unit name.",
                requested_at=requested_at,
                reconciled_at=None,
                evidence={"reason": "invalid_unit"},
            )
        if not self._check_rate(identity):
            return OperationResult(
                id=_new_id(),
                unit=unit,
                operation=request.operation,
                state="denied",
                message="Rate limit reached; try again shortly.",
                requested_at=requested_at,
                reconciled_at=None,
                evidence={"reason": "rate_limited"},
            )

        # Idempotency: repeat submissions return the stored result.
        with self._lock:
            prior = self._completed.get(idempotency_key)
        if prior is not None:
            return prior

        if unit in self._protected_units:
            result = self._deny(
                unit, request.operation, requested_at, "This unit is protected by Hyperion policy."
            )
            return self._record(identity, idempotency_key, unit, request.operation, result)

        # Stale-state protection: an explicit expected state that no longer
        # matches the observed unit state refuses the action.
        if (
            request.expected_state is not None
            and observed_state is not None
            and request.expected_state != observed_state
        ):
            result = self._deny(
                unit,
                request.operation,
                requested_at,
                f"Stale state: unit is {observed_state}, not {request.expected_state}.",
            )
            return self._record(identity, idempotency_key, unit, request.operation, result)

        # Target allowlist (strong operations only) mirrors helper policy.
        if (
            request.operation in {"start", "stop", "restart", "enable", "disable"}
            and unit not in self._allowed_units
        ):
            result = self._deny(
                unit,
                request.operation,
                requested_at,
                "This unit is not allowlisted for that operation.",
            )
            return self._record(identity, idempotency_key, unit, request.operation, result)

        pending = OperationResult(
            id=_new_id(),
            unit=unit,
            operation=request.operation,
            state="pending",
            message="Operation submitted; reconciling systemd evidence.",
            requested_at=requested_at,
            reconciled_at=None,
            evidence={"identity": identity},
        )
        self._activity.record(
            "operation",
            unit,
            f"{request.operation} requested for {unit}",
            result="pending",
            identity=identity,
            target_type="unit",
            dedupe_key=f"op:{idempotency_key}",
            evidence={"operation": request.operation},
        )

        try:
            helper_result = await self._helper.request(unit, request.operation)
        except (TimeoutError, OSError, ConnectionError):
            result = OperationResult(
                id=pending.id,
                unit=unit,
                operation=request.operation,
                state="helper_unavailable",
                message="The privileged operation helper is unavailable.",
                requested_at=requested_at,
                reconciled_at=datetime.now(UTC),
                evidence={"identity": identity, "reason": "helper_unavailable"},
            )
            return self._record(identity, idempotency_key, unit, request.operation, result)
        except OperationDenied as exc:
            result = self._deny(
                unit, request.operation, requested_at, f"Denied by privileged policy: {exc}"
            )
            return self._record(identity, idempotency_key, unit, request.operation, result)

        if not helper_result.get("ok", False):
            denied = helper_result.get("denied")
            final_state: OperationState = "denied" if denied else "failed"
            message = str(helper_result.get("error", "Operation failed."))
            evidence = {"identity": identity}
            if "returncode" in helper_result:
                evidence["returncode"] = str(helper_result["returncode"])
            result = OperationResult(
                id=pending.id,
                unit=unit,
                operation=request.operation,
                state=final_state,
                message=message,
                requested_at=requested_at,
                reconciled_at=datetime.now(UTC),
                evidence=evidence,
            )
            return self._record(identity, idempotency_key, unit, request.operation, result)

        result = OperationResult(
            id=pending.id,
            unit=unit,
            operation=request.operation,
            state="success",
            message=f"{request.operation} completed for {unit}.",
            requested_at=requested_at,
            reconciled_at=datetime.now(UTC),
            evidence={"identity": identity, "returncode": "0"},
        )
        return self._record(identity, idempotency_key, unit, request.operation, result)

    def _deny(
        self, unit: str, operation: OperationKind, requested_at: datetime, message: str
    ) -> OperationResult:
        return OperationResult(
            id=_new_id(),
            unit=unit,
            operation=operation,
            state="denied",
            message=message,
            requested_at=requested_at,
            reconciled_at=None,
            evidence={"reason": "denied"},
        )

    def _record(
        self,
        identity: str,
        idempotency_key: str,
        unit: str,
        operation: OperationKind,
        result: OperationResult,
    ) -> OperationResult:
        with self._lock:
            self._completed[idempotency_key] = result
        result_kind: ActivityResult = cast(
            ActivityResult,
            {
                "success": "success",
                "denied": "denied",
                "helper_unavailable": "warning",
                "pending": "pending",
            }.get(result.state, "failure"),
        )
        self._activity.record(
            "operation",
            unit,
            result.message,
            result=result_kind,
            identity=identity,
            target_type="unit",
            dedupe_key=f"op-result:{result.id}",
            evidence={"operation": operation, "state": result.state},
        )
        return result


def _new_id() -> str:
    return uuid.uuid4().hex[:16]
