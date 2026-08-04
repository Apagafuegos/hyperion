"""Fixture providers return deterministic evidence for the fixture catalog."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hyperion.catalog import load_catalog
from hyperion.providers.base import LogBinding
from hyperion.providers.fixture import FixtureProbeProvider, FixtureRuntimeProvider

FIXTURES = Path(__file__).parents[1] / "fixtures"


def catalog():
    return load_catalog(FIXTURES / "fixture-services.yaml")


def runtime_provider() -> FixtureRuntimeProvider:
    return FixtureRuntimeProvider(
        FIXTURES / "fixture-evidence.json", FIXTURES / "fixture-logs.json"
    )


def test_runtime_evidence_covers_all_services() -> None:
    observations = asyncio.run(runtime_provider().observe(catalog()))
    assert len(observations) == 2
    assert observations[0].provider == "docker"
    assert observations[1].provider == "systemd"
    keys = {(c.service_id, c.selector) for o in observations for c in o.components}
    declared = {
        (s.service_id, c.selector) for s in catalog().services for c in s.runtime.components
    }
    assert keys == declared


def test_unmapped_runtimes_present() -> None:
    observations = asyncio.run(runtime_provider().observe(catalog()))
    docker = next(o for o in observations if o.provider == "docker")
    references = {u.reference for u in docker.unmapped}
    assert references == {"caddy-1", "tailscale-1", "watchtower"}


def test_observed_at_stamped_now() -> None:
    observations = asyncio.run(runtime_provider().observe(catalog()))
    now = datetime.now(UTC)
    assert all(abs((o.observed_at - now).total_seconds()) < 60 for o in observations)


def test_probe_results_for_every_probe_binding() -> None:
    provider = FixtureProbeProvider(FIXTURES / "fixture-probes.json")
    urls = tuple(s.route_probe.url for s in catalog().services if s.route_probe is not None)
    observation = asyncio.run(provider.observe(urls))
    assert len(observation.results) == len(urls)
    by_url = {r.url: r for r in observation.results}
    assert by_url["https://langfuse.carlos-santos.es"].consecutive_failures == 2


def test_probe_unknown_url_reported_unknown() -> None:
    provider = FixtureProbeProvider(FIXTURES / "fixture-probes.json")
    observation = asyncio.run(provider.observe(("https://nope.carlos-santos.es",)))
    result = observation.results[0]
    assert result.state == "unknown"
    assert result.status_code is None
    assert result.error is None


def test_logs_are_bounded_and_filtered() -> None:
    binding = LogBinding(
        provider="docker", service_id="authentik", source="server", reference="authentik-server-1"
    )
    records = asyncio.run(runtime_provider().read_logs(binding, tail=10, before=None))
    assert all(r.source == "server" for r in records)
    assert len(records) <= 10


def test_logs_respect_before_cutoff() -> None:
    binding = LogBinding(
        provider="docker", service_id="authentik", source="server", reference="authentik-server-1"
    )
    before = datetime.now(UTC) - timedelta(seconds=30)
    records = asyncio.run(runtime_provider().read_logs(binding, tail=10, before=before))
    assert len(records) == 1


def test_logs_tail_returns_most_recent() -> None:
    binding = LogBinding(
        provider="docker", service_id="authentik", source="server", reference="authentik-server-1"
    )
    records = asyncio.run(runtime_provider().read_logs(binding, tail=1, before=None))
    assert len(records) == 1
    assert records[0].message == "rate limiting burst exceeded"
