"""Shared pytest fixtures."""

from __future__ import annotations

import ipaddress
import sys
from pathlib import Path

import pytest

from hyperion.catalog import load_catalog
from hyperion.diagnostics.linux import LinuxDiagnostics
from hyperion.diagnostics.provider import DiagnosticsProvider
from hyperion.providers.systemd import SystemdProvider

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_catalog_path() -> Path:
    return FIXTURES / "fixture-services.yaml"


class DiagnosticsHost:
    """A synthetic procfs and manager, used across provider/API/MCP acceptance tests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.linux = LinuxDiagnostics(root)
        self.service = load_catalog(FIXTURES / "fixture-services.yaml").services[0]
        self.properties = {
            "Id": "t3code.service",
            "LoadState": "loaded",
            "ActiveState": "active",
            "SubState": "running",
            "MainPID": "11",
            "NRestarts": "7",
            "ControlGroup": "/system.slice/t3code.service",
            "Result": "success",
            "ExecMainPID": "11",
            "ExecMainCode": "0",
            "ExecMainStatus": "0",
            "ExecMainStartTimestamp": "Tue 2026-10-06 12:00:00.000001 UTC",
            "ExecMainExitTimestamp": "0",
            "ExecMainStartTimestampMonotonic": "1000000",
            "ExecMainExitTimestampMonotonic": "0",
        }
        self.commands: list[list[str]] = []
        self.sockets: list[tuple[str, int, int]] = []
        for folder in ("self/ns", "sys/kernel/random", "net", "sys/net/ipv6"):
            (root / folder).mkdir(parents=True)
        (root / "sys/kernel/random/boot_id").write_text("11111111-1111-4111-8111-111111111111")
        (root / "self/ns/pid").symlink_to("pid:[123]")
        (root / "self/ns/net").symlink_to("net:[456]")
        (root / "stat").write_text("btime 1700000000\n")
        (root / "mounts").write_text("proc /proc proc rw,relatime 0 0\n")
        self.write_sockets()
        self.add_process(1, "/system.slice/init.scope", name="systemd", parent=0)
        (root / "1/ns").mkdir()
        (root / "1/ns/pid").symlink_to("pid:[123]")
        (root / "1/ns/net").symlink_to("net:[456]")
        self.add_process(11, "/system.slice/t3code.service", name="launcher", parent=1)
        self.provider = DiagnosticsProvider(self.linux, SystemdProvider(self.execute))

    async def execute(self, argv: list[str]) -> tuple[int, bytes, bytes]:
        self.commands.append(argv)
        assert argv[:4] == ["systemctl", "show", "t3code.service", "--no-pager"]
        return 0, "\n".join(f"{k}={v}" for k, v in self.properties.items()).encode(), b""

    def add_process(
        self,
        pid: int,
        group: str,
        *,
        name: str = "node-MainThread",
        parent: int = 11,
        ticks: int = 100,
        inodes: tuple[int, ...] = (),
        command: bytes = b"node\0/srv/app/main.js\0",
    ) -> None:
        folder = self.root / str(pid)
        folder.mkdir(exist_ok=True)
        fields = ["S", str(parent)] + ["0"] * 17 + [str(ticks)] + ["0"] * 3
        (folder / "stat").write_text(f"{pid} ({name}) " + " ".join(fields))
        (folder / "cgroup").write_text(f"0::{group}\n")
        (folder / "cmdline").write_bytes(command)
        (folder / "fd").mkdir(exist_ok=True)
        task = folder / "task" / str(pid)
        task.mkdir(parents=True, exist_ok=True)
        if not (task / "fd").is_symlink():
            (task / "fd").symlink_to("../../fd")
        for link, target in (("exe", "/usr/bin/node"), ("cwd", "/srv/app")):
            (folder / link).unlink(missing_ok=True)
            (folder / link).symlink_to(target)
        for index, inode in enumerate(inodes):
            link = folder / "fd" / str(index)
            link.unlink(missing_ok=True)
            link.symlink_to(f"socket:[{inode}]")

    def add_socket(self, address: str = "127.0.0.1", inode: int = 77, port: int = 3773) -> None:
        self.sockets.append((address, inode, port))
        self.write_sockets()

    def write_sockets(self) -> None:
        for version, table in ((4, "tcp"), (6, "tcp6")):
            lines = [
                "sl local_address rem_address st tx_queue rx_queue tr tm retr uid timeout inode"
            ]
            for address, inode, port in self.sockets:
                ip = ipaddress.ip_address(address)
                if ip.version != version:
                    continue
                raw = ip.packed
                if sys.byteorder == "little":
                    raw = b"".join(raw[i : i + 4][::-1] for i in range(0, len(raw), 4))
                lines.append(
                    f"0: {raw.hex()}:{port:04X} 00000000:0000 0A 0:0 00:0 0 1000 0 {inode}"
                )
            (self.root / "net" / table).write_text("\n".join(lines) + "\n")


@pytest.fixture
def diagnostics_host(tmp_path: Path) -> DiagnosticsHost:
    return DiagnosticsHost(tmp_path / "proc")
