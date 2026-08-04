"""Security boundary: identity, no secrets in responses, hardened headers.

Encodes the TECHNICAL-DESIGN.md section 12 controls that are reachable
through the HTTP surface, plus the section 16.2 "no environment values"
criterion.
"""

from __future__ import annotations

import re


def get(client, path, identity=True, **kwargs):
    headers = kwargs.pop("headers", {})
    if identity:
        headers["X-Authentik-Username"] = "owner"
    return client.get(path, headers=headers, **kwargs)


def test_all_management_endpoints_require_identity(client) -> None:
    for path in ("/", "/api/v1/snapshot", "/api/v1/services/langfuse/logs"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED", path


def test_health_endpoints_do_not_leak_state(client) -> None:
    health = get(client, "/healthz")
    assert set(health.json()) == {"status"}
    ready = get(client, "/readyz")
    assert set(ready.json()) == {"status"}


def test_snapshot_never_contains_environment_values(client) -> None:
    payload = get(client, "/api/v1/snapshot").json()
    text = str(payload)
    assert "PATH=" not in text
    assert "SECRET" not in text.upper()
    assert "TOKEN" not in text.upper()
    for service in payload["services"]:
        for component in service["components"]:
            assert component["image"] is not None or component["provider"] == "systemd"


def test_security_headers_present(client) -> None:
    response = get(client, "/api/v1/snapshot")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp


def test_index_has_restrictive_csp(client) -> None:
    response = get(client, "/")
    policy = re.search(
        r'http-equiv="Content-Security-Policy" content="([^"]+)"', response.text
    )
    assert policy is not None
    csp = policy.group(1)
    assert "default-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_logs_endpoint_never_returns_raw_environment(client) -> None:
    response = get(client, "/api/v1/services/langfuse/logs?tail=50")
    assert response.status_code == 200
    assert "Environment" not in response.text
    assert "Env" not in response.text
