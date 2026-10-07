"""Privileged operation helper: narrow root-owned boundary over a Unix socket.

This module runs as a separate root process. It accepts only typed operation
requests over a local Unix socket, verifies the calling process identity,
validates exact unit names, enforces operation and target allowlists, and
executes a fixed set of systemctl verbs with no free-form arguments.

The web application (unprivileged) must never reach this logic in-process; it
talks to it over the socket. Tests exercise the same policy through an
in-process policy evaluator.
"""

from __future__ import annotations

import asyncio
import configparser
import json
import logging
import os
import pwd
import re
import socket
import stat
import struct
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger("hyperion.ops.helper")

# The only operations the helper will ever perform. Each maps to one fixed
# argv tail; no flags, paths, commands, or shell syntax can be injected.
# `write-unit` and `remove-unit` are handled without systemctl (managed
# namespace only); `daemon-reload` runs a fixed target-less verb.
ALLOWED_OPERATIONS: dict[str, tuple[str, ...]] = {
    "start": ("start",),
    "stop": ("stop",),
    "restart": ("restart",),
    "enable": ("enable",),
    "disable": ("disable",),
    "trigger": ("start",),
    "enable-now": ("enable", "--now"),
}

# Operations that require the target to be explicitly allowlisted; everything
# else is governed by protected-unit policy + inventory derivation.
_STRONG_OPERATIONS = {"start", "stop", "restart", "enable", "disable", "enable-now", "trigger"}

# Access-critical units that are protected by default. Enforced independently
# of the web application: even a correctly-shaped request is refused here.
PROTECTED_UNITS = frozenset(
    {
        "ssh.service", "sshd.service", "ssh.socket",
        "systemd-networkd.service", "NetworkManager.service", "networking.service",
        "network.service",
        "docker.service",
        "caddy.service", "hyperion.service", "hyperion-ops.service", "hyperion-ops.timer",
        "authentik-server.service", "authentik-worker.service",
    }
)

# Exact unit names the operator may control after explicit curation. The web
# layer supplies this allowlist from server state; the helper still validates
# shape and applies the protected-unit override.
_ALLOWED_UNIT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]*\.service$")

# Hyperion-managed schedules live in a dedicated allowlisted namespace. The
# namespace itself is the allowlist: names are shape-constrained and the files
# are only ever written directly under the managed systemd directory.
_MANAGED_UNIT_PATTERN = re.compile(r"^hyperion-[a-z][a-z0-9-]*\.(service|timer)$")
_DEFAULT_MANAGED_DIR = Path("/etc/systemd/system")
_MAX_UNIT_BYTES = 16 * 1024

_OPERATION_TYPES = {
    "start", "stop", "restart", "enable", "disable", "trigger",
    "enable-now", "write-unit", "remove-unit", "daemon-reload",
}

# The uid under which Hyperion's web process runs. The helper refuses
# requests from any other caller. Overridable per deployment via the
# `--caller-uid` CLI flag on `hyperion.ops.server`.
_EXPECTED_CALLER_UID = 1000


class OperationDenied(RuntimeError):
    """A typed request was refused by helper policy."""


