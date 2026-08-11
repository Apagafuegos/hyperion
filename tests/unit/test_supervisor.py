"""RefreshSupervisor cycles swallow provider failures and self-heal."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from hyperion.catalog import load_catalog
from hyperion.providers.base import ProbeObservation
from hyperion.providers.fixture import FixtureProbeProvider, FixtureRuntimeProvider
from hyperion.services.snapshots import SnapshotStore
from hyperion.services.supervisor import RefreshSupervisor

FIXTURES = Path(__file__).parents[1] / "fixtures"


def catalog():
    return load_catalog(FIXTURES / "fixture-services.yaml")


def probe_config(cat) -> dict[str, tuple[float, float]]:
    return {
        s.route_probe.url: (s.route_probe.timeout_ms / 1000, s.route_probe.slow_after_ms / 1000)
        for s in cat.services
        if s.route_probe is not None
    }


class RecordingProbe:
    """Records (probes, config) per observe call; returns an empty observation."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, tuple[float, float]] | None]] = []

    async def observe(self, probes, config=None):
        self.calls.append((probes, config))
        return ProbeObservation(
            provider="probe", state="available", observed_at=datetime.now(UTC), results=[]
        )


class FailingRuntime:
    async def observe(self, catalog):
        raise RuntimeError("docker daemon unreachable")

    async def read_logs(self, binding, tail, before):
        return []


class FailingProbe:
    async def observe(self, probes):
        raise RuntimeError("probe worker crashed")


class FlakyRuntime:
    """Fails the first observe call, then delegates to the fixture provider."""

    def __init__(self) -> None:
        self._inner = FixtureRuntimeProvider(
            FIXTURES / "fixture-evidence.json", FIXTURES / "fixture-logs.json"
        )
        self._calls = 0

    async def observe(self, catalog):
        self._calls += 1
        if self._calls == 1:
            raise RuntimeError("transient docker failure")
        return await self._inner.observe(catalog)

    async def read_logs(self, binding, tail, before):
        return await self._inner.read_logs(binding, tail, before)


async def test_runtime_cycle_with_failing_provider_does_not_publish() -> None:
    store = SnapshotStore()
    supervisor = RefreshSupervisor(
        runtime_provider=FailingRuntime(),
        probe_provider=FixtureProbeProvider(FIXTURES / "fixture-probes.json"),
        catalog=catalog(),
        store=store,
    )
    await supervisor._runtime_cycle()
    assert store.get() is None


async def test_probe_cycle_with_failing_provider_does_not_raise() -> None:
    store = SnapshotStore()
    supervisor = RefreshSupervisor(
        runtime_provider=FixtureRuntimeProvider(
            FIXTURES / "fixture-evidence.json", FIXTURES / "fixture-logs.json"
        ),
        probe_provider=FailingProbe(),
        catalog=catalog(),
        store=store,
    )
    await supervisor._probe_cycle()
    assert supervisor._probe_observation is None


async def test_probe_cycle_passes_catalog_probe_config() -> None:
    probe = RecordingProbe()
    supervisor = RefreshSupervisor(
        runtime_provider=FixtureRuntimeProvider(
            FIXTURES / "fixture-evidence.json", FIXTURES / "fixture-logs.json"
        ),
        probe_provider=probe,
        catalog=catalog(),
        store=SnapshotStore(),
    )
    await supervisor._probe_cycle()
    probes, config = probe.calls[0]
    expected = probe_config(catalog())
    assert probes == tuple(expected)
    assert config == expected


async def test_runtime_cycle_passes_catalog_probe_config() -> None:
    probe = RecordingProbe()
    supervisor = RefreshSupervisor(
        runtime_provider=FixtureRuntimeProvider(
            FIXTURES / "fixture-evidence.json", FIXTURES / "fixture-logs.json"
        ),
        probe_provider=probe,
        catalog=catalog(),
        store=SnapshotStore(),
    )
    await supervisor._runtime_cycle()
    probes, config = probe.calls[0]
    expected = probe_config(catalog())
    assert probes == tuple(expected)
    assert config == expected


async def test_cycle_recovers_and_publishes_after_transient_failure() -> None:
    store = SnapshotStore()
    supervisor = RefreshSupervisor(
        runtime_provider=FlakyRuntime(),
        probe_provider=FixtureProbeProvider(FIXTURES / "fixture-probes.json"),
        catalog=catalog(),
        store=store,
    )
    await supervisor._runtime_cycle()
    assert store.get() is None
    await supervisor._runtime_cycle()
    snapshot, _ = store.get()
    assert snapshot is not None
    assert snapshot.fresh is True


async def test_runtime_cycle_live_reloads_catalog_and_keeps_last_valid(
    tmp_path: Path,
) -> None:
    source = FIXTURES / "fixture-services.yaml"
    catalog_path = tmp_path / "services.yaml"
    catalog_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    initial = load_catalog(catalog_path)
    published = []
    supervisor = RefreshSupervisor(
        runtime_provider=FixtureRuntimeProvider(
            FIXTURES / "fixture-evidence.json", FIXTURES / "fixture-logs.json"
        ),
        probe_provider=FixtureProbeProvider(FIXTURES / "fixture-probes.json"),
        catalog=initial,
        base_catalog=initial,
        catalog_path=catalog_path,
        on_catalog=published.append,
        store=SnapshotStore(),
    )

    changed = catalog_path.read_text(encoding="utf-8").replace(
        "name: T3 Code", "name: T3 Reloaded", 1
    )
    catalog_path.write_text(changed, encoding="utf-8")
    await supervisor._runtime_cycle()
    assert published[-1].services[0].name == "T3 Reloaded"

    catalog_path.write_text("not: a valid catalog", encoding="utf-8")
    await supervisor._runtime_cycle()
    assert published[-1].services[0].name == "T3 Reloaded"
