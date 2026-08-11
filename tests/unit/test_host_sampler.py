"""Host sampler derivation: CPU percent and network rates from counter deltas."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hyperion.models import (
    FilesystemEvidence,
    HostCpuEvidence,
    HostEvidence,
    HostMemoryEvidence,
    NetworkInterfaceEvidence,
)
from hyperion.services.host_sampler import HostSampler


class StubHostProvider:
    """Serves synthetic /proc/stat content; records the last provided text."""

    def __init__(self, stat_lines: list[str]) -> None:
        self._stat_lines = stat_lines
        self._index = 0

    async def observe(self) -> HostEvidence:
        text = self._stat_lines[min(self._index, len(self._stat_lines) - 1)]
        self._index += 1
        evidence = _base_evidence()
        evidence.cpu.utilization_percent = None
        self._proc = _FakeProc(text)
        return evidence


class _FakeProc:
    def __init__(self, text: str) -> None:
        self.text = text

    def read_text(self, encoding: str | None = None) -> str:  # noqa: ARG001
        return self.text


def _base_evidence(observed_at: datetime | None = None) -> HostEvidence:
    return HostEvidence(
        observed_at=observed_at or datetime.now(UTC),
        fresh=True,
        hostname="test",
        uptime_seconds=100.0,
        boot_time=None,
        cpu=HostCpuEvidence(
            utilization_percent=None,
            load_average_1m=0.1,
            load_average_5m=0.1,
            load_average_15m=0.1,
        ),
        memory=HostMemoryEvidence(
            total_bytes=1024,
            available_bytes=512,
            used_bytes=512,
            used_percent=50.0,
            swap_total_bytes=None,
            swap_free_bytes=None,
            swap_used_percent=None,
        ),
        filesystems=[
            FilesystemEvidence(
                mount_point="/",
                device="/dev/root",
                fstype="ext4",
                total_bytes=1000,
                free_bytes=100,
                used_bytes=900,
                used_percent=90.0,
                total_inodes=100,
                free_inodes=90,
                inode_used_percent=10.0,
                state="ok",
            )
        ],
        interfaces=[
            NetworkInterfaceEvidence(
                name="eth0",
                state="up",
                rx_bytes_total=1000,
                tx_bytes_total=2000,
                rx_bytes_per_second=None,
                tx_bytes_per_second=None,
            )
        ],
        pressure=[],
        temperatures=[],
        provider_state="available",
        provider_message=None,
    )


def test_sampler_derives_cpu_percent() -> None:
    provider = StubHostProvider([
        "cpu  100 0 100 800 0 0 0 0 0 0\n",
        "cpu  200 0 200 1600 0 0 0 0 0 0\n",
    ])
    clock = {"t": datetime(2026, 8, 9, 9, 0, 0, tzinfo=UTC)}

    def advance() -> datetime:
        clock["t"] = clock["t"] + timedelta(seconds=7)
        return clock["t"]

    sampler = HostSampler(provider=provider, history_seconds=1800, now=advance)

    import asyncio

    sampler.record(asyncio.run(provider.observe()))
    sampler.record(asyncio.run(provider.observe()))
    current = sampler.current()
    assert current is not None
    # total 1000->2000 (delta 1000), idle 800->1600 (delta 800) => 20% busy.
    assert current.cpu.utilization_percent is not None
    assert abs(current.cpu.utilization_percent - 20.0) < 0.5


def test_sampler_counter_reset_leaves_rate_unknown() -> None:
    provider = StubHostProvider([
        "cpu  100 0 100 800 0 0 0 0 0 0\n",
        "cpu  90 0 90 700 0 0 0 0 0 0\n",  # counter decreased (reset/reboot)
    ])
    clock = {"t": datetime(2026, 8, 9, 9, 0, 0, tzinfo=UTC)}

    def advance() -> datetime:
        clock["t"] = clock["t"] + timedelta(seconds=7)
        return clock["t"]

    sampler = HostSampler(provider=provider, history_seconds=1800, now=advance)

    import asyncio

    sampler.record(asyncio.run(provider.observe()))
    sampler.record(asyncio.run(provider.observe()))
    assert sampler.current() is not None
    assert sampler.current().cpu.utilization_percent is None


def test_history_respects_window() -> None:
    provider = StubHostProvider([
        "cpu  100 0 100 800 0 0 0 0 0 0\n",
        "cpu  200 0 200 1600 0 0 0 0 0 0\n",
    ])
    sampler = HostSampler(
        provider=provider,
        history_seconds=60,
        now=lambda: datetime(2026, 8, 9, 9, 0, 30, tzinfo=UTC),
    )

    import asyncio

    first = asyncio.run(provider.observe())
    first.observed_at = datetime(2026, 8, 9, 8, 59, 0, tzinfo=UTC)
    sampler.record(first)
    second = asyncio.run(provider.observe())
    second.observed_at = datetime(2026, 8, 9, 9, 0, 10, tzinfo=UTC)
    sampler.record(second)

    history = sampler.history(60)
    assert len(history) == 1
    assert history[0].observed_at == datetime(2026, 8, 9, 9, 0, 10, tzinfo=UTC)


def test_interface_rate_derivation() -> None:
    provider = StubHostProvider([
        "cpu  100 0 100 800 0 0 0 0 0 0\n",
        "cpu  200 0 200 1600 0 0 0 0 0 0\n",
    ])
    clock = {"t": datetime(2026, 8, 9, 9, 0, 0, tzinfo=UTC)}

    def advance() -> datetime:
        clock["t"] = clock["t"] + timedelta(seconds=5)
        return clock["t"]

    sampler = HostSampler(provider=provider, history_seconds=1800, now=advance)

    import asyncio

    first = asyncio.run(provider.observe())
    sampler.record(first)
    second = asyncio.run(provider.observe())
    second.interfaces[0].rx_bytes_total = 2000
    second.interfaces[0].tx_bytes_total = 4000
    sampler.record(second)

    current = sampler.current()
    assert current is not None
    # 1000 bytes over the 5s elapsed window = 200 B/s.
    assert current.interfaces[0].rx_bytes_per_second == 200.0
    assert current.interfaces[0].tx_bytes_per_second == 400.0
