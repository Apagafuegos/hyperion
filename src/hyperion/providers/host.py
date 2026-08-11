"""Host evidence provider: bounded reads from /proc and /sys.

Reads raw counters and gauges; never manufactures values. CPU utilization and
network rates are derived downstream from monotonic counter differences
(host_sampler), not invented here. Missing files yield None, never fabricated
numbers.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ..models import (
    FilesystemEvidence,
    HostCpuEvidence,
    HostEvidence,
    HostMemoryEvidence,
    NetworkInterfaceEvidence,
    PressureEvidence,
    TemperatureEvidence,
)

logger = logging.getLogger("hyperion.host")

# Filesystem types that represent real local or bind-mounted storage.
_INTERESTING_FS = {
    "ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "overlay", "fuse.sshfs",
    "tmpfs", "vfat", "exfat", "ntfs",
}

_PressureKind = Literal["cpu", "io", "memory"]
_PRESSURE_KINDS: tuple[_PressureKind, ...] = ("cpu", "io", "memory")

_InterfaceState = Literal["up", "down", "unknown"]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _parse_uint(value: str | None) -> int | None:
    if value is None or not value.isdigit():
        return None
    return int(value)


class HostProvider:
    """Reads current host evidence from the local filesystem."""

    def __init__(self, proc: Path = Path("/proc"), sys: Path = Path("/sys")) -> None:
        self._proc = proc
        self._sys = sys

    async def observe(self) -> HostEvidence:
        observed_at = datetime.now(UTC)
        provider_message: str | None = None
        provider_state: Literal["available", "degraded", "unavailable"] = "available"

        cpu = self._read_cpu()
        memory = self._read_memory()
        has_cpu_evidence = (
            cpu.utilization_percent is not None
            or cpu.load_average_1m is not None
            or cpu.load_average_5m is not None
            or cpu.load_average_15m is not None
        )
        if not has_cpu_evidence and memory.total_bytes is None:
            provider_state = "unavailable"
            provider_message = "No host evidence could be read."
        elif memory.total_bytes is None:
            provider_state = "degraded"
            provider_message = "Some host evidence is unavailable."
        elif not has_cpu_evidence:
            provider_state = "degraded"
            provider_message = "Some host evidence is unavailable."

        filesystems = self._read_filesystems()
        interfaces = self._read_interfaces()
        pressure = self._read_pressure()
        temperatures = self._read_temperatures()
        hostname = self._read_hostname()
        uptime = self._read_uptime()
        boot_time = self._read_boot_time()

        return HostEvidence(
            observed_at=observed_at,
            fresh=True,
            hostname=hostname,
            uptime_seconds=uptime,
            boot_time=boot_time,
            cpu=cpu,
            memory=memory,
            filesystems=filesystems,
            interfaces=interfaces,
            pressure=pressure,
            temperatures=temperatures,
            provider_state=provider_state,
            provider_message=provider_message,
        )

    def _read_cpu(self) -> HostCpuEvidence:
        # CPU utilization is derived by the sampler from successive /proc/stat
        # aggregates; the provider only carries the load averages it can read.
        loadavg = _read(self._proc / "loadavg").split()
        loads = [float(x) for x in loadavg[:3] if _is_float(x)]
        return HostCpuEvidence(
            utilization_percent=None,
            load_average_1m=loads[0] if len(loads) >= 1 else None,
            load_average_5m=loads[1] if len(loads) >= 2 else None,
            load_average_15m=loads[2] if len(loads) >= 3 else None,
        )

    def _read_memory(self) -> HostMemoryEvidence:
        values: dict[str, int] = {}
        for line in _read(self._proc / "meminfo").splitlines():
            match = re.match(r"^([A-Za-z_()]+):\s+(\d+) kB$", line)
            if match:
                values[match.group(1)] = int(match.group(2)) * 1024
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        swap_total = values.get("SwapTotal")
        swap_free = values.get("SwapFree")
        used = (total - available) if (total is not None and available is not None) else None
        used_percent = _percent(used, total)
        swap_used = (
            (swap_total - swap_free)
            if (swap_total is not None and swap_free is not None)
            else None
        )
        return HostMemoryEvidence(
            total_bytes=total,
            available_bytes=available,
            used_bytes=used,
            used_percent=used_percent,
            swap_total_bytes=swap_total,
            swap_free_bytes=swap_free,
            swap_used_percent=_percent(swap_used, swap_total),
        )

    def _read_filesystems(self) -> list[FilesystemEvidence]:
        evidence: list[FilesystemEvidence] = []
        try:
            os_statvfs = __import__("os").statvfs
        except (AttributeError, ImportError):
            return evidence
        for line in _read(self._proc / "mounts").splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            device, mount_point, fstype = parts[0], parts[1], parts[2]
            if fstype not in _INTERESTING_FS:
                continue
            if mount_point.startswith("/proc") or mount_point.startswith("/sys"):
                continue
            try:
                stats = os_statvfs(mount_point)
            except OSError:
                continue
            total = stats.f_frsize * stats.f_blocks
            free = stats.f_frsize * stats.f_bavail
            used = total - free
            total_inodes = stats.f_files
            free_inodes = stats.f_ffree
            state: Literal["ok", "full", "inode_pressure", "degraded", "unavailable"] = "ok"
            if free == 0:
                state = "full"
            elif total_inodes and free_inodes / total_inodes < 0.10:
                state = "inode_pressure"
            evidence.append(
                FilesystemEvidence(
                    mount_point=mount_point,
                    device=device,
                    fstype=fstype,
                    total_bytes=total,
                    free_bytes=free,
                    used_bytes=used,
                    used_percent=_percent(used, total),
                    total_inodes=total_inodes,
                    free_inodes=free_inodes,
                    inode_used_percent=_percent(total_inodes - free_inodes, total_inodes),
                    state=state,
                )
            )
        return evidence

    def _read_interfaces(self) -> list[NetworkInterfaceEvidence]:
        evidence: list[NetworkInterfaceEvidence] = []
        text = _read(self._proc / "net" / "dev")
        for line in text.splitlines()[2:]:
            parts = line.split(":")
            if len(parts) != 2:
                continue
            name = parts[0].strip()
            cols = parts[1].split()
            if len(cols) < 9:
                continue
            rx_bytes = _parse_uint(cols[0])
            tx_bytes = _parse_uint(cols[8])
            evidence.append(
                NetworkInterfaceEvidence(
                    name=name,
                    state=_interface_state(name),
                    rx_bytes_total=rx_bytes,
                    tx_bytes_total=tx_bytes,
                    rx_bytes_per_second=None,
                    tx_bytes_per_second=None,
                )
            )
        return evidence

    def _read_pressure(self) -> list[PressureEvidence]:
        evidence: list[PressureEvidence] = []
        for kind in _PRESSURE_KINDS:
            text = _read(self._proc / "pressure" / kind)
            values: dict[str, float] = {}
            for line in text.splitlines():
                key, _, rest = line.partition(" ")
                if not rest:
                    continue
                for token in rest.split():
                    if "=" in token:
                        k, v = token.split("=", 1)
                        if _is_float(v):
                            values[f"{key}_{k}"] = float(v)
            evidence.append(
                PressureEvidence(
                    kind=kind,
                    some_avg_10=values.get("some_avg10"),
                    some_avg_300=values.get("some_avg300"),
                    full_avg_10=values.get("full_avg10"),
                )
            )
        return evidence

    def _read_temperatures(self) -> list[TemperatureEvidence]:
        evidence: list[TemperatureEvidence] = []
        base = self._sys / "class" / "thermal"
        if not base.is_dir():
            return evidence
        for zone in sorted(base.iterdir()):
            if not zone.name.startswith("thermal_zone"):
                continue
            type_text = _read(zone / "type").strip()
            temp_text = _read(zone / "temp").strip()
            raw_milli = _parse_uint(temp_text)
            celsius = raw_milli / 1000.0 if raw_milli is not None else None
            evidence.append(
                TemperatureEvidence(zone=type_text or zone.name, celsius=celsius)
            )
        return evidence

    def _read_hostname(self) -> str | None:
        hostname = _read(self._proc / "sys" / "kernel" / "hostname").strip()
        return hostname or None

    def _read_uptime(self) -> float | None:
        text = _read(self._proc / "uptime").split()
        return float(text[0]) if text and _is_float(text[0]) else None

    def _read_boot_time(self) -> datetime | None:
        for line in _read(self._proc / "stat").splitlines():
            if line.startswith("btime "):
                value = line.split()[1]
                if value.isdigit():
                    return datetime.fromtimestamp(int(value), tz=UTC)
                return None
        return None


def _percent(part: int | None, total: int | None) -> float | None:
    if part is None or total is None or total == 0:
        return None
    return round((part / total) * 100, 1)


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _interface_state(name: str) -> _InterfaceState:
    if name == "lo":
        return "up"
    try:
        state_path = Path("/sys/class/net") / name / "operstate"
        oper = state_path.read_text(encoding="utf-8").strip()
        return "up" if oper == "up" else "down"
    except OSError:
        return "unknown"
