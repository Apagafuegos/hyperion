"""Real Unix-socket behavior of the privileged operation helper.

The helper must refuse connections from any caller whose UID is not the one it
was started for (the web process), expose a connectable-but-restricted socket
file, and reach helper policy only for the expected caller.
"""

from __future__ import annotations

import asyncio
import os
import stat
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

from hyperion.ops import OperationHelperClient, execute_operation, serve_ops_socket


async def _start_server(path: Path, **kwargs):
    task = asyncio.create_task(serve_ops_socket(path, **kwargs))
    for _ in range(100):
        if await asyncio.to_thread(os.path.exists, path):
            return task
        await asyncio.sleep(0.02)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    raise RuntimeError("ops socket never appeared")


async def _stop_server(task: asyncio.Task) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError, RuntimeError):
        await task


async def test_ops_socket_refuses_mismatched_caller(tmp_path: Path) -> None:
    sock = tmp_path / "ops.sock"
    task = await _start_server(sock, caller_uid=os.getuid() + 1)
    try:
        client = OperationHelperClient(sock)
        result = await client.request("t3code.service", "restart")
    finally:
        await _stop_server(task)
    assert result["ok"] is False
    assert result["error"] == "caller not permitted"


async def test_ops_socket_reaches_policy_for_matching_caller(tmp_path: Path) -> None:
    sock = tmp_path / "ops.sock"
    task = await _start_server(sock, allowed_units={"t3code.service"}, caller_uid=os.getuid())
    try:
        client = OperationHelperClient(sock)
        result = await client.request("other.service", "restart")
    finally:
        await _stop_server(task)
    assert result["ok"] is False
    assert "not allowlisted" in result["error"]


async def test_ops_socket_file_mode_is_hardened(tmp_path: Path) -> None:
    sock = tmp_path / "ops.sock"
    task = await _start_server(sock, caller_uid=os.getuid())
    try:
        await asyncio.sleep(0.1)
        mode = stat.S_IMODE(os.stat(sock).st_mode)
    finally:
        await _stop_server(task)
    assert mode == 0o660


def test_ops_server_cli_exposes_caller_uid_flag() -> None:
    output = subprocess.run(
        [sys.executable, "-m", "hyperion.ops.server", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--caller-uid" in output.stdout


async def test_ops_socket_write_unit_writes_into_managed_dir(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    sock = tmp_path / "ops.sock"
    task = await _start_server(sock, caller_uid=os.getuid(), managed_dir=managed)
    content = (
        "[Unit]\nDescription=Demo\n\n[Service]\nType=oneshot\nUser=hyperion-jobs\n"
        "ExecStart=/usr/local/bin/demo --force\n"
    )
    try:
        client = OperationHelperClient(sock)
        result = await client.request("hyperion-demo.service", "write-unit", content=content)
    finally:
        await _stop_server(task)
    assert result["ok"] is True
    written = (managed / "hyperion-demo.service").read_text(encoding="utf-8")
    assert written.startswith(content)
    assert "NoNewPrivileges=yes" in written


async def test_ops_socket_write_unit_rejects_foreign_unit(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    sock = tmp_path / "ops.sock"
    task = await _start_server(sock, caller_uid=os.getuid(), managed_dir=managed)
    try:
        client = OperationHelperClient(sock)
        result = await client.request("t3code.service", "write-unit", content="[Unit]\n")
    finally:
        await _stop_server(task)
    assert result["ok"] is False
    assert result["denied"] is True
    assert not (managed / "t3code.service").exists()


async def test_ops_socket_write_unit_rejects_malformed_content(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    sock = tmp_path / "ops.sock"
    task = await _start_server(sock, caller_uid=os.getuid(), managed_dir=managed)
    try:
        client = OperationHelperClient(sock)
        for evil in ("[Unit]\n<garbage>\n", "[Unit]\nExecStart=one\ntwo\n", "[Unit]\0bad\n"):
            result = await client.request("hyperion-bad.service", "write-unit", content=evil)
            assert result["ok"] is False, evil
            assert result["denied"] is True, evil
    finally:
        await _stop_server(task)


async def test_ops_socket_remove_unit_removes_managed_file(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "hyperion-demo.service").write_text("[Unit]\n", encoding="utf-8")
    sock = tmp_path / "ops.sock"
    task = await _start_server(sock, caller_uid=os.getuid(), managed_dir=managed)
    try:
        client = OperationHelperClient(sock)
        result = await client.request("hyperion-demo.service", "remove-unit")
    finally:
        await _stop_server(task)
    assert result["ok"] is True
    assert not (managed / "hyperion-demo.service").exists()


async def test_ops_socket_remove_unit_rejects_foreign_unit(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    victim = managed / "t3code.service"
    victim.write_text("[Unit]\n", encoding="utf-8")
    sock = tmp_path / "ops.sock"
    task = await _start_server(sock, caller_uid=os.getuid(), managed_dir=managed)
    try:
        client = OperationHelperClient(sock)
        result = await client.request("t3code.service", "remove-unit")
    finally:
        await _stop_server(task)
    assert result["ok"] is False
    assert result["denied"] is True
    assert victim.exists()


async def test_ops_socket_enable_now_only_for_managed_units(tmp_path: Path) -> None:
    sock = tmp_path / "ops.sock"
    task = await _start_server(sock, caller_uid=os.getuid(), managed_dir=tmp_path / "m")
    try:
        client = OperationHelperClient(sock)
        denied = await client.request("t3code.service", "enable-now")
        assert denied["ok"] is False
        assert denied["denied"] is True
    finally:
        await _stop_server(task)


def test_execute_operation_daemon_reload_argv() -> None:
    calls: list[list[str]] = []

    async def runner(argv: list[str]):
        calls.append(argv)
        return 0, b"", b""

    result = asyncio.run(execute_operation("", "daemon-reload", executor=runner))
    assert result["ok"] is True
    assert calls == [["systemctl", "daemon-reload"]]


def test_execute_operation_enable_now_argv() -> None:
    calls: list[list[str]] = []

    async def runner(argv: list[str]):
        calls.append(argv)
        return 0, b"", b""

    result = asyncio.run(execute_operation("hyperion-demo.timer", "enable-now", executor=runner))
    assert result["ok"] is True
    assert calls == [["systemctl", "enable", "--now", "hyperion-demo.timer"]]


def test_ops_server_cli_exposes_managed_dir_flag() -> None:
    output = subprocess.run(
        [sys.executable, "-m", "hyperion.ops.server", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--managed-dir" in output.stdout
