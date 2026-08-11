"""Operation coordinator: web request controls and protected-unit policy."""

from __future__ import annotations

from pathlib import Path

from hyperion.models import OperationRequest
from hyperion.ops import HelperPolicy, OperationDenied, execute_operation
from hyperion.services.activity import ActivityStore
from hyperion.services.operations import OperationCoordinator


class FakeHelper:
    """A helper that accepts requests and returns a canned structured result."""

    def __init__(self, *, available: bool = True, denied: bool = False, ok: bool = True) -> None:
        self._available = available
        self._denied = denied
        self._ok = ok
        self.requests: list[tuple[str, str]] = []

    async def request(self, unit: str, operation: str) -> dict[str, object]:
        self.requests.append((unit, operation))
        if not self._available:
            raise ConnectionError("helper unavailable")
        if self._denied:
            return {"ok": False, "error": "denied by policy", "denied": True}
        return {"ok": self._ok, "returncode": 0}


def _coordinator(
    tmp_path: Path,
    helper: FakeHelper,
    allowed: set[str] | None = None,
    protected: set[str] | None = None,
):
    activity = ActivityStore(tmp_path / "activity.db")
    return OperationCoordinator(helper, activity, allowed_units=allowed, protected_units=protected)


def test_invalid_unit_name_is_denied(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, FakeHelper(), allowed={"t3code.service"})
    result = __import__("asyncio").run(
        coordinator.submit(
            "owner",
            "t3code; rm -rf /",
            OperationRequest(operation="restart", expected_state=None, reason=""),
            "idempotency-key-1",
            None,
        )
    )
    assert result.state == "denied"


def test_protected_unit_is_denied(tmp_path: Path) -> None:
    coordinator = _coordinator(
        tmp_path,
        FakeHelper(),
        allowed={"docker.service"},
        protected={"docker.service"},
    )
    result = __import__("asyncio").run(
        coordinator.submit(
            "owner",
            "docker.service",
            OperationRequest(operation="restart", expected_state="active", reason=""),
            "idempotency-key-2",
            "active",
        )
    )
    assert result.state == "denied"
    assert "protected" in result.message


def test_non_allowlisted_unit_denied(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, FakeHelper(), allowed={"other.service"})
    result = __import__("asyncio").run(
        coordinator.submit(
            "owner",
            "t3code.service",
            OperationRequest(operation="restart", expected_state="active", reason=""),
            "idempotency-key-3",
            "active",
        )
    )
    assert result.state == "denied"
    assert "allowlisted" in result.message


def test_stale_expected_state_denied(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, FakeHelper(), allowed={"t3code.service"})
    result = __import__("asyncio").run(
        coordinator.submit(
            "owner",
            "t3code.service",
            OperationRequest(operation="restart", expected_state="inactive", reason=""),
            "idempotency-key-4",
            "active",
        )
    )
    assert result.state == "denied"
    assert "stale" in result.message.lower()


def test_idempotency_returns_stored_result(tmp_path: Path) -> None:
    helper = FakeHelper()
    coordinator = _coordinator(tmp_path, helper, allowed={"t3code.service"})
    request = OperationRequest(operation="restart", expected_state="active", reason="")
    first = __import__("asyncio").run(
        coordinator.submit("owner", "t3code.service", request, "idempotency-key-5", "active")
    )
    second = __import__("asyncio").run(
        coordinator.submit("owner", "t3code.service", request, "idempotency-key-5", "active")
    )
    assert first.state == "success"
    assert second.id == first.id
    assert len(helper.requests) == 1


def test_helper_unavailable(tmp_path: Path) -> None:
    coordinator = _coordinator(
        tmp_path, FakeHelper(available=False), allowed={"t3code.service"}
    )
    result = __import__("asyncio").run(
        coordinator.submit(
            "owner",
            "t3code.service",
            OperationRequest(operation="restart", expected_state="active", reason=""),
            "idempotency-key-6",
            "active",
        )
    )
    assert result.state == "helper_unavailable"


def test_helper_denial_surfaces(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, FakeHelper(denied=True), allowed={"t3code.service"})
    result = __import__("asyncio").run(
        coordinator.submit(
            "owner",
            "t3code.service",
            OperationRequest(operation="restart", expected_state="active", reason=""),
            "idempotency-key-7",
            "active",
        )
    )
    assert result.state == "denied"


def test_activity_records_operation(tmp_path: Path) -> None:
    helper = FakeHelper()
    activity = ActivityStore(tmp_path / "activity.db")
    coordinator = OperationCoordinator(helper, activity, allowed_units={"t3code.service"})
    __import__("asyncio").run(
        coordinator.submit(
            "owner",
            "t3code.service",
            OperationRequest(operation="restart", expected_state="active", reason=""),
            "idempotency-key-8",
            "active",
        )
    )
    records = activity.query(limit=10).records
    assert any(r.kind == "operation" and r.target == "t3code.service" for r in records)
    assert any(r.result == "success" for r in records)


def test_rate_limit(tmp_path: Path) -> None:
    helper = FakeHelper()
    coordinator = _coordinator(tmp_path, helper, allowed={"t3code.service"})
    import asyncio

    request = OperationRequest(operation="restart", expected_state="active", reason="")
    denied_seen = False
    for index in range(30):
        result = asyncio.run(
            coordinator.submit(
                "owner", "t3code.service", request, f"idempotency-key-{index}", "active"
            )
        )
        if result.state == "denied" and "rate limit" in result.message.lower():
            denied_seen = True
            break
    assert denied_seen


def test_helper_policy_rejects_invalid_shapes() -> None:
    policy = HelperPolicy(allowed_units={"t3code.service"})
    assert policy.evaluate("t3code.service", "restart") == (True, None)
    assert policy.evaluate("t3code.service;id", "restart")[0] is False
    assert policy.evaluate("t3code.service", "hack")[0] is False
    assert policy.evaluate("docker.service", "restart")[0] is False
    assert policy.evaluate("ssh.service", "restart")[0] is False


def test_execute_operation_runs_fixed_argv() -> None:
    calls: list[list[str]] = []

    async def runner(argv: list[str]):
        calls.append(argv)
        return 0, b"", b""

    import asyncio

    result = asyncio.run(
        execute_operation(
            "t3code.service", "restart", executor=runner, allowed_units={"t3code.service"}
        )
    )
    assert result["ok"] is True
    assert calls == [["systemctl", "restart", "t3code.service"]]


def test_execute_operation_denies_protected() -> None:
    import asyncio

    with pytest_raises_operation_denied():
        asyncio.run(
            execute_operation("docker.service", "restart", allowed_units={"docker.service"})
        )


def pytest_raises_operation_denied():
    import pytest

    return pytest.raises(OperationDenied)