class HelperPolicy:
    """Pure policy used by both the socket server and the test harness."""

    def __init__(self, allowed_units: set[str] | None = None) -> None:
        self._allowed_units = set(allowed_units or ())

    def allow_units(self, units: set[str]) -> None:
        self._allowed_units.update(units)

    def evaluate(
        self, unit: str, operation: str
    ) -> tuple[bool, str | None]:
        """Return (allowed, reason). The helper's word is final."""
        if operation not in _OPERATION_TYPES:
            return False, f"unknown operation {operation!r}"
        if unit in PROTECTED_UNITS:
            return False, "unit is protected by Hyperion policy"
        if operation == "daemon-reload":
            return True, None
        managed = _MANAGED_UNIT_PATTERN.fullmatch(unit) is not None
        if operation in ("write-unit", "remove-unit", "enable-now"):
            if not managed:
                return False, "unit is not in the managed namespace"
            return True, None
        if not _ALLOWED_UNIT_PATTERN.fullmatch(unit) and not managed:
            return False, "invalid unit name"
        if unit in PROTECTED_UNITS:
            return False, "unit is protected by Hyperion policy"
        if (
            operation in _STRONG_OPERATIONS
            and unit not in self._allowed_units
            and not managed
        ):
            return False, "unit is not allowlisted for this operation"
        if operation == "trigger":
            # Trigger is `start` on a unit whose timer relationship is
            # confirmed by the web layer; the helper only needs the shape.
            if not unit.endswith(".service"):
                return False, "trigger requires a service unit"
        return True, None

    def systemctl_argv(self, operation: str) -> tuple[str, ...]:
        tail = ALLOWED_OPERATIONS[operation]
        return ("systemctl",) + tail


ExecRunner = Callable[[list[str]], Coroutine[Any, Any, tuple[int, bytes, bytes]]]


async def execute_operation(
    unit: str,
    operation: str,
    *,
    executor: ExecRunner | None = None,
    allowed_units: set[str] | None = None,
    managed_dir: Path | None = None,
    content: str | None = None,
) -> dict[str, object]:
    """Validate policy and execute the typed operation.

    Systemctl verbs run one fixed argv tail; `write-unit` and `remove-unit`
    operate only inside the managed unit-file namespace without running
    systemctl; `daemon-reload` runs the fixed target-less verb.
    """
    policy = HelperPolicy(allowed_units)
    allowed, reason = policy.evaluate(unit, operation)
    if not allowed:
        raise OperationDenied(reason or "denied")
    target_dir = managed_dir if managed_dir is not None else _DEFAULT_MANAGED_DIR
    if operation == "write-unit":
        return _write_unit_result(unit, content, target_dir)
    if operation == "remove-unit":
        return _remove_unit_result(unit, target_dir)
    if operation == "daemon-reload":
        argv: tuple[str, ...] = ("systemctl", "daemon-reload")
    else:
        argv = ("systemctl",) + ALLOWED_OPERATIONS[operation] + (unit,)
    runner = executor or _systemctl_runner
    returncode, stdout, stderr = await runner(list(argv))
    return {
        "unit": unit,
        "operation": operation,
        "argv": list(argv),
        "returncode": returncode,
        "stdout": stdout.decode("utf-8", errors="replace")[:512],
        "stderr": stderr.decode("utf-8", errors="replace")[:512],
        "error": stderr.decode("utf-8", errors="replace")[:240] if returncode else "",
        "ok": returncode == 0,
    }


def _write_unit_result(
    unit: str, content: str | None, managed_dir: Path
) -> dict[str, object]:
    def base(ok: bool, stderr: str) -> dict[str, object]:
        return {
            "unit": unit,
            "operation": "write-unit",
            "argv": ["write-unit", unit],
            "returncode": 0 if ok else 1,
            "stdout": "",
            "stderr": stderr,
            "ok": ok,
            "denied": not ok,
        }

    if content is None:
        return base(False, "write-unit requires content")
    try:
        _validate_unit_content(content, unit)
    except OperationDenied as exc:
        return base(False, str(exc))
    target = managed_dir / unit
    try:
        managed_dir.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        if unit.endswith(".service"):
            content += "\n[Service]\nNoNewPrivileges=yes\nCapabilityBoundingSet=\nAmbientCapabilities=\nProtectSystem=strict\nProtectHome=yes\nPrivateTmp=yes\nPrivateDevices=yes\nProtectKernelTunables=yes\nProtectKernelModules=yes\nProtectControlGroups=yes\nRestrictSUIDSGID=yes\nRestrictNamespaces=yes\nInaccessiblePaths=/run/docker.sock /run/hyperion /run/docker-observer\n"
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        return base(False, str(exc))
    return base(True, "")


