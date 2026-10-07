"""API snapshot models serialize with the committed camelCase contract."""

from __future__ import annotations

from datetime import UTC, datetime

from hyperion.models import (
    AtlasSnapshot,
    ComponentSnapshot,
    Diagnostics,
    ErrorResponse,
    LogsResponse,
    ProviderStatus,
    ServiceSnapshot,
    StateReason,
    StateSummary,
    Territory,
)


def make_service() -> ServiceSnapshot:
    return ServiceSnapshot(
        id="langfuse",
        name="Langfuse",
        description="LLM observability",
        territory="applications",
        kind="web",
        intent="active",
        action={"type": "open", "url": "https://langfuse.carlos-santos.es"},
        state="reachable",
        state_reasons=[
            StateReason(code="intent_dormant", severity="info", message="x", component_key=None)
        ],
        observed_at=datetime(2026, 8, 4, 6, 0, 0, tzinfo=UTC),
        route=None,
        components=[
            ComponentSnapshot(
                key="langfuse",
                label="Langfuse",
                provider="docker",
                provider_ref="langfuse-1",
                role="primary",
                required=True,
                state="running",
                health="healthy",
                observed_at=datetime(2026, 8, 4, 6, 0, 0, tzinfo=UTC),
                image="ghcr.io/langfuse/langfuse:3",
                uptime_seconds=86400,
                restart_count=0,
                cpu_percent=1.25,
                memory_bytes=104857600,
            )
        ],
        dependencies=[],
        log_sources=[],
    )


def test_snapshot_uses_camel_case_aliases() -> None:
    service = make_service()
    payload = service.model_dump(by_alias=True)
    assert set(payload) == {
        "id",
        "name",
        "description",
        "territory",
        "kind",
        "intent",
        "action",
        "state",
        "stateReasons",
        "observedAt",
        "route",
        "components",
        "dependencies",
        "logSources",
        "diagnosticEndpoints",
    }
    component = payload["components"][0]
    assert component["uptimeSeconds"] == 86400
    assert component["restartCount"] == 0
    assert component["cpuPercent"] == 1.25
    assert component["memoryBytes"] == 104857600
    assert component["providerRef"] == "langfuse-1"
    assert payload["stateReasons"][0]["componentKey"] is None


def test_snapshot_populate_by_name() -> None:
    payload = make_service().model_dump(by_alias=True)
    assert ServiceSnapshot.model_validate(payload).id == "langfuse"


def test_unknown_properties_rejected() -> None:
    payload = make_service().model_dump(by_alias=True)
    payload["invented"] = True
    try:
        ServiceSnapshot.model_validate(payload)
    except Exception as exc:  # pydantic.ValidationError
        assert "invented" in str(exc)
    else:
        raise AssertionError("expected validation error")


def test_full_atlas_snapshot_round_trip() -> None:
    snapshot = AtlasSnapshot(
        schema_version=1,
        generated_at=datetime(2026, 8, 4, 6, 0, 0, tzinfo=UTC),
        catalog_revision="a" * 64,
        fresh=True,
        providers=[
            ProviderStatus(
                provider="docker",
                state="available",
                observed_at=datetime(2026, 8, 4, 6, 0, 0, tzinfo=UTC),
                message=None,
            )
        ],
        summary=StateSummary(total=10, reachable=4, degraded=2, down=2, dormant=1, unknown=1),
        territories=[
            Territory(id="applications", label="Applications", order=1),
            Territory(id="services", label="Services", order=2),
            Territory(id="foundations", label="Foundations", order=3),
        ],
        services=[make_service()],
        diagnostics=Diagnostics(unmapped_runtimes=[], warnings=[]),
    )
    payload = snapshot.model_dump(by_alias=True)
    assert payload["catalogRevision"] == "a" * 64
    assert payload["diagnostics"]["unmappedRuntimes"] == []
    assert AtlasSnapshot.model_validate(payload).generated_at == snapshot.generated_at


def test_error_envelope_shape() -> None:
    error = ErrorResponse(
        error={"code": "SERVICE_NOT_FOUND", "message": "no such service", "retryable": False}
    )
    payload = error.model_dump(by_alias=True)
    assert payload["error"]["code"] == "SERVICE_NOT_FOUND"
    assert ErrorResponse.model_validate(payload).error.retryable is False


def test_logs_response_shape() -> None:
    response = LogsResponse(
        service_id="langfuse",
        requested_at=datetime(2026, 8, 4, 6, 0, 0, tzinfo=UTC),
        source=None,
        records=[],
        truncated=False,
    )
    payload = response.model_dump(by_alias=True)
    assert set(payload) == {"serviceId", "requestedAt", "source", "records", "truncated"}
