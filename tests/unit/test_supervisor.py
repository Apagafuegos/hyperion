"""RefreshSupervisor cycles swallow provider failures and self-heal."""

from __future__ import annotations

from pathlib import Path

from hyperion.catalog import load_catalog
from hyperion.providers.fixture import FixtureProbeProvider, FixtureRuntimeProvider
from hyperion.services.snapshots import SnapshotStore
from hyperion.services.supervisor import RefreshSupervisor

FIXTURES = Path(__file__).parents[1] / "fixtures"


def catalog():
    return load_catalog(FIXTURES / "fixture-services.yaml")


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
