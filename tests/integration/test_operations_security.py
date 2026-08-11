"""Security boundary for the operations-console write surface (plan 11.1).

Covers unit-name and command injection, forged identity headers, cross-site
writes, duplicate submissions, stale-state operations, protected-unit
operations, and oversize/manifested payload boundaries.
"""

from __future__ import annotations


def _post(client, path, body=None, idempotency=None, origin="http://testserver", identity="owner"):
    headers = {"X-Authentik-Username": identity}
    if idempotency is not None:
        headers["Idempotency-Key"] = idempotency
    if origin is not None:
        headers["Origin"] = origin
    return client.post(path, headers=headers, json=body or {})


def test_unit_name_injection_rejected(client) -> None:
    for evil in (
        "t3code.service; rm -rf /",
        "t3code.service && id",
        "../etc/passwd.service",
        "t3code.service|cat /etc/shadow",
        "$(touch /tmp/pwned)",
    ):
        response = _post(
            client,
            f"/api/v1/units/{evil}/operations",
            body={"operation": "restart", "expectedState": None, "reason": ""},
            idempotency=f"inj-{abs(hash(evil))}",
        )
        # Either 404 (path rejected) or 200 with denied (policy) — never a shell run.
        assert response.status_code in (200, 404), evil
        if response.status_code == 200:
            assert response.json()["state"] == "denied", evil


def test_operation_enum_rejects_arbitrary_verbs(client) -> None:
    response = _post(
        client,
        "/api/v1/units/t3code.service/operations",
        body={"operation": "systemctl", "expectedState": None, "reason": ""},
        idempotency="verb-00000001",
    )
    assert response.status_code == 422


def test_forged_identity_header_denied(client) -> None:
    response = _post(
        client,
        "/api/v1/units/t3code.service/operations",
        body={"operation": "restart", "expectedState": None, "reason": ""},
        idempotency="forged-000001",
        identity="",
    )
    assert response.status_code == 401


def test_no_origin_on_write_requires_same_origin(client) -> None:
    # The Origin header is optional for same-origin navigation but a
    # cross-origin request with a mismatched host is refused.
    response = _post(
        client,
        "/api/v1/units/t3code.service/operations",
        body={"operation": "restart", "expectedState": None, "reason": ""},
        idempotency="origin-0000001",
        origin="http://attacker.invalid",
    )
    assert response.status_code == 403


def test_duplicate_submission_is_idempotent(client) -> None:
    body = {"operation": "restart", "expectedState": "active", "reason": ""}
    first = _post(
        client, "/api/v1/units/t3code.service/operations", body=body, idempotency="dup-00000001"
    )
    second = _post(
        client, "/api/v1/units/t3code.service/operations", body=body, idempotency="dup-00000001"
    )
    assert first.json()["id"] == second.json()["id"]


def test_stale_expected_state_refused(client) -> None:
    response = _post(
        client,
        "/api/v1/units/t3code.service/operations",
        body={"operation": "restart", "expectedState": "inactive", "reason": ""},
        idempotency="stale-0000001",
    )
    assert response.status_code == 200
    assert response.json()["state"] == "denied"
    assert "stale" in response.json()["message"].lower()


def test_protected_units_cannot_be_operated(client) -> None:
    for unit in ("docker.service", "caddy.service", "ssh.service"):
        response = _post(
            client,
            f"/api/v1/units/{unit}/operations",
            body={"operation": "restart", "expectedState": "active", "reason": ""},
            idempotency=f"prot-{unit}",
        )
        assert response.json()["state"] == "denied", unit
        assert "protected" in response.json()["message"], unit


def test_oversized_reason_rejected(client) -> None:
    response = _post(
        client,
        "/api/v1/units/t3code.service/operations",
        body={"operation": "restart", "expectedState": None, "reason": "x" * 5000},
        idempotency="huge-00000001",
    )
    assert response.status_code == 422


def test_managed_schedule_rejects_shell_and_paths(client) -> None:
    body = {
        "name": "evil-schedule",
        "description": "",
        "onCalendar": "*-*-* 02:00:00",
        "executable": "/bin/sh",
        "arguments": ["$(rm -rf /)"],
        "user": "root",
        "workingDirectory": None,
        "timeoutSeconds": 60,
        "overlapPolicy": "drop",
        "missedRunBehavior": "ignore",
        "relatedService": None,
    }
    response = _post(client, "/api/v1/schedules", body=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SCHEDULE_INVALID"


def test_api_never_leaks_environment_values(client) -> None:
    for path in ("/api/v1/host", "/api/v1/units", "/api/v1/schedules", "/api/v1/activity"):
        response = client.get(path, headers={"X-Authentik-Username": "owner"})
        assert response.status_code == 200, path
        assert "HYPERION_" not in response.text, path
        assert "Password" not in response.text and "Secret" not in response.text, path
