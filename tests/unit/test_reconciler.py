"""Reconciler joins catalog entries to provider evidence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from hyperion.catalog import load_catalog
from hyperion.models import DependencySnapshot
from hyperion.providers.base import (
    ComponentEvidence,
    ProbeEvidence,
    ProbeObservation,
    ProviderObservation,
    UnmappedRuntimeEvidence,
)
from hyperion.providers.fixture import FixtureProbeProvider, FixtureRuntimeProvider
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


def test_reconcile_degraded_provider_absence_is_unknown() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    observation = ProviderObservation(
        provider="docker",
        state="degraded",
        observed_at=NOW,
        components=[],
        unmapped=[],
        conflicts=["could not inspect 2 of 12 containers"],
    )
    snapshot = reconcile(catalog, [observation, systemd_obs()], probe_obs([]), NOW)
    authentik = next(s for s in snapshot.services if s.id == "authentik")
    assert authentik.components[0].state == "unknown"
    assert authentik.state == "unknown"


def test_reconcile_joins_evidence() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    evidence = ComponentEvidence(
        service_id="authentik",
        selector="server",
        provider="docker",
        state="running",
        health="healthy",
        reference="authentik-server-1",
        image="ghcr.io/goauthentik/server:2026.6.2",
        uptime_seconds=259200,
        restart_count=0,
        cpu_percent=2.1,
        memory_bytes=419430400,
        observed_at=NOW,
    )
    snapshot = reconcile(catalog, [docker_obs([evidence]), systemd_obs()], probe_obs([]), NOW)
    authentik = next(s for s in snapshot.services if s.id == "authentik")
    component = next(c for c in authentik.components if c.key == "server")
    assert component.state == "running"
    assert component.health == "healthy"
    assert component.provider_ref == "authentik-server-1"
    assert component.image == "ghcr.io/goauthentik/server:2026.6.2"
    assert component.uptime_seconds == 259200
    assert component.restart_count == 0
    assert component.cpu_percent == 2.1
    assert component.memory_bytes == 419430400
    assert component.ambiguous is False


def test_reconcile_ambiguous_required_dependency_degrades() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    evidence = [
        ComponentEvidence(
            service_id="authentik",
            selector="server",
            provider="docker",
            state="running",
            health="healthy",
            observed_at=NOW,
        ),
        ComponentEvidence(
            service_id="authentik",
            selector="worker",
            provider="docker",
            state="running",
            health="healthy",
            observed_at=NOW,
        ),
        ComponentEvidence(
            service_id="authentik",
            selector="postgresql",
            provider="docker",
            state="running",
            health="healthy",
            ambiguous=True,
            observed_at=NOW,
        ),
    ]
    snapshot = reconcile(catalog, [docker_obs(evidence), systemd_obs()], probe_obs([]), NOW)
    authentik = next(s for s in snapshot.services if s.id == "authentik")
    postgresql = next(c for c in authentik.components if c.key == "postgresql")
    assert postgresql.ambiguous is True
    assert authentik.state == "degraded"
    assert {r.code for r in authentik.state_reasons} >= {"required_component_ambiguous"}


def test_reconcile_dependency_states_via_fixture_providers() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    runtime = FixtureRuntimeProvider(
        FIXTURES / "fixture-evidence.json", FIXTURES / "fixture-logs.json"
    )
    probe = FixtureProbeProvider(FIXTURES / "fixture-probes.json")
    observations = asyncio.run(runtime.observe(catalog))
    urls = tuple(s.route_probe.url for s in catalog.services if s.route_probe is not None)
    probe_observation = asyncio.run(probe.observe(urls))
    snapshot = reconcile(catalog, observations, probe_observation, NOW)
    worker = next(s for s in snapshot.services if s.id == "demo-worker")
    assert worker.dependencies == [DependencySnapshot(service_id="demo-dormant", state="dormant")]


def test_reconcile_route_mapping() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    results = [
        ProbeEvidence(
            url="https://auth.carlos-santos.es",
            state="reachable",
            status_code=200,
            latency_ms=210,
            consecutive_failures=0,
            observed_at=NOW,
        )
    ]
    snapshot = reconcile(catalog, [docker_obs([]), systemd_obs()], probe_obs(results), NOW)
    authentik = next(s for s in snapshot.services if s.id == "authentik")
    assert authentik.route is not None
    assert authentik.route.state == "reachable"
    assert authentik.route.status_code == 200
    assert authentik.route.latency_ms == 210
    t3 = next(s for s in snapshot.services if s.id == "t3-code")
    assert t3.route is not None
    assert t3.route.state == "unknown"
    assert t3.route.observed_at is None


def test_reconcile_fresh_with_full_fixture_run() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    runtime = FixtureRuntimeProvider(
        FIXTURES / "fixture-evidence.json", FIXTURES / "fixture-logs.json"
    )
    probe = FixtureProbeProvider(FIXTURES / "fixture-probes.json")
    observations = asyncio.run(runtime.observe(catalog))
    urls = tuple(s.route_probe.url for s in catalog.services if s.route_probe is not None)
    probe_observation = asyncio.run(probe.observe(urls))
    snapshot = reconcile(catalog, observations, probe_observation, NOW)
    assert snapshot.fresh is True


def test_reconcile_truncates_long_conflict_warnings() -> None:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    observation = ProviderObservation(
        provider="docker",
        state="available",
        observed_at=NOW,
        components=[],
        unmapped=[],
        conflicts=["conflict " + "x" * 250],
    )
    snapshot = reconcile(catalog, [observation, systemd_obs()], probe_obs([]), NOW)
    assert snapshot.diagnostics.warnings
    warning = snapshot.diagnostics.warnings[0]
    assert len(warning) <= 240
    assert warning.endswith("...")