def _remove_unit_result(unit: str, managed_dir: Path) -> dict[str, object]:
    target = managed_dir / unit
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:
        return {
            "unit": unit,
            "operation": "remove-unit",
            "argv": ["remove-unit", unit],
            "returncode": 1,
            "stdout": "",
            "stderr": str(exc),
            "ok": False,
            "denied": True,
        }
    return {
        "unit": unit,
        "operation": "remove-unit",
        "argv": ["remove-unit", unit],
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "ok": True,
    }


def _validate_unit_content(content: str, unit: str = "hyperion-task.service") -> None:
    """Accept only the scheduler schema, running as an isolated unprivileged user."""
    if len(content.encode()) > _MAX_UNIT_BYTES or any(c in content for c in ("\x00", "\r", "\\", "%")):
        raise OperationDenied("invalid unit content")
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(content)
    except configparser.Error as exc:
        raise OperationDenied("invalid unit syntax") from exc
    allowed = {
        "Unit": {"Description", "After"},
        "Service": {"Type", "User", "WorkingDirectory", "ExecStart", "TimeoutStartSec"},
        "Timer": {"OnCalendar", "Persistent", "Unit", "RandomizedDelaySec", "AccuracySec"},
        "Install": {"WantedBy"},
    }
    for section in parser:
        if section == "DEFAULT":
            if parser.defaults():
                raise OperationDenied("default directives are forbidden")
            continue
        if section not in allowed or set(parser[section]) - allowed[section]:
            raise OperationDenied("directive not permitted by scheduler policy")
        if any("\n" in v for v in parser[section].values()):
            raise OperationDenied("multiline directives are forbidden")
    if unit.endswith(".service"):
        if set(parser.sections()) != {"Unit", "Service"}:
            raise OperationDenied("invalid service sections")
        service = parser["Service"]
        if service.get("User") != "hyperion-jobs" or service.get("Type") != "oneshot":
            raise OperationDenied("scheduled commands must run as hyperion-jobs (unprivileged)")
        if not service.get("ExecStart", "").startswith("/"):
            raise OperationDenied("an absolute executable path is required")
    else:
        if set(parser.sections()) != {"Unit", "Timer", "Install"}:
            raise OperationDenied("invalid timer sections")
        if parser["Timer"].get("Unit") != unit.removesuffix(".timer") + ".service":
            raise OperationDenied("timer must reference its own managed service")
        if parser["Install"].get("WantedBy") != "timers.target":
            raise OperationDenied("invalid timer installation target")


