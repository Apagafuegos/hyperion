"""Operations-console API resources: host, units, schedules, activity,
operations, and managed schedules over the fixture providers."""

from __future__ import annotations


def _get(client, path, **kwargs):
    headers = kwargs.pop("headers", {})
    headers["X-Authentik-Username"] = "owner"
    return client.get(path, headers=headers, **kwargs)


def _post(client, path, body=None, idempotency=None, origin=None, **kwargs):
    headers = kwargs.pop("headers", {})
    headers["X-Authentik-Username"] = "owner"
    if idempotency is not None:
        headers["Idempotency-Key"] = idempotency
    if origin is not None:
        headers["Origin"] = origin
    return client.post(path, headers=headers, json=body or {}, **kwargs)


def test_host_current_and_history(client) -> None:
    host = _get(client, "/api/v1/host")
    assert host.status_code == 200
    payload = host.json()
    assert payload["hostname"] == "meridian-vps"
    assert payload["cpu"]["utilizationPercent"] is not None
    assert payload["memory"]["usedPercent"] is not None
    assert len(payload["filesystems"]) == 2

    history = _get(client, "/api/v1/host/history?window=30m")
    assert history.status_code == 200
    assert history.json()["windowSeconds"] == 1800
    assert len(history.json()["samples"]) >= 1


def test_host_invalid_window(client) -> None:
    response = _get(client, "/api/v1/host/history?window=banana")
    assert response.status_code == 422


def test_units_inventory(client) -> None:
    response = _get(client, "/api/v1/units")
    assert response.status_code == 200
    payload = response.json()
    assert payload["fresh"] is True
    assert len(payload["units"]) == 7
    by_name = {u["name"]: u for u in payload["units"]}
    assert by_name["docker.service"]["protection"] == "protected"
    assert by_name["t3code.service"]["relatedService"] == "t3-code"


def test_unit_logs(client) -> None:
    response = _get(client, "/api/v1/units/t3code.service/logs?tail=50")
    assert response.status_code == 200
    payload = response.json()
    assert payload["unit"] == "t3code.service"
    assert len(payload["records"]) >= 1


def test_unit_logs_require_inventory_identity(client) -> None:
    response = _get(client, "/api/v1/units/nonexistent.service/logs?tail=50")
    assert response.status_code == 404  # unit must be in the enumerated inventory
    assert response.json()["error"]["code"] == "UNIT_NOT_FOUND"


def test_schedules_index(client) -> None:
    response = _get(client, "/api/v1/schedules")
    assert response.status_code == 200
    payload = response.json()
    sources = {s["source"] for s in payload["schedules"]}
    assert sources == {"systemd", "cron"}
    cron = next(s for s in payload["schedules"] if s["source"] == "cron")
    assert cron["lastResult"] == "not_observed"


def test_schedule_detail(client) -> None:
    response = _get(client, "/api/v1/schedules/backup")
    assert response.status_code == 200
    assert response.json()["source"] == "systemd"
    assert response.json()["target"] == "backup.service"


def test_schedule_not_found(client) -> None:
    response = _get(client, "/api/v1/schedules/nope")
    assert response.status_code == 404


def test_activity_requires_identity(client) -> None:
    assert client.get("/api/v1/activity").status_code == 401


def test_operation_denied_for_protected_unit(client) -> None:
    response = _post(
        client,
        "/api/v1/units/docker.service/operations",
        body={"operation": "restart", "expectedState": "active", "reason": ""},
        idempotency="idem-key-00000001",
        origin="http://testserver",
    )
    assert response.status_code == 200
    assert response.json()["state"] == "denied"
    assert "protected" in response.json()["message"]


def test_operation_cross_origin_refused(client) -> None:
    response = _post(
        client,
        "/api/v1/units/t3code.service/operations",
        body={"operation": "restart", "expectedState": "active", "reason": ""},
        idempotency="idem-key-00000002",
        origin="http://evil.example",
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CROSS_ORIGIN_DENIED"


def test_operation_helper_unavailable(client) -> None:
    response = _post(
        client,
        "/api/v1/units/t3code.service/operations",
        body={"operation": "restart", "expectedState": "active", "reason": ""},
        idempotency="idem-key-00000003",
        origin="http://testserver",
    )
    assert response.status_code == 200
    assert response.json()["state"] == "helper_unavailable"


def test_operation_idempotency(client) -> None:
    body = {"operation": "restart", "expectedState": "active", "reason": ""}
    first = _post(client, "/api/v1/units/t3code.service/operations", body=body,
                  idempotency="idem-key-00000004", origin="http://testserver")
    second = _post(client, "/api/v1/units/t3code.service/operations", body=body,
                   idempotency="idem-key-00000004", origin="http://testserver")
    assert first.json()["id"] == second.json()["id"]


def test_operation_missing_idempotency_key(client) -> None:
    response = _post(
        client,
        "/api/v1/units/t3code.service/operations",
        body={"operation": "restart", "expectedState": "active", "reason": ""},
        origin="http://testserver",
    )
    assert response.status_code == 422


def test_managed_schedule_create_and_validate(client) -> None:
    body = {
        "name": "nightly-sync",
        "description": "Nightly sync job",
        "onCalendar": "*-*-* 03:30:00",
        "executable": "/usr/local/bin/sync",
        "arguments": ["--force"],
        "user": "root",
        "workingDirectory": None,
        "timeoutSeconds": 3600,
        "overlapPolicy": "drop",
        "missedRunBehavior": "ignore",
        "relatedService": None,
    }
    created = _post(client, "/api/v1/schedules", body=body, origin="http://testserver")
    assert created.status_code == 201
    payload = created.json()
    assert payload["revision"] == 0
    assert payload["serviceUnit"] == "hyperion-nightly-sync.service"


def test_managed_schedule_rejects_invalid(client) -> None:
    body = {
        "name": "evil",
        "description": "",
        "onCalendar": "rm -rf /; poweroff",
        "executable": "/bin/sh",
        "arguments": [],
        "user": "root",
        "workingDirectory": None,
        "timeoutSeconds": 60,
        "overlapPolicy": "drop",
        "missedRunBehavior": "ignore",
        "relatedService": None,
    }
    response = _post(client, "/api/v1/schedules", body=body, origin="http://testserver")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SCHEDULE_INVALID"


def test_all_resources_require_identity(client) -> None:
    for path in (
        "/api/v1/host",
        "/api/v1/host/history",
        "/api/v1/units",
        "/api/v1/schedules",
        "/api/v1/activity",
    ):
        assert client.get(path).status_code == 401, path
