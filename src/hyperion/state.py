"""Service state derivation.

Implements the exact precedence table from TECHNICAL-DESIGN.md section 8.
The first matching terminal rule wins; informational reasons are retained.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from .models import ComponentSnapshot, RouteSnapshot, ServiceState, StateReason

FRESHNESS_WINDOW = timedelta(seconds=45)

_DOWN_COMPONENT_STATES = {"missing", "stopped", "paused"}
_REQUIRED_DEGRADED_STATES = {"missing", "stopped", "paused", "restarting"}


def make_reason(
    code: str,
    severity: Literal["info", "warning", "critical"],
    message: str,
    component_key: str | None = None,
) -> StateReason:
    return StateReason(
        code=code,
        severity=severity,
        message=message,
        component_key=component_key,
    )


def _primary(components: list[ComponentSnapshot]) -> ComponentSnapshot:
    for component in components:
        if component.role == "primary":
            return component
    raise ValueError("service has no primary component")


def _evidence_stale(
    components: list[ComponentSnapshot], route: RouteSnapshot | None, now: datetime
) -> bool:
    for component in components:
        if not component.required:
            continue
        if component.observed_at is None or now - component.observed_at > FRESHNESS_WINDOW:
            return True
    if route is not None and route.observed_at is not None:
        if now - route.observed_at > FRESHNESS_WINDOW:
            return True
    return False


def derive_service_state(
    service_id: str,
    intent: str,
    components: list[ComponentSnapshot],
    route: RouteSnapshot | None,
    failure_threshold: int,
    providers_ok: dict[str, bool],
    now: datetime,
) -> tuple[ServiceState, list[StateReason]]:
    """Return (state, reasons) for one service per the section 8 precedence."""
    reasons: list[StateReason] = []
    primary = _primary(components)

    informational_reasons(components, reasons)

    # Rule 1: dormant intent wins over runtime evidence.
    if intent == "dormant":
        reasons.append(make_reason("intent_dormant", "info", "Service is intentionally dormant."))
        return "dormant", reasons

    # Rule 2: primary is missing, stopped, paused, ambiguous, or unhealthy.
    if primary.state in _DOWN_COMPONENT_STATES:
        label = "was not found" if primary.state == "missing" else f"is {primary.state}"
        reasons.append(
            make_reason(
                f"primary_{primary.state}", "critical", f"Primary component {label}.", primary.key
            )
        )
        return "down", reasons
    if primary.ambiguous:
        reasons.append(
            make_reason(
                "primary_ambiguous",
                "critical",
                "Multiple containers matched the primary component.",
                primary.key,
            )
        )
        return "down", reasons
    if primary.provider == "docker" and primary.health == "unhealthy":
        reasons.append(
            make_reason(
                "primary_unhealthy",
                "critical",
                "Primary component failed its health check.",
                primary.key,
            )
        )
        return "down", reasons

    # Rule 3: route reached its configured consecutive-failure threshold.
    if route is not None and route.consecutive_failures >= failure_threshold:
        reasons.append(
            make_reason("route_failed", "critical", "The public route has failed repeatedly.")
        )
        return "down", reasons

    # Rule 4: required provider evidence unavailable or stale.
    if not providers_ok.get(primary.provider, True):
        reasons.append(
            make_reason(
                "provider_unavailable",
                "warning",
                "The runtime provider for this service is unavailable.",
            )
        )
        return "unknown", reasons
    if _evidence_stale(components, route, now):
        reasons.append(
            make_reason(
                "evidence_stale",
                "warning",
                "Runtime evidence is older than the freshness boundary.",
            )
        )
        return "unknown", reasons

    # Rule 5: primary transitioning, or a required non-primary component degrades.
    if primary.state in {"starting", "restarting"} or primary.health == "starting":
        reasons.append(
            make_reason(
                "primary_transitioning",
                "warning",
                "Primary component is starting or restarting.",
                primary.key,
            )
        )
        return "degraded", reasons
    for component in components:
        if component.key == primary.key or not component.required:
            continue
        if component.state in _REQUIRED_DEGRADED_STATES:
            code = (
                "required_component_transitioning"
                if component.state == "restarting"
                else f"required_component_{component.state}"
            )
            reasons.append(
                make_reason(
                    code,
                    "warning",
                    f"Required component {component.label} is {component.state}.",
                    component.key,
                )
            )
            return "degraded", reasons
        if component.ambiguous:
            reasons.append(
                make_reason(
                    "required_component_ambiguous",
                    "warning",
                    f"Required component {component.label} matched multiple containers.",
                    component.key,
                )
            )
            return "degraded", reasons
        if component.health == "unhealthy":
            reasons.append(
                make_reason(
                    "required_component_unhealthy",
                    "warning",
                    f"Required component {component.label} failed its health check.",
                    component.key,
                )
            )
            return "degraded", reasons

    # Rule 6: one pending failure or slow route.
    if route is not None:
        if route.state == "slow":
            reasons.append(
                make_reason("route_slow", "warning", "The public route is slower than expected.")
            )
            return "degraded", reasons
        if 0 < route.consecutive_failures < failure_threshold:
            reasons.append(
                make_reason(
                    "route_failure_pending",
                    "warning",
                    "The public route failed; the failure threshold has not been reached.",
                )
            )
            return "degraded", reasons

    # Rules 7/8: primary running, required components running and not unhealthy,
    # and either the route is reachable or no route is configured.
    runtime_ok = primary.state == "running" and primary.health != "unhealthy"
    if runtime_ok:
        for component in components:
            if component.key == primary.key or not component.required:
                continue
            if component.state != "running" or component.health == "unhealthy":
                runtime_ok = False
                break
    if runtime_ok:
        if route is None or route.state == "reachable":
            return "reachable", reasons

    # Rule 9: fallback.
    return "unknown", reasons


def informational_reasons(
    components: list[ComponentSnapshot], reasons: list[StateReason]
) -> None:
    """Non-state-changing reasons retained alongside the terminal one."""
    for component in components:
        if component.required or component.role == "primary":
            continue
        if component.state != "running":
            reasons.append(
                make_reason(
                    "optional_component_unavailable",
                    "info",
                    f"Optional component {component.label} is not running.",
                    component.key,
                )
            )
