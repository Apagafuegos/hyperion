"""Selected Linux reads only. No shell, environment reads, or arbitrary proc paths."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ..models import DiagnosticEndpoint
from ..privacy import redact
from .models import CommandSummary, ControlGroup, Issue, Scope

MAX_PROCESSES = 24
MAX_LISTENERS = 24
MAX_OWNERS = 12
MAX_SCAN_PIDS = 8192
MAX_SCAN_TASKS = 16384
MAX_SCAN_FDS = 65536


class ReadFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def issue(code: str, *fields: str, retryable: bool = False) -> Issue:
    return Issue(code=code, fields=list(fields), retryable=retryable)


def error_code(exc: Exception, prefix: str) -> str:
    if isinstance(exc, PermissionError):
        return f"{prefix}_PERMISSION_DENIED"
    if isinstance(exc, FileNotFoundError):
        return f"{prefix}_EXITED" if prefix == "PROCESS" else f"{prefix}_UNAVAILABLE"
    if isinstance(exc, ReadFailure):
        return exc.code
    return f"{prefix}_UNAVAILABLE"


def read_bounded(path: Path, limit: int = 16384) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ReadFailure("READ_LIMIT_EXCEEDED")
    return data


async def run_read_command(argv: list[str]) -> tuple[int, bytes, bytes]:
    """Bound both pipes and kill/reap on cancellation, timeout, or excessive output."""
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC"},
    )

    async def drain(stream: asyncio.StreamReader | None) -> bytes:
        if stream is None:
            return b""
        data = bytearray()
        while chunk := await stream.read(4096):
            data.extend(chunk)
            if len(data) > 65536:
                raise ReadFailure("MANAGER_OUTPUT_LIMIT")
        return bytes(data)

    try:
        async with asyncio.timeout(2):
            stdout, stderr = await asyncio.gather(drain(process.stdout), drain(process.stderr))
            await process.wait()
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    return process.returncode or 0, stdout, stderr


@dataclass(frozen=True)
class Process:
    pid: int
    name: str
    state: str
    parent_pid: int
    start_ticks: int


@dataclass(frozen=True)
class Socket:
    inode: int
    family: Literal["ipv4", "ipv6"]
    address: str
    port: int
    overlap: Literal["exact", "wildcard", "potential_ipv6"]


@dataclass
class ProcessScan:
    processes: list[Process] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    changed: bool = False
    truncated: bool = False


@dataclass
class SocketScan:
    sockets: list[Socket] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    tables_read: int = 0
    truncated: bool = False


@dataclass
class OwnerScan:
    owners: dict[int, list[Process]] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    changed: bool = False
    truncated: bool = False


@dataclass
class FdScan:
    matches: dict[int, Path] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    changed: bool = False
    truncated: bool = False
    tasks: int = 0
    descriptors: int = 0


def endpoint_overlap(
    endpoint: DiagnosticEndpoint, address: str
) -> Literal["exact", "wildcard", "potential_ipv6"] | None:
    requested = ipaddress.ip_address(endpoint.address)
    observed = ipaddress.ip_address(address)
    if requested.version == observed.version:
        if requested == observed:
            return "exact"
        if requested.is_unspecified or observed.is_unspecified:
            return "wildcard"
        return None
    v6 = requested if requested.version == 6 else observed
    v4 = observed if observed.version == 4 else requested
    if isinstance(v6, ipaddress.IPv6Address):
        if v6.is_unspecified or v6.ipv4_mapped == v4:
            return "potential_ipv6"
        if v4.is_unspecified and v6.ipv4_mapped is not None:
            return "potential_ipv6"
    return None


class LinuxDiagnostics:
    def __init__(self, proc: Path = Path("/proc"), host_ref: str = "local-host") -> None:
        self.proc = proc
        self.host_ref = host_ref
        self.clock_ticks = os.sysconf("SC_CLK_TCK")

    def scope(self) -> Scope:
        scope = Scope(
            host_ref=self.host_ref,
            boot_id=read_bounded(self.proc / "sys/kernel/random/boot_id", 128).decode().strip(),
            pid_namespace_ref=os.readlink(self.proc / "self/ns/pid"),
            network_namespace_ref=os.readlink(self.proc / "self/ns/net"),
        )
        # Reject known container contexts, including the host-networked MCP
        # container. PID 1's namespace links require ptrace privileges on some
        # hosts, so do not require them for ordinary unprivileged host reads.
        if self.process(1).name != "systemd" or (
            self.proc == Path("/proc")
            and (Path("/.dockerenv").exists() or Path("/run/systemd/container").exists())
        ):
            raise ReadFailure("HOST_SCOPE_UNAVAILABLE")
        for namespace, expected in (
            ("pid", scope.pid_namespace_ref),
            ("net", scope.network_namespace_ref),
        ):
            try:
                observed = os.readlink(self.proc / f"1/ns/{namespace}")
            except PermissionError:
                continue
            if observed != expected:
                raise ReadFailure("HOST_SCOPE_UNAVAILABLE")
        return scope

    def process(self, pid: int) -> Process:
        if pid < 1:
            raise ReadFailure("PROCESS_UNAVAILABLE")
        text = read_bounded(self.proc / str(pid) / "stat").decode(errors="replace")
        left, right = text.find("("), text.rfind(")")
        if left < 1 or right <= left or int(text[:left].strip()) != pid:
            raise ReadFailure("PROCESS_STAT_INVALID")
        fields = text[right + 1 :].split()
        if len(fields) < 20:
            raise ReadFailure("PROCESS_STAT_INVALID")
        return Process(
            pid, redact(text[left + 1 : right], 64), fields[0], int(fields[1]), int(fields[19])
        )

    def control_groups(self, pid: int) -> list[ControlGroup]:
        text = read_bounded(self.proc / str(pid) / "cgroup", 4096).decode()
        groups = []
        for line in text.splitlines():
            hierarchy, controllers, path = line.split(":", 2)
            groups.append(
                ControlGroup(
                    hierarchy_id=int(hierarchy),
                    controllers=controllers.split(",") if controllers else [],
                    path=path,
                )
            )
        if len(groups) > 8:
            raise ReadFailure("CGROUP_LIMIT_EXCEEDED")
        return groups

    def started_at(self, start_ticks: int) -> datetime:
        text = read_bounded(self.proc / "stat", 65536).decode()
        match = re.search(r"^btime (\d+)$", text, re.MULTILINE)
        if match is None:
            raise ReadFailure("BOOT_TIME_UNAVAILABLE")
        return datetime.fromtimestamp(int(match[1]) + start_ticks / self.clock_ticks, UTC)

    def path_metadata(self, pid: int, name: Literal["exe", "cwd"]) -> str:
        value = os.readlink(self.proc / str(pid) / name)
        if len(value) > 1024:
            raise ReadFailure("PROCESS_PATH_LIMIT")
        return redact(value, 1024)

    def command_summary(self, pid: int) -> CommandSummary:
        # Only the executable basename and a conventional interpreter script basename.
        # All other arguments, including flags and positional credentials, are omitted.
        raw = read_bounded(self.proc / str(pid) / "cmdline", 16384)
        parts = raw.decode(errors="replace").split("\x00")
        executable = redact(Path(self.path_metadata(pid, "exe")).name, 128)
        script = None
        if executable and re.fullmatch(
            r"node|nodejs|python(?:\d(?:\.\d+)?)?|ruby|perl", executable
        ):
            if len(parts) > 1 and re.fullmatch(r"[\w./ -]+\.(?:m?js|cjs|py|rb|pl)", parts[1]):
                script = redact(Path(parts[1]).name, 128)
        return CommandSummary(
            executable_name=executable, script_name=script, arguments_disclosed=False
        )

    def _pids(self, deadline: float) -> tuple[list[int], bool]:
        pids: list[int] = []
        with os.scandir(self.proc) as entries:
            for entry in entries:
                if time.monotonic() >= deadline:
                    return sorted(pids), True
                if entry.name.isdigit():
                    pids.append(int(entry.name))
                    if len(pids) >= MAX_SCAN_PIDS:
                        return sorted(pids), True
        return sorted(pids), False

    def _hidden_processes(self) -> bool:
        # hidepid can remove whole proc directories, so successful reads alone cannot
        # establish complete owner enumeration. fail closed if mount facts are hidden.
        text = read_bounded(self.proc / "mounts", 65536).decode()
        return any(
            len(parts := line.split()) >= 4
            and parts[2] == "proc"
            and any(
                option.startswith("hidepid=") and option not in {"hidepid=0", "hidepid=off"}
                for option in parts[3].split(",")
            )
            for line in text.splitlines()
        )

    def unit_processes(self, control_group: str, main_pid: int, deadline: float) -> ProcessScan:
        scan = ProcessScan()
        try:
            if self._hidden_processes():
                scan.issues.append(issue("PROCESS_VISIBILITY_RESTRICTED", "processes"))
            pids, scan.truncated = self._pids(deadline)
        except (OSError, ValueError, ReadFailure) as exc:
            scan.issues.append(issue(error_code(exc, "PROCESS"), "processes"))
            return scan
        if main_pid in pids:
            pids.remove(main_pid)
            pids.insert(0, main_pid)
        for pid in pids:
            if time.monotonic() >= deadline:
                scan.truncated = True
                break
            try:
                groups = self.control_groups(pid)
                if not in_unit(groups, control_group):
                    continue
                process = self.process(pid)
                if (
                    self.process(pid).start_ticks != process.start_ticks
                    or self.control_groups(pid) != groups
                ):
                    scan.changed = True
                    continue
                if len(scan.processes) == MAX_PROCESSES:
                    scan.truncated = True
                    break
                scan.processes.append(process)
            except FileNotFoundError:
                scan.changed = True
            except (OSError, ValueError, ReadFailure) as exc:
                scan.issues.append(issue(error_code(exc, "PROCESS"), "processes"))
        return scan

    def listeners(self, endpoint: DiagnosticEndpoint, deadline: float) -> SocketScan:
        scan = SocketScan()
        for table, family in (("tcp", "ipv4"), ("tcp6", "ipv6")):
            try:
                # Streaming avoids copying the host's full socket table into memory.
                with (self.proc / "net" / table).open("rb") as stream:
                    consumed = 0
                    next(stream, b"")
                    for line in stream:
                        consumed += len(line)
                        if consumed > 4_194_304 or time.monotonic() >= deadline:
                            scan.truncated = True
                            break
                        parts = line.split()
                        if len(parts) < 10:
                            raise ReadFailure("SOCKET_TABLE_INVALID")
                        if parts[3] != b"0A":
                            continue
                        encoded, raw_port = parts[1].split(b":")
                        port = int(raw_port, 16)
                        if port != endpoint.port:
                            continue
                        raw = bytes.fromhex(encoded.decode())
                        if sys.byteorder == "little":
                            raw = b"".join(raw[i : i + 4][::-1] for i in range(0, len(raw), 4))
                        address = str(ipaddress.ip_address(raw))
                        overlap = endpoint_overlap(endpoint, address)
                        if overlap is None:
                            continue
                        inode = int(parts[9])
                        if inode < 1:
                            raise ReadFailure("SOCKET_ID_UNAVAILABLE")
                        if len(scan.sockets) >= MAX_LISTENERS:
                            scan.truncated = True
                            break
                        scan.sockets.append(Socket(inode, family, address, port, overlap))  # type: ignore[arg-type]
                scan.tables_read += 1
            except FileNotFoundError:
                # tcp6 legitimately does not exist when IPv6 is disabled.
                if table == "tcp6" and not (self.proc / "sys/net/ipv6").exists():
                    scan.tables_read += 1
                    continue
                scan.issues.append(issue("SOCKET_ENUMERATION_UNAVAILABLE", "listeners"))
            except (OSError, ValueError, ReadFailure) as exc:
                scan.issues.append(issue(error_code(exc, "SOCKET_ENUMERATION"), "listeners"))
        return scan

    def socket_fds(
        self,
        pid: int,
        inodes: set[int],
        deadline: float,
        *,
        task_budget: int = MAX_SCAN_TASKS,
        fd_budget: int = MAX_SCAN_FDS,
    ) -> FdScan:
        """Inspect every visible task's descriptors, including unshared FD tables."""
        scan = FdScan()
        try:
            with os.scandir(self.proc / str(pid) / "task") as tasks:
                for task in tasks:
                    if not task.name.isdigit():
                        continue
                    if scan.tasks >= task_budget or time.monotonic() >= deadline:
                        scan.truncated = True
                        break
                    scan.tasks += 1
                    try:
                        with os.scandir(Path(task.path) / "fd") as fds:
                            for fd in fds:
                                if scan.descriptors >= fd_budget or time.monotonic() >= deadline:
                                    scan.truncated = True
                                    break
                                scan.descriptors += 1
                                try:
                                    target = os.readlink(fd.path)
                                except FileNotFoundError:
                                    scan.changed = True
                                    continue
                                match = re.fullmatch(r"socket:\[(\d+)\]", target)
                                if match and (inode := int(match[1])) in inodes:
                                    scan.matches[inode] = Path(fd.path)
                    except FileNotFoundError:
                        scan.changed = True
                    except (OSError, ValueError, ReadFailure) as exc:
                        scan.issues.append(issue(error_code(exc, "OWNER"), "listeners.owners"))
                    if scan.truncated:
                        break
        except FileNotFoundError:
            scan.changed = True
        except (OSError, ValueError, ReadFailure) as exc:
            scan.issues.append(issue(error_code(exc, "OWNER"), "listeners.owners"))
        # Confirm selected descriptors after the scan; a disappearing task cannot
        # establish a current process holder merely from an earlier FD observation.
        for inode, path in list(scan.matches.items()):
            try:
                if os.readlink(path) == f"socket:[{inode}]":
                    continue
                scan.changed = True
            except FileNotFoundError:
                scan.changed = True
            except OSError as exc:
                scan.issues.append(issue(error_code(exc, "OWNER"), "listeners.owners"))
            del scan.matches[inode]
        return scan

    def socket_owners(self, inodes: set[int], deadline: float) -> OwnerScan:
        scan = OwnerScan(owners={inode: [] for inode in inodes})
        if not inodes:
            return scan
        try:
            if self._hidden_processes():
                scan.issues.append(issue("OWNER_VISIBILITY_RESTRICTED", "listeners.owners"))
            pids, scan.truncated = self._pids(deadline)
        except (OSError, ValueError, ReadFailure) as exc:
            scan.issues.append(issue(error_code(exc, "OWNER"), "listeners.owners"))
            return scan
        visited_fds = 0
        visited_tasks = 0
        for pid in pids:
            if (
                time.monotonic() >= deadline
                or visited_fds >= MAX_SCAN_FDS
                or visited_tasks >= MAX_SCAN_TASKS
            ):
                scan.truncated = True
                break
            try:
                process = self.process(pid)
                fds = self.socket_fds(
                    pid,
                    inodes,
                    deadline,
                    task_budget=MAX_SCAN_TASKS - visited_tasks,
                    fd_budget=MAX_SCAN_FDS - visited_fds,
                )
                visited_tasks += fds.tasks
                visited_fds += fds.descriptors
                scan.issues.extend(fds.issues)
                scan.changed |= fds.changed
                scan.truncated |= fds.truncated
                if not fds.matches:
                    continue
                if self.process(pid).start_ticks != process.start_ticks:
                    scan.changed = True
                    continue
                for inode in fds.matches:
                    if len(scan.owners[inode]) >= MAX_OWNERS:
                        scan.truncated = True
                    else:
                        scan.owners[inode].append(process)
            except FileNotFoundError:
                scan.changed = True
            except (OSError, ValueError, ReadFailure) as exc:
                scan.issues.append(issue(error_code(exc, "OWNER"), "listeners.owners"))
        for owners in scan.owners.values():
            if not owners:
                scan.issues.append(issue("OWNER_NOT_RESOLVED", "listeners.owners", retryable=True))
                break
        return scan


def manager_cgroup_paths(groups: list[ControlGroup]) -> list[str]:
    """Systemd ownership uses the unified hierarchy or v1's named systemd hierarchy."""
    return [
        group.path
        for group in groups
        if (group.hierarchy_id == 0 and not group.controllers)
        or "name=systemd" in group.controllers
    ]


def in_unit(groups: list[ControlGroup], control_group: str) -> bool:
    # Never treat the root cgroup or a textual prefix (foo.service-extra) as ownership.
    return bool(control_group and control_group != "/") and any(
        group == control_group or group.startswith(control_group.rstrip("/") + "/")
        for group in manager_cgroup_paths(groups)
    )
