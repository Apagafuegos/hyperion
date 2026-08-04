# Hyperion v1 Implementation Plan — Part 4: Deployment and Hardening

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Continue from Part 3 (Phase 2 complete). Steps use checkbox (`- [ ]`) syntax for tracking.

**Part 4 covers Phase 3:** Task 3.1 (hardened systemd unit + environment), Task 3.2 (Caddy route example + auth verification), Task 3.3 (security and robustness verification), Task 3.4 (Section 16.2 acceptance run + visual comparison + CI).

---

## Phase 3 — Deployment and hardening

### Task 3.1: Hardened systemd unit and environment

**Files:**
- Create: `deploy/hyperion.service`
- Create: `deploy/install.md`

- [ ] **Step 1: Write the hardened unit**

`deploy/hyperion.service`:

```ini
[Unit]
Description=Hyperion service atlas
After=network-online.target
Wants=network-online.target

[Service]
User=hyperion
Group=hyperion
EnvironmentFile=/etc/hyperion/env
WorkingDirectory=/opt/hyperion
ExecStart=/opt/hyperion/.venv/bin/uvicorn hyperion.main:app --host 127.0.0.1 --port 8787 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
SupplementaryGroups=systemd-journal
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictRealtime=yes
MemoryDenyWriteExecute=yes
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Notes:
- `MemoryDenyWriteExecute=yes` forbids writable+executable memory — Python/uvicorn comply; do NOT wrap the ExecStart in `uv run` (uv's re-exec writes its cache); use the venv binary directly.
- The environment file is readable only by root; systemd reads it before dropping privileges.
- The `hyperion` user needs `systemd-journal` membership for journal reads and read access to `/etc/hyperion/services.yaml` (make it root:hyperion 640 or place the catalog under `/opt/hyperion`).

- [ ] **Step 2: Write the install guide**

`deploy/install.md`:

```markdown
# Hyperion deployment

1. Create the service account and directories:

       sudo useradd --system --no-create-home --shell /usr/sbin/nologin hyperion
       sudo usermod -aG systemd-journal hyperion
       sudo mkdir -p /opt/hyperion /etc/hyperion

2. Install the code (from a clone of this repository):

       sudo rsync -a --exclude node_modules --exclude .venv --exclude .git \
         . /opt/hyperion/
       cd /opt/hyperion
       uv sync --frozen
       npm ci && npm run build

3. Configure the environment (root-only):

       sudo tee /etc/hyperion/env >/dev/null <<'EOF'
HYPERION_CATALOG_PATH=/etc/hyperion/services.yaml
HYPERION_DOCKER_HOST=tcp://127.0.0.1:2375
HYPERION_BIND_HOST=127.0.0.1
HYPERION_BIND_PORT=8787
HYPERION_LOG_LEVEL=INFO
EOF
       sudo chmod 600 /etc/hyperion/env
       sudo chown root:hyperion /etc/hyperion/env
       sudo cp services.yaml /etc/hyperion/services.yaml
       sudo chown root:hyperion /etc/hyperion/services.yaml
       sudo chmod 640 /etc/hyperion/services.yaml

4. Start the Docker socket proxy (pinned image + digest):

       docker compose -f deploy/docker-proxy.compose.yaml up -d
       # After first pull, pin the digest in the compose file:
       docker image inspect --format '{{index .RepoDigests 0}}' \
         ghcr.io/tecnativa/docker-socket-proxy:0.14.1

5. Install and start the service:

       sudo cp deploy/hyperion.service /etc/systemd/system/hyperion.service
       sudo systemctl daemon-reload
       sudo systemctl enable --now hyperion
       journalctl -u hyperion -n 50

6. Verify:

       curl -s http://127.0.0.1:8787/healthz          # {"status":"ok"}
       curl -s http://127.0.0.1:8787/readyz           # 200 only after first snapshot
```

- [ ] **Step 3: Install and verify under hardening**

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin hyperion 2>/dev/null || true
sudo usermod -aG systemd-journal hyperion
sudo mkdir -p /etc/hyperion
# follow deploy/install.md steps 2-5 on the VPS
systemctl status hyperion --no-pager
```

Expected: unit `active (running)`; journal shows startup lines and no permission errors; `/healthz` and `/readyz` respond on loopback. Verify journal reads work under `ProtectSystem=strict` + `systemd-journal` membership by calling `GET /api/v1/services/t3-code/logs?tail=50` with the identity header.

- [ ] **Step 4: Commit**

```bash
git add deploy/hyperion.service deploy/install.md
git commit -m "feat: hardened systemd unit and install guide"
```

---

### Task 3.2: Caddy route example and Authentik verification