async def _systemctl_runner(argv: list[str]) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(10.0):
            stdout, stderr = await process.communicate()
    except (TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise
    return process.returncode or 0, stdout, stderr


# --- Unix socket server ------------------------------------------------------


async def serve_ops_socket(
    path: Path,
    allowed_units: set[str] | None = None,
    caller_uid: int = _EXPECTED_CALLER_UID,
    managed_dir: Path | None = None,
) -> None:
    """Run the privileged helper over a Unix socket (root-owned, 0660).

    The socket is created with restrictive ownership and mode, peer identity is
    checked against the configured web-process UID on every request, and each
    request is a single JSON object on one line. Responses are one JSON object
    on one line.
    """
    policy = HelperPolicy(allowed_units)
    await asyncio.to_thread(_prepare_socket, path)

    server = await asyncio.start_unix_server(
        lambda r, w: _handle_client(r, w, policy, caller_uid, managed_dir),
        path=str(path),
    )
    _harden_socket(path, caller_uid)
    logger.info("ops helper listening on %s", path)
    try:
        async with server:
            await server.serve_forever()
    finally:
        await asyncio.to_thread(_remove_socket, path)


def _remove_socket(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _prepare_socket(path: Path) -> None:
    if path.exists():
        path.unlink()
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)


def _harden_socket(path: Path, caller_uid: int) -> None:
    """Restrict the bound socket to root:<web-group> rw and deny everyone else."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)
    except OSError:
        logger.warning("could not chmod %s", path)
    if os.geteuid() == 0:
        try:
            gid = pwd.getpwuid(caller_uid).pw_gid
        except KeyError:
            logger.warning("no group for caller uid %d", caller_uid)
            return
        try:
            os.chown(path, 0, gid)
        except OSError:
            logger.warning("could not chown %s to uid 0 gid %d", path, gid)


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    policy: HelperPolicy,
    caller_uid: int,
    managed_dir: Path | None,
) -> None:
    sock = writer.get_extra_info("socket")
    peer = _peer_identity(sock)
    try:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        except (TimeoutError, ConnectionError):
            return
        if not line:
            return
        try:
            request = json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            await _respond(writer, {"ok": False, "error": "malformed request"})
            return
        unit = request.get("unit")
        operation = request.get("operation")
        content = request.get("content")
        if not isinstance(unit, str) or not isinstance(operation, str):
            await _respond(writer, {"ok": False, "error": "malformed request"})
            return
        if content is not None and not isinstance(content, str):
            await _respond(writer, {"ok": False, "error": "malformed request"})
            return
        if peer is not None and peer != caller_uid:
            await _respond(writer, {"ok": False, "error": "caller not permitted"})
            return
        allowed, reason = policy.evaluate(unit, operation)
        if not allowed:
            await _respond(
                writer, {"ok": False, "error": reason or "denied", "denied": True}
            )
            return
        try:
            result = await execute_operation(
                unit,
                operation,
                executor=_systemctl_runner,
                allowed_units=None,
                managed_dir=managed_dir,
                content=content,
            )
        except OperationDenied as exc:
            await _respond(writer, {"ok": False, "error": str(exc), "denied": True})
            return
        except Exception as exc:  # helper boundary
            await _respond(writer, {"ok": False, "error": str(exc)[:240]})
            return
        result["ok"] = bool(result["returncode"] == 0)
        await _respond(writer, result)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


def _peer_identity(sock: socket.socket | None) -> int | None:
    """Return the connecting process UID from SO_PEERCRED, or None if unknown.

    SO_PEERCRED is a `struct ucred` (pid, uid, gid); the buffer length must be
    requested explicitly or getsockopt returns only the first word (the PID).
    """
    if sock is None:
        return None
    try:
        buf = sock.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
        )
        _pid, uid, _gid = cast(tuple[int, int, int], struct.unpack("3i", buf))
        return uid
    except (OSError, TypeError, struct.error):
        return None


async def _respond(writer: asyncio.StreamWriter, payload: dict[str, object]) -> None:
    writer.write(json.dumps(payload).encode("utf-8") + b"\n")
    await writer.drain()


# --- Client side (unprivileged web process) ---------------------------------


class OperationHelperClient:
    """Socket client used by the web process; mirrors the server policy for
    early rejection but never trusts the server's word for authorization."""

    def __init__(self, socket_path: Path, timeout: float = 5.0) -> None:
        self._path = socket_path
        self._timeout = timeout

    async def request(
        self, unit: str, operation: str, content: str | None = None
    ) -> dict[str, object]:
        payload = json.dumps(
            {"unit": unit, "operation": operation}
            if content is None
            else {"unit": unit, "operation": operation, "content": content}
        )
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(self._path)), timeout=self._timeout
        )
        try:
            writer.write(payload.encode("utf-8") + b"\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=self._timeout)
        finally:
            writer.close()
            await writer.wait_closed()
        if not line:
            return {"ok": False, "error": "empty helper response"}
        try:
            parsed = json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {"ok": False, "error": "malformed helper response"}
        if not isinstance(parsed, dict):
            return {"ok": False, "error": "malformed helper response"}
        return {str(k): v for k, v in parsed.items()}

    def evaluate(self, unit: str, operation: str) -> tuple[bool, str | None]:
        """Local mirror of helper policy for early UI feedback."""
        return HelperPolicy().evaluate(unit, operation)
