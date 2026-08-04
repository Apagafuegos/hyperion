"""Reconciler joins catalog entries to provider evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from hyperion.catalog import load_catalog
from hyperion.providers.base import (
    ComponentEvidence,
    ProbeEvidence,
    ProbeObservation,
    ProviderObservation,
    UnmappedRuntimeEvidence,
)
from hyperion.services.reconciler import reconcile

FIXTURES = Path(__file__).parents[1] / "fixtures"
NOW = datetime(2026, 8, 4, 6, 0, 0, tzinfo=UTC)


def docker_obs(components: list[ComponentEvidence]) -> ProviderObservation:
    return ProviderObservation(
        provider="docker",
        state="available",
        observed_at=NOW,
        components=components,
        unmapped=[
            UnmappedRuntimeEvidence(
                provider="docker",
                reference="caddy-1",
                project="caddy",
                component="caddy",
                state="running",
            )
        ],
    )


def systemd_obs() -> ProviderObservation:
    return ProviderObservation(
        provider="systemd", state="available", observed_at=NOW, components=[], unmapped=[]
    )


def probe_obs(results: list[ProbeEvidence]) -> ProbeObservation:
    return ProbeObservation(
        provider="probe", state="available", observed_at=NOW, results=results
    )


def test_reconcile_builds_snapshot() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    snapshot = reconcile(catalog, [docker_obs([]), systemd_obs()], probe_obs([]), NOW)
    assert snapshot.schema_version == 1
    assert len(snapshot.catalog_revision) == 64
    assert len(snapshot.services) == len(catalog.services)
    assert [t.id for t in snapshot.territories] == ["applications", "services", "foundations"]
    assert snapshot.diagnostics.unmapped_runtimes[0].reference == "caddy-1"


def test_reconcile_missing_component_is_missing() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    snapshot = reconcile(catalog, [docker_obs([]), systemd_obs()], probe_obs([]), NOW)
    t3 = next(s for s in snapshot.services if s.id == "t3-code")
    assert t3.components[0].state == "missing"
    assert t3.state == "down"


def test_reconcile_dormant_intent_wins() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    snapshot = reconcile(catalog, [docker_obs([]), systemd_obs()], probe_obs([]), NOW)
    demo = next(s for s in snapshot.services if s.id == "demo-dormant")
    assert demo.state == "dormant"


def test_reconcile_provider_unavailable_yields_unknown() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    observation = ProviderObservation(
        provider="docker",
        state="unavailable",
        observed_at=NOW,
        components=[],
        error="connection refused to proxy",
    )
    snapshot = reconcile(catalog, [observation, systemd_obs()], probe_obs([]), NOW)
    authentik = next(s for s in snapshot.services if s.id == "authentik")
    assert authentik.state == "unknown"
    assert authentik.components[0].state == "unknown"
    assert not snapshot.fresh
    status = next(p for p in snapshot.providers if p.provider == "docker")
    assert status.state == "unavailable"
    assert "proxy" in (status.message or "")


def test_reconcile_log_sources_and_availability() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    snapshot = reconcile(catalog, [docker_obs([]), systemd_obs()], probe_obs([]), NOW)
    langfuse = next(s for s in snapshot.services if s.id == "langfuse")
    keys = {source.key for source in langfuse.log_sources}
    assert keys == {"langfuse", "langfuse-worker"}
    assert all(source.available for source in langfuse.log_sources)
    t3 = next(s for s in snapshot.services if s.id == "t3-code")
    assert t3.log_sources[0].provider == "journald"