**Files:**
- Create: `deploy/Caddyfile.example`

- [ ] **Step 1: Write the Caddy route example**

`deploy/Caddyfile.example`:

```
# Example Caddy route for Hyperion. Replace the hostname before use.
# Caddy performs Authentik forward auth; Hyperion independently requires
# X-Authentik-Username on all management endpoints.
hyperion.carlos-santos.es {
    encode zstd gzip

    forward_auth auth.carlos-santos.es {
        uri /outpost.goauthentik.io/auth/caddy
        uri_override /outpost.goauthentik.io/auth/caddy
        copy_headers X-Authentik-Username
        copy_headers X-Authentik-Groups
        copy_headers X-Authentik-Email
        copy_headers X-Authentik-Name
    }

    # Health endpoints remain loopback-only (handle blocks run before forward_auth).
    handle_path /healthz {
        respond 404
    }
    handle_path /readyz {
        respond 404
    }

    reverse_proxy 127.0.0.1:8787
}
```

Notes:
- `forward_auth` copies the identity headers from the Authentik response over any client-supplied values, so the upstream only ever sees Authentik-verified identity; the app independently rejects requests without `X-Authentik-Username`.
- `handle_path` blocks execute before `forward_auth`, keeping `/healthz` and `/readyz` loopback-only.

- [ ] **Step 2: Deploy the route and verify header behavior**

```bash
# add the site block to the real Caddyfile, then
sudo systemctl reload caddy
```

Verify with curl against the public hostname (unauthenticated):

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://hyperion.carlos-santos.es/healthz   # 404 (loopback-only)
curl -s -o /dev/null -w "%{http_code}\n" https://hyperion.carlos-santos.es/          # 302 -> Authentik login
```

Then authenticate in a browser and confirm the atlas loads with the real identity header.

- [ ] **Step 3: Commit**

```bash
git add deploy/Caddyfile.example
git commit -m "feat: caddy route example with authentik forward auth"
```

---

### Task 3.3: Security and robustness verification

This task turns the §16.2 security criteria into repeatable tests.

**Files:**
- Create: `tests/integration/test_security.py`
- Create: `tests/integration/test_failure_states.py`

- [ ] **Step 1: Write the security tests**

`tests/integration/test_security.py`:

```python
"""Security boundary: identity, no secrets in responses, hardened headers."""

from __future__ import annotations

import re

import pytest


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
    assert "SECRET" not in text.upper() or "secret" not in text.lower()
    for service in payload["services"]:
        for component in service["components"]:
            assert component["image"] is not None or component["provider"] == "systemd"


def test_security_headers_present(client) -> None:
    response = get(client, "/api/v1/snapshot")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"


