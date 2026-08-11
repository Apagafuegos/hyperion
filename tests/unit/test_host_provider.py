"""Host provider reads real /proc-style evidence; tests use temp trees."""

from __future__ import annotations

from pathlib import Path

from hyperion.providers.host import HostProvider


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_reads_memory_and_loadavg(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    _write(proc / "loadavg", "0.42 0.38 0.33 1/123 45678\n")
    _write(
        proc / "meminfo",
        "MemTotal:       16384000 kB\n"
        "MemAvailable:   11744051 kB\n"
        "SwapTotal:       2097152 kB\n"
        "SwapFree:        2097152 kB\n",
    )
    _write(proc / "mounts", "/dev/mapper/vg-root / ext4 rw 0 0\n")
    _write(
        proc / "net" / "dev",
        "Inter-|   Receive\n face |bytes\n"
        "eth0: 1000 0 0 0 0 0 0 0 2000 0 0 0 0 0 0 0\n",
    )
    _write(proc / "sys" / "kernel" / "hostname", "test-vps\n")
    _write(proc / "uptime", "12345.67 54321.0\n")
    _write(proc / "stat", "btime 1754200000\ncpu  100 0 100 1000 0 0 0 0 0 0\n")

    provider = HostProvider(proc=proc, sys=tmp_path / "sys")
    import asyncio

    evidence = asyncio.run(provider.observe())

    assert evidence.hostname == "test-vps"
    assert evidence.memory.total_bytes == 16384000 * 1024
    assert evidence.memory.swap_used_percent == 0.0
    assert evidence.cpu.load_average_1m == 0.42
    assert evidence.provider_state == "available"


def test_missing_files_yield_absent_values() -> None:
    provider = HostProvider(proc=Path("/nonexistent-proc"), sys=Path("/nonexistent-sys"))
    import asyncio

    evidence = asyncio.run(provider.observe())
    assert evidence.cpu.load_average_1m is None
    assert evidence.memory.total_bytes is None
    assert evidence.hostname is None
    assert evidence.filesystems == []
    assert evidence.interfaces == []
    assert evidence.provider_state == "unavailable"


def test_host_never_manufactures_values(tmp_path: Path) -> None:
    # A host with only partial evidence reports what it read and nothing more.
    proc = tmp_path / "proc"
    _write(proc / "loadavg", "0.10 0.05 0.02 1/100 200\n")
    provider = HostProvider(proc=proc, sys=tmp_path / "sys")
    import asyncio

    evidence = asyncio.run(provider.observe())
    assert evidence.cpu.load_average_1m == 0.10
    assert evidence.memory.total_bytes is None
    assert evidence.memory.used_percent is None
    assert evidence.cpu.utilization_percent is None
    assert evidence.provider_state == "degraded"
