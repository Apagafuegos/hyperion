"""Security boundary: identity, no secrets in responses, hardened headers.

Encodes the TECHNICAL-DESIGN.md section 12 controls that are reachable
through the HTTP surface, plus the section 16.2 "no environment values"
criterion.
"""

from __future__ import annotations

import re

# Environment-shaped "KEY=value" inside a JSON string, e.g. "HOME=/root",
# "SECRET_KEY=abc", or an empty-valued "PATH=". The double quote must
# precede the key, so camelCase JSON keys ("schemaVersion":) and URLs
# (?FOO=bar mid-string) never match; matched against the JSON wire format
# (response.text), not the single-quoted dict repr.
_ENV_SHAPE = re.compile(r'"[A-Z][A-Z0-9_]{2,}=')
# Explicit keys on the uppercased text as a belt-and-suspenders pass.
_ENV_KEYS = ("PATH=", "HOME=", "LANG=", "SECRET", "TOKEN")


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


def test_health_endpoints_do_not_require_identity(client) -> None:
    # Loopback-only enforcement is Caddy-level; the app itself must never
    # add auth to the health endpoints.
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_snapshot_never_contains_environment_values(client) -> None:
    # Fixture-path guarantee: the fixture evidence is static and clean.
    # Provider_ref and image pass through unsanitized (reconciler copies
    # evidence verbatim), so the §16.2 live guarantee depends on provider
    # scrubbing at the docker/systemd boundary.
    response = get(client, "/api/v1/snapshot")
    payload = response.json()
    text = response.text
    assert _ENV_SHAPE.search(text) is None, "environment-shaped key=value leaked into the snapshot"
    upper = text.upper()
    for key in _ENV_KEYS:
        assert key not in upper, f"{key} appears in the snapshot"
    for service in payload["services"]:
        for component in service["components"]:
            if component["provider"] == "docker":
                assert component["image"], "docker components must carry an image"


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


def test_responses_require_revalidation_never_stale(client) -> None:
    # Static assets and the HTML are served behind a CDN proxy with no
    # content hashing, so stale cached copies (browser or edge) must never
    # outlive a deploy. `no-cache` forces revalidation on every load; the
    # origin answers with 304/ETag when unchanged.
    for path in ("/", "/static/assets/styles.css", "/static/assets/app.js"):
        response = get(client, path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-cache", path


def test_api_keeps_explicit_cache_policies(client) -> None:
    # The API endpoints carry contract-tested cache policies that must not
    # be overridden by the revalidation default above.
    snapshot = get(client, "/api/v1/snapshot")
    assert snapshot.headers["cache-control"] == "private, max-age=0, must-revalidate"
    logs = get(client, "/api/v1/services/langfuse/logs?tail=50")
    assert logs.headers["cache-control"] == "private, no-store"