def test_index_has_restrictive_csp(client) -> None:
    response = get(client, "/")
    policy = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', response.text)
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
```

`tests/integration/test_failure_states.py` — the §14 material failure states reachable in fixture mode:

```python
"""Material failure states from TECHNICAL-DESIGN.md section 14."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hyperion.main import create_app
from hyperion.settings import Settings

FIXTURES = Path(__file__).parents[1] / "fixtures"


def get(client, path, **kwargs):
    headers = kwargs.pop("headers", {})
    headers["X-Authentik-Username"] = "owner"
    return client.get(path, headers=headers, **kwargs)


def test_missing_catalog_keeps_health_up_but_not_ready(tmp_path) -> None:
    base = Settings.from_env()
    settings = Settings(
        catalog_path=tmp_path / "missing.yaml",
        docker_host=base.docker_host,
        bind_host=base.bind_host,
        bind_port=base.bind_port,
        log_level=base.log_level,
        fixture_mode=True,
    )
    client = TestClient(create_app(settings=settings))
    assert client.get("/healthz").status_code == 200
    ready = client.get("/readyz")
    assert ready.status_code == 503
    assert ready.json()["error"]["code"] == "SNAPSHOT_UNAVAILABLE"
    snapshot = client.get(
        "/api/v1/snapshot", headers={"X-Authentik-Username": "owner"}
    )
    assert snapshot.status_code == 503


def test_valid_catalog_with_no_services(tmp_path) -> None:
    catalog = tmp_path / "empty.yaml"
    catalog.write_text("version: 1\nservices: []\n", encoding="utf-8")
    base = Settings.from_env()
    client = TestClient(
        create_app(
            settings=Settings(
                catalog_path=catalog,
                docker_host=base.docker_host,
                bind_host=base.bind_host,
                bind_port=base.bind_port,
                log_level=base.log_level,
                fixture_mode=True,
            )
        )
    )
    payload = get(client, "/api/v1/snapshot").json()
    assert payload["summary"]["total"] == 0
    assert [t["id"] for t in payload["territories"]] == [
        "applications", "services", "foundations",
    ]
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/integration/test_security.py tests/integration/test_failure_states.py -v`
Expected: PASS.

- [ ] **Step 3: Live hardening verification (root required)**

```bash
# 1. Docker POST denied through the proxy
curl -s -X POST http://127.0.0.1:2375/v1.41/containers/create -d '{"Image":"nginx"}' -o /dev/null -w "%{http_code}\n"
# Expected: 403 (Tecnativa proxy denies unallowed methods with 403 Forbidden), NOT 201.

# 2. Mutation attempt through the proxy
curl -s -X POST http://127.0.0.1:2375/v1.41/containers/c1/restart -o /dev/null -w "%{http_code}\n"
# Expected: 403.

# 3. No environment exposure via the API (already covered by tests above).
```

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_security.py tests/integration/test_failure_states.py
git commit -m "test: security boundary and material failure states"
```

---

### Task 3.4: Acceptance run, visual comparison, and CI

**Files:**
- Create: `.github/workflows/ci.yml`

- [x] **Step 1: Write the CI workflow** (committed in 0b2c0af; `actions/setup-node@v4` with Node 22 added to honor `engines`)

`.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: astral-sh/setup-uv@v5
      - name: Install python dependencies
        run: uv sync --frozen
      - name: Install node dependencies
        run: npm ci
      - name: Build frontend
        run: npm run build
      - name: Ruff
        run: uv run ruff check src tests
      - name: Mypy
        run: uv run mypy src
      - name: Pytest (unit + integration + contract parity)
        run: uv run pytest -q
      - name: ESLint and tsc
        run: npm run lint
      - name: Vitest
        run: npm test
      - name: Install chromium
        run: npx playwright install --with-deps chromium
      - name: Browser tests
        run: npx playwright test
```

Note: the contract-parity tests run inside `pytest`; a divergence between generated and committed schemas fails CI.

- [x] **Step 2: Run the full acceptance checklist (Section 16.2)**

Run everything and record results against each criterion:

```bash
uv run pytest -q            # unit + integration + contract parity + security + failure states
npm run lint                # eslint + tsc
npm test                    # vitest
npm run build               # vite build
npx playwright test         # desktop + mobile + reduced motion
uv run ruff check src tests
uv run mypy src
```

Checklist (from TECHNICAL-DESIGN.md §16.2 — every item must pass):

- [x] Committed production catalog (`services.yaml`) validates structurally and semantically.
- [x] Every declared current component resolves exactly once (live snapshot: no `missing` for declared selectors); unexpected containers reported as `diagnostics.unmappedRuntimes`.
- [x] T3 Code state and journal logs work through the systemd provider (live check in Task 2.5/3.1).
- [x] Every Docker-backed service exposes correct component state and allowlisted recent logs.
- [x] Public launch/copy actions match the Caddy routes (compare `action.url` values against the live Caddyfile).
- [x] State derivation passes the table-driven test for every rule and precedence collision (`tests/unit/test_state_rules.py`).
- [x] Provider loss yields partial/stale/Unknown states without losing the last valid snapshot (fixture: `test_reconcile_provider_unavailable_yields_unknown`; live: stop the docker proxy briefly and confirm snapshots continue serving with `fresh: false` and Unknown states).
- [x] No API returns environment values, raw inspect payloads, credentials, or arbitrary host data (`test_security.py` + manual live spot-check).
- [x] Docker POST requests through the proxy are demonstrably denied (Task 3.3 step 3 — `403`; the Tecnativa proxy returns 403 Forbidden for unallowed methods, not 405).
- [x] UI meets the behavior, responsive, focus, reduced-motion, and visual commitments in `DESIGN.md`.

**Execution results (Task 3.4, 2026-08-04):** all gates green — `uv run pytest -q` (171 passed), `uv run ruff check src tests` (clean), `uv run mypy src` (clean), `npm run lint` (clean), `npm test` (13 passed), `npm run build` (ok), `npx playwright test` (26 passed). Live §16.2 checks: catalog loads via `load_catalog`; live snapshot 8/8 services resolved, zero `missing`/`unknown`, `diagnostics.unmappedRuntimes` = docker-proxy only; t3-code systemd component `running`/`healthy` with 50 journald records; langfuse (5 components) and authentik (3 components) docker states correct with allowlisted bounded logs (`tail` restricted to 50/100/250/500, other values 422); catalog action URLs match the six Caddyfile hosts (t3/auth/ai/rare/langfuse/mcp); `test_state_rules.py` 32 passed; live outage (docker-proxy stopped 25s): snapshot kept serving HTTP 200 with `fresh: false`, docker provider `unavailable`, 7 docker-backed services `unknown` while systemd-backed t3-code stayed `reachable`; recovery within ~25s of restart (all 8 `reachable`, `fresh: true`); `test_security.py` 7 passed and live snapshot grep found no env values, credentials, raw inspect keys, or keys; Docker POST `/containers/create` denied with HTTP 403 (verified against the live proxy on 127.0.0.1:2375) while read GETs return 200; visual comparison found one drift — the desktop `.atlas-frame` grid auto-placed `.latitudes` into row 2 (bands rendered below the fold, first band at y≈795); fixed by pinning `.latitudes`/`.bearings-rail` to `grid-row: 1` in `frontend/atlas.css`; after rebuild the first band sits at y≈203 and the bearings rail aligns with the bands; browser suite re-run 26/26 pass; clean-install (`rm -rf .venv node_modules` → `uv sync --frozen && npm ci && npm run build && uv run pytest -q && npm test`) all green.

Residual gap: provider-loss behavior for the **systemd** provider was drill-tested only via fixtures, not live — t3-code survived the docker-outage drill by design (its state comes from systemd, not docker), and stopping the live hyperion service itself would have taken down the atlas under test.

- [x] **Step 3: Visual comparison against the approved prototype**

Completed via DOM-geometry + computed-style + region-pixel comparison of the prototype (`index.html`) vs the fixture-mode atlas at 1440px, 390px, and reduced-motion; one drift found and fixed (`.latitudes`/`.bearings-rail` pinned to `grid-row: 1`, commit 0b2c0af); post-fix screenshots in `/tmp/opencode/hyperion-shots/` (ephemeral). A strict pixel diff against `.impeccable/mocks/02-latitude-bands.png` was not performed — the comp is illustrative, so the structural commitments (header/search/nav, band borders `rgb(40,40,36)`, dividers `rgb(206,196,178)`, status palette, 4-col dossier, bearings rail at x=1240/w=178, 26px logs drawer gap, mobile strip + stacked bands + single-col dossier, 10µs reduced-motion transitions) were verified programmatically instead. The approved design documents (DESIGN.md, PRODUCT.md, index.html, .impeccable/) are committed so a fresh clone can reproduce the visual contract.

1. Run fixture mode with the built client: `HYPERION_FIXTURE_MODE=1 HYPERION_CATALOG_PATH=tests/fixtures/fixture-services.yaml uv run uvicorn hyperion.main:app --host 127.0.0.1 --port 8787 --workers 1`.
2. Open `http://127.0.0.1:8787` with the identity header (or run `npx playwright open` with the test config).
3. Compare against the approved reference `.impeccable/mocks/02-latitude-bands.png` and the prototype `index.html` (serve it via `python3 -m http.server` in the project root and open `index.html`):
   - Header, search, and destinations match at 1440px.
   - Territory bands, coordinate margins, row dividers, status chips, and bearings rail match.
   - Selected dossier expands inline with the 4-column grid; route wash on hover/selection.
   - Logs drawer sits 26px below the atlas, closed by default, native disclosure.
   - Mobile 390px: bearings strip, stacked bands, 2×2 bearings, single-column dossier.
   - Reduced motion: effectively instant.
4. Apply ONE bounded correction pass if any drift is found (adjust `frontend/atlas.css`/`atlas.ts`, rebuild, re-run the browser suite). Do not change the approved topology into a card grid.

- [x] **Step 4: Clean install verification**

From a fresh clone (or fresh `rm -rf .venv node_modules`) — executed in Task 3.4, all green (recorded in the execution results above):

```bash
uv sync --frozen && npm ci && npm run build && uv run pytest -q && npm test
```

Expected: everything green from lockfiles alone.

- [x] **Step 5: Final commit**

Executed in commit 0b2c0af (extended message `ci: full pipeline with contract parity and browser verification; accept hyperion v1`):

```bash
git add .github/workflows/ci.yml
git commit -m "ci: full pipeline with contract parity and browser verification"
```

**Completion gate:** all of §16.2 passes (checklist above), including exact runtime ownership, T3 journal access, denied Docker mutations, partial-provider behavior, schema/API parity, and bounded desktop/mobile visual verification.

---

## Deferred (explicitly out of v1, do not implement)

- Live log following.
- Persisted observation history and event timelines.
- Notifications or alerting.
- Read-write operational controls.
- Multiple VPS agents.
- Catalog live-reload on file change (validated startup load only).

---

**End of plan.** After Part 4, verify the §16.2 checklist one final time, then consider the work complete.
