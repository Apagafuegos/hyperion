"""Immutable snapshot store: atomic swap, ETags, and freshness."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hyperion.models import (
    AtlasSnapshot,
    Diagnostics,
    ProviderStatus,
    StateSummary,
    Territory,
)
from hyperion.services.snapshots import FRESH_WINDOW, SnapshotStore, compute_fresh

NOW = datetime(2026, 8, 4, 6, 0, 0, tzinfo=UTC)


def make_snapshot(providers: list[ProviderStatus]) -> AtlasSnapshot:
    return AtlasSnapshot(
        schema_version=1,
        generated_at=NOW,
        catalog_revision="r" * 64,
        fresh=True,
        providers=providers,
        summary=StateSummary(total=0, reachable=0, degraded=0, down=0, dormant=0, unknown=0),
        territories=[
            Territory(id="applications", label="Applications", order=1),
            Territory(id="services", label="Services", order=2),
            Territory(id="foundations", label="Foundations", order=3),
        ],
        services=[],
        diagnostics=Diagnostics(unmapped_runtimes=[], warnings=[]),
    )


def test_store_returns_none_before_first_publish() -> None:
    store = SnapshotStore()
    assert store.get() is None


def test_store_swaps_snapshots_atomically() -> None:
    store = SnapshotStore()
    first = make_snapshot([])
    store.publish(first)
    assert store.get() is not None
    second = make_snapshot([])
    store.publish(second)
    assert store.get()[0] is second


def test_etag_is_stable_and_sensitive() -> None:
    store = SnapshotStore()
    store.publish(make_snapshot([]))
    _, etag_a = store.get()
    store.publish(make_snapshot([]))
    _, etag_b = store.get()
    assert etag_a == etag_b
    changed = make_snapshot(
        [ProviderStatus(provider="docker", state="unavailable", observed_at=NOW, message=None)]
    )
    store.publish(changed)
    _, etag_c = store.get()
    assert etag_c != etag_a
    assert etag_a.startswith('"') and etag_a.endswith('"')


def test_fresh_when_all_required_providers_recent() -> None:
    statuses = {
        "docker": ProviderStatus(
            provider="docker", state="available", observed_at=NOW, message=None
        ),
        "systemd": ProviderStatus(
            provider="systemd", state="available", observed_at=NOW, message=None
        ),
        "probe": ProviderStatus(
            provider="probe", state="available", observed_at=NOW, message=None
        ),
    }
    assert compute_fresh(statuses, {"docker", "probe"}, NOW)


def test_stale_when_required_provider_old() -> None:
    statuses = {
        "docker": ProviderStatus(
            provider="docker", state="available",
            observed_at=NOW - FRESH_WINDOW - timedelta(seconds=1), message=None,
        )
    }
    assert not compute_fresh(statuses, {"docker"}, NOW)


def test_stale_when_required_provider_unavailable() -> None:
    statuses = {
        "docker": ProviderStatus(
            provider="docker", state="unavailable", observed_at=NOW, message="boom"
        )
    }
    assert not compute_fresh(statuses, {"docker"}, NOW)


def test_fresh_ignores_unrequired_providers() -> None:
    statuses = {
        "probe": ProviderStatus(
            provider="probe", state="unavailable", observed_at=NOW, message="down"
        )
    }
    assert compute_fresh(statuses, {"docker", "systemd"}, NOW)


def test_fresh_skips_required_provider_without_entry() -> None:
    assert compute_fresh({}, {"docker"}, NOW)


def test_fresh_at_exact_window_boundary() -> None:
    statuses = {
        "docker": ProviderStatus(
            provider="docker", state="available",
            observed_at=NOW - FRESH_WINDOW, message=None,
        )
    }
    assert compute_fresh(statuses, {"docker"}, NOW)


def test_stale_when_observed_at_missing() -> None:
    statuses = {
        "docker": ProviderStatus(
            provider="docker", state="available", observed_at=None, message=None
        )
    }
    assert not compute_fresh(statuses, {"docker"}, NOW)


def test_compute_fresh_accepts_none_required() -> None:
    assert compute_fresh({}, set(), NOW)
