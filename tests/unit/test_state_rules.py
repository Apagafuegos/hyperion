"""Table-driven coverage of the exact state precedence in TECHNICAL-DESIGN.md section 8."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hyperion.models import ComponentSnapshot, RouteSnapshot
from hyperion.state import derive_service_state, make_reason

NOW = datetime(2026, 8, 4, 6, 0, 0, tzinfo=UTC)


def comp(
    key: str,
    role: str = "primary",
    required: bool = True,
    state: str = "running",
    health: str = "healthy",
    provider: str = "docker",
    ambiguous: bool = False,
) -> ComponentSnapshot:
    return ComponentSnapshot(
        key=key,
        label=key,
        provider=provider,
        provider_ref=None,
        role=role,
        required=required,
        state=state,
        health=health,
        observed_at=NOW,
        image=None,
        uptime_seconds=None,
        restart_count=None,
        cpu_percent=None,
        memory_bytes=None,
        ambiguous=ambiguous,
    )


def route(
    state: str = "reachable",
    failures: int = 0,
    slow: bool = False,
    observed: datetime | None = NOW,
) -> RouteSnapshot:
    return RouteSnapshot(
        state=state,
        status_code=200 if state == "reachable" else None,
        latency_ms=1500 if slow else 200,
        consecutive_failures=failures,
        observed_at=observed,
        error=None,
    )


def derive(components, route_snap, providers_ok, intent="active", threshold=2):
    return derive_service_state(
        service_id="svc",
        intent=intent,
        components=components,
        route=route_snap,
        failure_threshold=threshold,
        providers_ok=providers_ok,
        now=NOW,
    )


@pytest.mark.parametrize(
    ("name", "components", "route_snap", "providers_ok", "intent", "expected", "expected_code"),
    [
        (
            "rule1 dormant wins over runtime evidence",
            [comp("p", state="stopped")],
            route(),
            {"docker": True},
            "dormant",
            "dormant",
            "intent_dormant",
        ),
        (
            "rule2 primary missing",
            [comp("p", state="missing")],
            route(),
            {"docker": True},
            "active",
            "down",
            "primary_missing",
        ),
        (
            "rule2 primary stopped",
            [comp("p", state="stopped")],
            route(),
            {"docker": True},
            "active",
            "down",
            "primary_stopped",
        ),
        (
            "rule2 primary paused",
            [comp("p", state="paused")],
            route(),
            {"docker": True},
            "active",
            "down",
            "primary_paused",
        ),
        (
            "rule2 primary ambiguous",
            [comp("p", state="unknown", ambiguous=True)],
            route(),
            {"docker": True},
            "active",
            "down",
            "primary_ambiguous",
        ),
        (
            "rule2 primary docker unhealthy",
            [comp("p", state="running", health="unhealthy")],
            route(),
            {"docker": True},
            "active",
            "down",
            "primary_unhealthy",
        ),
        (
            "rule2 beats rule3 route failure",
            [comp("p", state="stopped")],
            route(state="failed", failures=5),
            {"docker": True},
            "active",
            "down",
            "primary_stopped",
        ),
        (
            "rule3 route failure threshold reached",
            [comp("p")],
            route(state="failed", failures=2),
            {"docker": True},
            "active",
            "down",
            "route_failed",
        ),
        (
            "rule3 beats rule4 stale evidence",
            [comp("p", state="running")],
            route(state="failed", failures=2),
            {"docker": True},
            "active",
            "down",
            "route_failed",
        ),
        (
            "rule4 provider unavailable",
            [comp("p", state="unknown")],
            route(),
            {"docker": False},
            "active",
            "unknown",
            "provider_unavailable",
        ),
        (
            "rule4 evidence stale",
            [comp("p", state="running")],
            route(observed=NOW - timedelta(seconds=60)),
            {"docker": True},
            "active",
            "unknown",
            "evidence_stale",
        ),
        (
            "rule5 primary transitioning",
            [comp("p", state="starting")],
            route(),
            {"docker": True},
            "active",
            "degraded",
            "primary_transitioning",
        ),
        (
            "rule5 primary health starting",
            [comp("p", state="running", health="starting")],
            route(),
            {"docker": True},
            "active",
            "degraded",
            "primary_transitioning",
        ),
        (
            "rule5 required dependency missing",
            [comp("p"), comp("d", role="dependency", state="missing")],
            route(),
            {"docker": True},
            "active",
            "degraded",
            "required_component_missing",
        ),
        (
            "rule5 required dependency unhealthy",
            [comp("p"), comp("d", role="dependency", health="unhealthy")],
            route(),
            {"docker": True},
            "active",
            "degraded",
            "required_component_unhealthy",
        ),
        (
            "rule5 required dependency ambiguous",
            [comp("p"), comp("d", role="dependency", state="unknown", ambiguous=True)],
            route(),
            {"docker": True},
            "active",
            "degraded",
            "required_component_ambiguous",
        ),
        (
            "rule5 required dependency restarting",
            [comp("p"), comp("d", role="dependency", state="restarting")],
            route(),
            {"docker": True},
            "active",
            "degraded",
            "required_component_transitioning",
        ),
        (
            "rule5 beats rule7",
            [comp("p"), comp("d", role="dependency", state="stopped")],
            route(),
            {"docker": True},
            "active",
            "degraded",
            "required_component_stopped",
        ),
        (
            "rule6 slow route",
            [comp("p")],
            route(state="slow", slow=True),
            {"docker": True},
            "active",
            "degraded",
            "route_slow",
        ),
        (
            "rule6 one pending failure",
            [comp("p")],
            route(state="failed", failures=1),
            {"docker": True},
            "active",
            "degraded",
            "route_failure_pending",
        ),
        (
            "rule7 all healthy and route reachable",
            [comp("p")],
            route(),
            {"docker": True},
            "active",
            "reachable",
            None,
        ),
        (
            "rule8 running without probe",
            [comp("p")],
            None,
            {"docker": True},
            "active",
            "reachable",
            None,
        ),
        (
            "rule8 beats rule9",
            [comp("p", state="running", health="unconfigured")],
            None,
            {"docker": True},
            "active",
            "reachable",
            None,
        ),
        (
            "rule9 fallback unknown",
            [comp("p", state="unknown")],
            route(),
            {"docker": True},
            "active",
            "unknown",
            None,
        ),
        (
            "optional component failure keeps reachable with info reason",
            [comp("p"), comp("w", role="worker", required=False, state="stopped")],
            route(),
            {"docker": True},
            "active",
            "reachable",
            "optional_component_unavailable",
        ),
        (
            "systemd healthy",
            [comp("p", provider="systemd", state="running", health="healthy")],
            route(),
            {"systemd": True},
            "active",
            "reachable",
            None,
        ),
        (
            "systemd failed unit is down",
            [comp("p", provider="systemd", state="stopped", health="unhealthy")],
            route(),
            {"systemd": True},
            "active",
            "down",
            "primary_stopped",
        ),
    ],
)
def test_derivation_precedence(
    name: str, components, route_snap, providers_ok, intent, expected, expected_code
) -> None:
    state, reasons = derive(components, route_snap, providers_ok, intent)
    assert state == expected, name
    if expected_code is not None:
        codes = {reason.code for reason in reasons}
        assert expected_code in codes, name


def test_threshold_comes_from_probe_config() -> None:
    state, _ = derive(
        [comp("p")], route(state="failed", failures=2), {"docker": True}, threshold=3
    )
    assert state == "degraded"


def test_reasons_retain_component_key() -> None:
    reason = make_reason("primary_missing", "critical", "Primary component was not found", "p")
    assert reason.component_key == "p"
