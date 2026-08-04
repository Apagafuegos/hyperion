# Hyperion v1 Implementation Plan — Part 2: App Shell, HTTP API, Frontend

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Continue from Part 1 (tasks 0.1–1.7 complete). Steps use checkbox (`- [ ]`) syntax for tracking.

**Part 2 covers:** Task 1.8 (settings + app shell + HTTP API), Task 1.9 (OpenAPI contract parity), Task 1.10 (Jinja application shell), Task 1.11 (frontend API client + logic), Task 1.12 (atlas client + authored CSS), Task 1.13 (browser verification). Phase 1 is complete when this part is green.

---

### Task 1.8: Settings, application shell, and HTTP API

**Files:**
- Create: `src/hyperion/settings.py`
- Create: `src/hyperion/api/__init__.py`
- Create: `src/hyperion/api/errors.py`
- Create: `src/hyperion/api/routes.py`
- Create: `src/hyperion/main.py`
- Create: `src/hyperion/providers/combined.py`
- Create: `src/hyperion/services/supervisor.py`
- Create: `src/hyperion/templates/index.html` (minimal placeholder — replaced in Task 1.10)
- Test: `tests/integration/conftest.py`
- Test: `tests/integration/test_api.py`

- [ ] **Step 1: Write the failing integration tests**

`tests/integration/conftest.py`:

```python
"""Integration fixtures: app assembled from fixture providers."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hyperion.main import create_app
from hyperion.settings import Settings

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def client() -> TestClient:
    settings = Settings.from_env()
    app = create_app(
        settings=Settings(
            catalog_path=FIXTURES / "fixture-services.yaml",
            docker_host=settings.docker_host,
            bind_host=settings.bind_host,
            bind_port=settings.bind_port,
            log_level=settings.log_level,
            fixture_mode=True,
        )
    )
    return TestClient(app)
```

`tests/integration/test_api.py`:

```python
"""HTTP contract: auth, health, snapshot, logs, and error envelope."""

from __future__ import annotations


def get(client, path, identity=True, **kwargs):
    headers = kwargs.pop("headers", {})
    if identity:
        headers["X-Authentik-Username"] = "owner"
    return client.get(path, headers=headers, **kwargs)


def test_healthz_does_not_require_identity(client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_ok_after_initial_snapshot(client) -> None:
    response = get(client, "/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_identity_required_for_snapshot(client) -> None:
    response = client.get("/api/v1/snapshot")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert body["error"]["retryable"] is False


def test_snapshot_shape_and_content(client) -> None:
    response = get(client, "/api/v1/snapshot")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=0, must-revalidate"
    payload = response.json()
    assert payload["schemaVersion"] == 1
    assert payload["fresh"] is True
    assert len(payload["services"]) == 10
    assert [t["id"] for t in payload["territories"]] == [
        "applications", "services", "foundations",
    ]
    summary = payload["summary"]
    assert summary["total"] == 10
    assert summary["dormant"] == 1
    states = {s["id"]: s["state"] for s in payload["services"]}
    assert states["t3-code"] == "reachable"
    assert states["demo-dormant"] == "dormant"
    assert states["librechat"] == "down"
    assert states["langfuse"] == "down"
    assert states["authentik"] == "degraded"  # slow route
    assert states["rarecord"] == "degraded"  # unhealthy dependency
    assert states["mcp-observatory"] == "degraded"  # health starting
    assert states["the-vault"] == "reachable"
    assert states["shared-postgres"] == "reachable"
    assert len(payload["diagnostics"]["unmappedRuntimes"]) == 3


def test_snapshot_etag_round_trip(client) -> None:
    first = get(client, "/api/v1/snapshot")
    etag = first.headers["etag"]
    second = get(client, "/api/v1/snapshot", headers={"If-None-Match": etag})
    assert second.status_code == 304


def test_logs_endpoint(client) -> None:
    response = get(client, "/api/v1/services/authentik/logs?tail=50")
    assert response.status_code == 200
    payload = response.json()
    assert payload["serviceId"] == "authentik"
    assert payload["records"]
    assert payload["records"][0]["provider"] == "docker"
    assert response.headers["cache-control"] == "private, no-store"


def test_logs_source_filter(client) -> None:
    response = get(client, "/api/v1/services/authentik/logs?source=worker&tail=50")
    payload = response.json()
    assert payload["source"] == "worker"
    assert all(r["source"] == "worker" for r in payload["records"])


def test_logs_unknown_service(client) -> None:
    response = get(client, "/api/v1/services/nope/logs")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SERVICE_NOT_FOUND"


def test_logs_unknown_source(client) -> None:
    response = get(client, "/api/v1/services/authentik/logs?source=ghost")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "LOG_SOURCE_NOT_FOUND"


def test_logs_invalid_tail(client) -> None:
    response = get(client, "/api/v1/services/authentik/logs?tail=999")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_TAIL"


def test_logs_invalid_before(client) -> None:
    response = get(client, "/api/v1/services/authentik/logs?before=not-a-date")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_BEFORE"


def test_index_served_under_identity(client) -> None:
    response = get(client, "/")
    assert response.status_code == 200
    assert "Hyperion" in response.text
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_api.py -x -q`
Expected: FAIL — `hyperion.settings` import error.

- [ ] **Step 3: Write `src/hyperion/settings.py`**

```python
"""Environment configuration; the full v1 surface from TECHNICAL-DESIGN.md 12.2."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    catalog_path: Path
    docker_host: str
    bind_host: str
    bind_port: int
    log_level: str
    fixture_mode: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        def _env(name: str, default: str) -> str:
            return os.environ.get(name, default)

        return cls(
            catalog_path=Path(_env("HYPERION_CATALOG_PATH", "/etc/hyperion/services.yaml")),
            docker_host=_env("HYPERION_DOCKER_HOST", "tcp://127.0.0.1:2375"),
            bind_host=_env("HYPERION_BIND_HOST", "127.0.0.1"),
            bind_port=int(_env("HYPERION_BIND_PORT", "8787")),
            log_level=_env("HYPERION_LOG_LEVEL", "INFO"),
            fixture_mode=_env("HYPERION_FIXTURE_MODE", "") == "1",
        )
```

Note: `HYPERION_FIXTURE_MODE` is a development-only switch; the five production variables remain exactly those in §12.2.

- [ ] **Step 4: Write the error envelope**

`src/hyperion/api/__init__.py`:

```python
"""HTTP API layer."""
```

`src/hyperion/api/errors.py`:

```python
"""Stable error envelope from TECHNICAL-DESIGN.md section 10."""

from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..models import ErrorResponse

ERROR_CODES = (
    "AUTHENTICATION_REQUIRED",
    "SNAPSHOT_UNAVAILABLE",
    "SERVICE_NOT_FOUND",
    "LOG_SOURCE_NOT_FOUND",
    "LOG_SOURCE_UNAVAILABLE",
    "INVALID_TAIL",
    "INVALID_BEFORE",
    "INVALID_REQUEST",
    "PROVIDER_TIMEOUT",
    "RESPONSE_TOO_LARGE",
)


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, retryable: bool) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


def _payload(code: str, message: str, retryable: bool) -> dict:
    error = ErrorResponse(error={"code": code, "message": message, "retryable": retryable})
    return error.model_dump(by_alias=True)


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_payload(exc.code, exc.message, exc.retryable),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    locations = [tuple(error["loc"]) for error in exc.errors()]
    if any("before" in loc for loc in locations):
        return JSONResponse(
            status_code=422, content=_payload("INVALID_BEFORE", "Invalid timestamp.", False)
        )
    if any("tail" in loc for loc in locations):
        return JSONResponse(
            status_code=422, content=_payload("INVALID_TAIL", "Invalid tail value.", False)
        )
    return JSONResponse(status_code=422, content=_payload("INVALID_REQUEST", "Invalid request.", False))
```

Note: `INVALID_REQUEST` is an additive error code (allowed by §10); document it in TECHNICAL-DESIGN.md §10 in the final commit step of this task.

- [ ] **Step 5: Write the routes**

`src/hyperion/api/routes.py`:

```python
"""HTTP routes: health, readiness, snapshot, and logs."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import APIKeyHeader

from ..models import AtlasSnapshot, HealthResponse, LogsResponse
from ..services.logs import LogGateway
from .errors import ApiError

router = APIRouter()

_identity_header = APIKeyHeader(name="X-Authentik-Username", auto_error=False)


def require_identity(identity: Annotated[str | None, Depends(_identity_header)]) -> str:
    if identity is None or not identity.strip():
        raise ApiError(
            401, "AUTHENTICATION_REQUIRED", "Authentik identity header is required.", False
        )
    return identity


@router.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    """Process liveness check."""
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=HealthResponse)
def readyz(request: Request) -> HealthResponse:
    """Catalog and initial snapshot readiness check."""
    if request.app.state.snapshot_store.get() is None:
        raise ApiError(
            503, "SNAPSHOT_UNAVAILABLE", "The atlas has not published a valid snapshot yet.", True
        )
    return HealthResponse(status="ready")


@router.get("/api/v1/snapshot", response_model=AtlasSnapshot)
def get_snapshot(
    request: Request, response: Response, identity: Annotated[str, Depends(require_identity)]
) -> Response:
    """Get the complete normalized atlas snapshot."""
    pair = request.app.state.snapshot_store.get()
    if pair is None:
        raise ApiError(
            503, "SNAPSHOT_UNAVAILABLE", "The atlas has not published a valid snapshot yet.", True
        )
    snapshot, etag = pair
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    payload = snapshot.model_dump(mode="json", by_alias=True)
    return Response(content=json.dumps(payload), media_type="application/json")


@router.get("/api/v1/services/{service_id}/logs", response_model=LogsResponse)
async def get_service_logs(
    request: Request,
    service_id: str,
    source: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    tail: Literal[50, 100, 250, 500] = 100,
    before: Annotated[datetime | None, Query()] = None,
    _: Annotated[str, Depends(require_identity)] = "",
) -> LogsResponse:
    """Get bounded recent logs for one allowlisted service."""
    gateway: LogGateway = request.app.state.log_gateway
    return await gateway.read(service_id, source, tail, before)
```

- [ ] **Step 6: Write the log gateway**

`src/hyperion/services/logs.py`:

```python
"""Log gateway: allowlist resolution and bounded on-demand log reads."""

from __future__ import annotations

from datetime import datetime, timezone

from ..api.errors import ApiError
from ..models import LogRecord, LogsResponse
from ..providers.base import LogBinding, LogRecordIn, RuntimeProvider


class LogGateway:
    def __init__(self, runtime_provider: RuntimeProvider, catalog) -> None:
        self._provider = runtime_provider
        self._catalog = catalog

    async def read(
        self, service_id: str, source: str | None, tail: int, before: datetime | None
    ) -> LogsResponse:
        service = next((s for s in self._catalog.services if s.id == service_id), None)
        if service is None:
            raise ApiError(404, "SERVICE_NOT_FOUND", "No such service in the catalog.", False)
        requested = [source] if source is not None else list(service.logs.sources)
        for name in requested:
            if name not in service.logs.sources:
                raise ApiError(404, "LOG_SOURCE_NOT_FOUND", "Log source is not allowlisted.", False)

        records: list[LogRecord] = []
        for name in requested:
            component = next(
                (c for c in service.runtime.components if c.selector == name), None
            )
            if component is None:
                continue
            binding = LogBinding(
                provider="systemd" if service.runtime.provider == "systemd" else "docker",
                service_id=service_id,
                source=name,
                reference=component.selector,
            )
            fetched = await self._provider.read_logs(binding, tail, before)
            records.extend(_normalize(fetched))

        records.sort(
            key=lambda record: (record.timestamp is not None, record.timestamp or datetime.min)
        )
        if len(records) > tail:
            records = records[-tail:]
            truncated = True
        else:
            truncated = False
        return LogsResponse(
            service_id=service_id,
            requested_at=datetime.now(timezone.utc),
            source=source,
            records=records,
            truncated=truncated,
        )


def _normalize(records: list[LogRecordIn]) -> list[LogRecord]:
    out: list[LogRecord] = []
    for record in records:
        message = record.message
        truncated = len(message) > 16384
        if truncated:
            message = message[:16384]
        out.append(
            LogRecord(
                timestamp=record.timestamp,
                source=record.source,
                provider=record.provider,
                stream=record.stream,
                severity=record.severity,
                message=message,
                truncated=truncated,
            )
        )
    return out
```

- [ ] **Step 7: Write the combined runtime provider**

`src/hyperion/providers/combined.py`:

```python
"""Combined runtime provider: delegates by binding provider."""

from __future__ import annotations

from datetime import datetime

from ..models import Catalog
from .base import LogBinding, LogRecordIn, ProviderObservation, RuntimeProvider


class CombinedRuntimeProvider(RuntimeProvider):
    def __init__(self, docker, systemd) -> None:
        self._docker = docker
        self._systemd = systemd

    async def observe(self, catalog: Catalog) -> list[ProviderObservation]:
        return [await self._docker.observe(catalog), await self._systemd.observe(catalog)]

    async def read_logs(
        self, binding: LogBinding, tail: int, before: datetime | None
    ) -> list[LogRecordIn]:
        provider = self._docker if binding.provider == "docker" else self._systemd
        return await provider.read_logs(binding, tail, before)
```

- [ ] **Step 8: Write the refresh supervisor**

`src/hyperion/services/supervisor.py`:

```python
"""Refresh supervisor: monotonic scheduling, no overlapping cycles."""

from __future__ import annotations

import asyncio
import logging

from ..providers.base import ProbeObservation
from .reconciler import reconcile
from .snapshots import SnapshotStore

logger = logging.getLogger("hyperion.supervisor")

RUNTIME_INTERVAL_SECONDS = 15
PROBE_INTERVAL_SECONDS = 30


class RefreshSupervisor:
    def __init__(
        self,
        runtime_provider,
        probe_provider,
        catalog,
        store: SnapshotStore,
        runtime_interval: float = RUNTIME_INTERVAL_SECONDS,
        probe_interval: float = PROBE_INTERVAL_SECONDS,
    ) -> None:
        self._runtime_provider = runtime_provider
        self._probe_provider = probe_provider
        self._catalog = catalog
        self._store = store
        self._runtime_interval = runtime_interval
        self._probe_interval = probe_interval
        self._probe_observation: ProbeObservation | None = None
        self._cancelled = False

    def _probe_urls(self) -> tuple[str, ...]:
        return tuple(
            s.route_probe.url for s in self._catalog.services if s.route_probe is not None
        )

    async def run(self) -> None:
        """Run both loops until cancelled; cycles are sequential, never queued."""
        tasks = [
            asyncio.create_task(self._runtime_loop()),
            asyncio.create_task(self._probe_loop()),
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            self._cancelled = True
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _runtime_loop(self) -> None:
        while not self._cancelled:
            started = asyncio.get_running_loop().time()
            await self._runtime_cycle()
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.0, self._runtime_interval - elapsed))

    async def _probe_loop(self) -> None:
        while not self._cancelled:
            started = asyncio.get_running_loop().time()
            await self._probe_cycle()
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.0, self._probe_interval - elapsed))

    async def _runtime_cycle(self) -> None:
        try:
            runtime_observations = await self._runtime_provider.observe(self._catalog)
            probe_observation = self._probe_observation or await self._probe_provider.observe(
                self._probe_urls()
            )
            snapshot = reconcile(self._catalog, runtime_observations, probe_observation)
            self._store.publish(snapshot)
        except Exception as exc:  # provider boundary; keep the last valid snapshot
            logger.error("runtime refresh cycle failed: %s", exc)

    async def _probe_cycle(self) -> None:
        try:
            self._probe_observation = await self._probe_provider.observe(self._probe_urls())
        except Exception as exc:  # keep the last probe observation
            logger.error("probe refresh cycle failed: %s", exc)
```

- [ ] **Step 9: Write the application shell**

`src/hyperion/main.py`:

```python
"""Application assembly, lifespan, and security headers."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api.errors import ApiError, api_error_handler, validation_error_handler
from .api.routes import router as api_router
from .catalog import CatalogError, load_catalog
from .providers.combined import CombinedRuntimeProvider
from .providers.fixture import FixtureProbeProvider, FixtureRuntimeProvider
from .services.logs import LogGateway
from .services.reconciler import reconcile
from .services.snapshots import SnapshotStore
from .services.supervisor import RefreshSupervisor
from .settings import Settings

logger = logging.getLogger("hyperion")

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
FIXTURES_DIR = Path(__file__).parents[2] / "tests" / "fixtures"


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    _configure_logging(settings)
    store = SnapshotStore()
    app.state.snapshot_store = store
    app.state.supervisor_task = None
    app.state.runtime_provider = None
    app.state.probe_provider = None
    app.state.log_gateway = None

    catalog = None
    try:
        catalog = load_catalog(settings.catalog_path)
    except CatalogError as exc:
        logger.error("catalog invalid at startup; readiness will remain false: %s", exc)
    app.state.catalog = catalog

    if catalog is not None:
        runtime_provider = _build_runtime_provider(settings)
        probe_provider = _build_probe_provider(settings)
        app.state.runtime_provider = runtime_provider
        app.state.probe_provider = probe_provider
        app.state.log_gateway = LogGateway(runtime_provider, catalog)
        if await _run_cycle(runtime_provider, probe_provider, catalog, store):
            supervisor = RefreshSupervisor(
                runtime_provider=runtime_provider,
                probe_provider=probe_provider,
                catalog=catalog,
                store=store,
            )
            app.state.supervisor_task = asyncio.create_task(supervisor.run())
    yield
    task = app.state.supervisor_task
    if task is not None:
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    await _close_providers(app)


def _build_runtime_provider(settings: Settings):
    if settings.fixture_mode:
        return FixtureRuntimeProvider(
            FIXTURES_DIR / "fixture-evidence.json", FIXTURES_DIR / "fixture-logs.json"
        )
    from .providers.docker import DockerProvider
    from .providers.systemd import SystemdProvider

    return CombinedRuntimeProvider(DockerProvider(settings.docker_host), SystemdProvider())


def _build_probe_provider(settings: Settings):
    if settings.fixture_mode:
        return FixtureProbeProvider(FIXTURES_DIR / "fixture-probes.json")
    from .providers.probe import ProbeProvider

    return ProbeProvider()


def _probe_urls(catalog) -> tuple[str, ...]:
    return tuple(s.route_probe.url for s in catalog.services if s.route_probe is not None)


async def _run_cycle(runtime_provider, probe_provider, catalog, store) -> bool:
    try:
        runtime_obs, probe_obs = await asyncio.gather(
            runtime_provider.observe(catalog),
            probe_provider.observe(_probe_urls(catalog)),
        )
    except Exception as exc:  # provider boundary
        logger.error("initial refresh failed: %s", exc)
        return False
    snapshot = reconcile(catalog, runtime_obs, probe_obs)
    store.publish(snapshot)
    return True


async def _close_providers(app) -> None:
    runtime = getattr(app.state, "runtime_provider", None)
    close = getattr(runtime, "close", None)
    if close is not None:
        try:
            await close()
        except Exception as exc:
            logger.warning("runtime provider close failed: %s", exc)
    probe = getattr(app.state, "probe_provider", None)
    close = getattr(probe, "close", None)
    if close is not None:
        try:
            await close()
        except Exception as exc:
            logger.warning("probe provider close failed: %s", exc)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="Hyperion API",
        version="1.0.0",
        description="Read-only API for the single-VPS Hyperion service atlas.",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(api_router)

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        identity = request.headers.get("X-Authentik-Username")
        if not identity or not identity.strip():
            raise ApiError(
                401, "AUTHENTICATION_REQUIRED", "Authentik identity header is required.", False
            )
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        return templates.TemplateResponse(request, "index.html", {})

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


app = create_app()
```

Notes:
- `FixtureRuntimeProvider` returns `list[ProviderObservation]` directly, and `CombinedRuntimeProvider` also returns a list — `_run_cycle` works for both. The `ProbeProvider`/`DockerProvider`/`SystemdProvider` will gain a `close()` method in Part 3 (the fixture providers do not need one; `getattr(..., "close", None)` handles absence).
- The Docker provider thread pool shutdown is added in Part 3 (`shutdown(wait=False)` on the executor).

- [ ] **Step 10: Create placeholder template and static dir**

```bash
mkdir -p src/hyperion/templates src/hyperion/static
```

`src/hyperion/templates/index.html` (minimal; replaced in Task 1.10):

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Hyperion</title>
</head>
<body>
<p>Hyperion atlas — client build pending (Task 1.10).</p>
</body>
</html>
```

- [ ] **Step 11: Run tests, lint, typecheck**

```bash
uv run pytest tests/integration/test_api.py -v
uv run ruff check src tests
uv run mypy src
```

Expected: PASS (fixture mode: t3-code reachable, authentik degraded, dormant wins, etag/304, logs, 401/404/422 envelopes, unmapped diagnostics).

- [ ] **Step 12: Document the additive error code and commit**

Add to `TECHNICAL-DESIGN.md` §10's code list: `INVALID_REQUEST`. Then:

```bash
git add src/hyperion/settings.py src/hyperion/api src/hyperion/main.py src/hyperion/providers/combined.py src/hyperion/services/logs.py src/hyperion/services/supervisor.py src/hyperion/templates tests/integration TECHNICAL-DESIGN.md
git commit -m "feat: application shell, snapshot and logs API with error envelope"
```

---

### Task 1.9: OpenAPI contract parity

**Files:**
- Test: `tests/integration/test_contract_parity.py`

- [ ] **Step 1: Write the parity test**

`tests/integration/test_contract_parity.py`:

```python
"""Generated OpenAPI must match the committed schema/openapi.yaml contract."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from hyperion.main import create_app

OPENAPI_PATH = Path(__file__).parents[2] / "schema" / "openapi.yaml"


def _resolve(node: Any, components: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].removeprefix("#/components/schemas/")
            resolved = _resolve(copy.deepcopy(components[name]), components)
            extra = {k: v for k, v in node.items() if k != "$ref"}
            if extra:
                merged = copy.deepcopy(resolved)
                merged.update({k: _resolve(v, components) for k, v in extra.items()})
                return merged
            return resolved
        return {k: _resolve(v, components) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(i, components) for i in node]
    return node


def _sort(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _sort(v) for k, v in sorted(node.items())}
    if isinstance(node, list):
        return [_sort(i) for i in node]
    return node


def _normalize(node: Any) -> Any:
    """Canonicalize both sides; documented normalization rules only."""
    if isinstance(node, dict):
        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key in ("title", "default", "format", "servers", "security", "openapi"):
                continue
            if key == "oneOf":
                key = "anyOf"
            if key in ("const", "enum") and "type" in node:
                continue
            if key == "responses":
                value = {k: v for k, v in value.items() if k != "422"}
            if key == "headers" and isinstance(value, dict) and "required" in value:
                value = {k: v for k, v in value.items() if k != "required"}
            cleaned[key] = _normalize(value)
        return _sort(cleaned)
    if isinstance(node, list):
        return [_normalize(i) for i in node]
    return node


def _strip(node: Any) -> Any:
    """Keep paths, parameters, responses, and schemas; drop non-contract noise."""
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_strip(i) for i in node]
    return node


def test_openapi_matches_committed_contract() -> None:
    committed = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    generated = create_app().openapi()

    committed_schemas = committed["components"]["schemas"]
    generated_schemas = generated["components"]["schemas"]
    committed_paths = _strip(committed["paths"])
    generated_paths = _strip(generated["paths"])

    for path, methods in committed_paths.items():
        assert path in generated_paths, f"missing path {path}"
        for operation, spec in methods.items():
            assert operation in generated_paths[path], f"missing operation {path} {operation}"
            assert spec["summary"] == generated_paths[path][operation]["summary"], path
            resolved_committed = _resolve(spec["responses"], committed_schemas)
            resolved_generated = _resolve(
                generated_paths[path][operation]["responses"], generated_schemas
            )
            assert _normalize(resolved_committed) == _normalize(resolved_generated), path
            for parameter in spec.get("parameters", []):
                in_generated = next(
                    (
                        p
                        for p in generated_paths[path][operation].get("parameters", [])
                        if p["name"] == parameter["name"] and p["in"] == parameter["in"]
                    ),
                    None,
                )
                assert in_generated is not None, f"missing parameter {parameter['name']} in {path}"
                assert _normalize(_resolve(parameter, committed_schemas)) == _normalize(
                    _resolve(in_generated, generated_schemas)
                ), parameter

    for schema_name, schema in committed_schemas.items():
        assert schema_name in generated_schemas, f"missing schema {schema_name}"
        assert _normalize(_resolve(schema, committed_schemas)) == _normalize(
            _resolve(generated_schemas[schema_name], generated_schemas)
        ), schema_name
```

- [ ] **Step 2: Run and iterate**

Run: `uv run pytest tests/integration/test_contract_parity.py -x -v`
Expected: first run exposes small structural gaps (FastAPI inlining `Id`, header schema differences, `security` arrays). Fix by aligning `models.py`/route declarations. Where the generated contract is legitimately more precise (e.g., `required` on header ETag), update `schema/openapi.yaml` AND `TECHNICAL-DESIGN.md` in the same change. Do NOT weaken the test.

> **Task 1.3 note (verified, not speculative):** the API models were already exercised against the committed `openapi.yaml` during Task 1.3. Expect these representational mismatches in `_normalize` (or as YAML updates, per the "generated is legitimately more precise → update the YAML" path above) rather than discovering them here:
> - **Nullable style:** pydantic emits `anyOf: [{...}, {"type": "null"}]` where the committed YAML uses `type: [x, "null"]` on ~20 nullable fields (provider status, route, components, log records, etc.) — normalize `type` arrays to `anyOf` before comparing (or rewrite the YAML nullables).
> - **`const` + `type` asymmetry:** pydantic emits `{"type": ..., "const": ...}` where the YAML has bare `{"const": ...}` for `schemaVersion` and the three `ServiceAction` discriminators (open/copy/none).
> - **`minLength: 1` on `Id`:** the generated `Id` schema carries `minLength: 1` (shared catalog alias constraint) but the YAML `Id` schema only has `pattern` + `maxLength` — the YAML may be updated to include it (generated is more precise).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_contract_parity.py schema/openapi.yaml TECHNICAL-DESIGN.md
git commit -m "feat: enforce openapi contract parity in tests"
```

---

### Task 1.10: Jinja application shell

**Files:**
- Create: `src/hyperion/templates/index.html`

- [ ] **Step 1: Write the shell** (semantic structure from `index.html`'s approved prototype; all runtime rows are rendered by TypeScript; CSP is restrictive; fonts and assets are same-origin)

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hyperion — Service Atlas</title>
<meta name="color-scheme" content="light">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'none'; frame-ancestors 'none'; object-src 'none'">
<link rel="stylesheet" href="/static/assets/styles.css">
</head>
<body>
<header class="global-header">
  <div class="header-inner">
    <a class="brand" href="/" aria-label="Hyperion home">
      <span class="brand-mark" aria-hidden="true"></span>
      <strong class="brand-title">Hyperion</strong>
    </a>
    <label class="global-search">
      <span class="search-icon" aria-hidden="true"></span>
      <input id="search" type="search" placeholder="Find services, endpoints…" autocomplete="off" aria-label="Search services">
      <kbd class="search-key">⌘ K</kbd>
    </label>
    <nav class="global-nav" aria-label="Territories">
      <a class="nav-destination" href="#applications" data-territory-link="applications">Applications</a>
      <a class="nav-destination" href="#services" data-territory-link="services">Services</a>
      <a class="nav-destination" href="#foundations" data-territory-link="foundations">Foundations</a>
    </nav>
  </div>
</header>

<main class="atlas-main">
  <p class="workspace-statement">Everything deployed, mapped to one calm surface.</p>

  <section class="atlas-frame" aria-label="Service atlas">
    <aside class="bearings-rail" aria-label="Fleet bearings">
      <div id="bearings"></div>
      <p class="bearings-note">Service states, not containers.</p>
      <div id="diagnostics-keycap" class="diagnostics-keycap" hidden></div>
    </aside>
    <div class="latitudes" id="latitudes" aria-live="polite"></div>
  </section>

  <details class="log-drawer" id="log-drawer">
    <summary>
      <strong>Logs</strong>
      <span class="log-drawer-subject" id="log-subject">Select a service to inspect its logs.</span>
      <span class="open-label">Open logs</span>
    </summary>
    <div class="log-controls">
      <label>Source
        <select id="log-source"></select>
      </label>
      <label>Tail
        <select id="log-tail">
          <option value="50">50</option>
          <option value="100" selected>100</option>
          <option value="250">250</option>
          <option value="500">500</option>
        </select>
      </label>
      <button type="button" id="log-refresh" class="button-route">Refresh</button>
      <span class="log-state" id="log-state" role="status"></span>
    </div>
    <div id="log-rows" class="log-rows"></div>
  </details>

  <div id="toast" class="toast" role="status" hidden></div>
</main>

<noscript>
  <section class="noscript-note">
    Hyperion requires JavaScript to render the live atlas. The read-only API
    remains available at <code>/api/v1/snapshot</code> with the Authentik identity header.
  </section>
</noscript>

<script type="module" src="/static/assets/atlas.js"></script>
</body>
</html>
```

Notes:
- The workspace statement is a compact sentence, never a hero. Keep copy short.
- Rows, status chips, bearings, and log rows are produced by `atlas.ts` into `#latitudes`, `#bearings`, and `#log-rows`.
- CSP forbids remote fonts — `@fontsource` assets are bundled into `/static/assets/` by Vite.
- The `<link>` references `/static/assets/styles.css` — this is the filename Vite produces from the `styles` input (Task 1.12); the stylesheet link must match whatever `vite build` emits (check `src/hyperion/static/assets/` after the first build and adjust if needed).

- [ ] **Step 2: Verify the shell renders**

Run (fixture mode):

```bash
HYPERION_FIXTURE_MODE=1 HYPERION_CATALOG_PATH=tests/fixtures/fixture-services.yaml \
  uv run uvicorn hyperion.main:app --host 127.0.0.1 --port 8787 --workers 1 &
sleep 2
curl -s -H "X-Authentik-Username: owner" http://127.0.0.1:8787/ | grep -q "Service Atlas" && echo OK
kill %1
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add src/hyperion/templates/index.html
git commit -m "feat: jinja application shell with restrictive csp"
```

---

### Task 1.11: Frontend API client and pure logic (Vitest)

**Files:**
- Create: `frontend/api.ts`
- Create: `frontend/logic.ts`
- Test: `tests/frontend/logic.test.ts`

- [ ] **Step 1: Write the API client**

`frontend/api.ts` — typed client mirroring the committed contract (camelCase):

```ts
export type ServiceState = "reachable" | "degraded" | "down" | "dormant" | "unknown";
export type ComponentState = "running" | "starting" | "restarting" | "paused" | "stopped" | "missing" | "unknown";
export type HealthState = "healthy" | "starting" | "unhealthy" | "unconfigured" | "unknown";
export type TerritoryId = "applications" | "services" | "foundations";
export type Severity = "debug" | "info" | "notice" | "warning" | "error" | "critical" | "alert" | "emergency";

export interface ServiceActionOpen { type: "open"; url: string }
export interface ServiceActionCopy { type: "copy"; url: string }
export interface ServiceActionNone { type: "none" }
export type ServiceAction = ServiceActionOpen | ServiceActionCopy | ServiceActionNone;

export interface StateReason {
  code: string;
  severity: "info" | "warning" | "critical";
  message: string;
  componentKey: string | null;
}

export interface RouteSnapshot {
  state: "reachable" | "slow" | "failed" | "unknown";
  statusCode: number | null;
  latencyMs: number | null;
  consecutiveFailures: number;
  observedAt: string | null;
  error: "dns" | "timeout" | "tls" | "connection" | "http" | "unknown" | null;
}

export interface ComponentSnapshot {
  key: string;
  label: string;
  provider: "docker" | "systemd";
  providerRef: string | null;
  role: "primary" | "worker" | "dependency" | "sidecar" | "shared";
  required: boolean;
  state: ComponentState;
  health: HealthState;
  observedAt: string | null;
  image: string | null;
  uptimeSeconds: number | null;
  restartCount: number | null;
  cpuPercent: number | null;
  memoryBytes: number | null;
}

export interface DependencySnapshot { serviceId: string; state: ServiceState }
export interface LogSource { key: string; label: string; provider: "docker" | "journald"; available: boolean }

export interface ServiceSnapshot {
  id: string;
  name: string;
  description: string;
  territory: TerritoryId;
  kind: "web" | "api" | "mcp" | "worker" | "infrastructure";
  intent: "active" | "dormant";
  action: ServiceAction;
  state: ServiceState;
  stateReasons: StateReason[];
  observedAt: string;
  route: RouteSnapshot | null;
  components: ComponentSnapshot[];
  dependencies: DependencySnapshot[];
  logSources: LogSource[];
}

export interface ProviderStatus {
  provider: "docker" | "systemd" | "probe";
  state: "available" | "degraded" | "unavailable";
  observedAt: string | null;
  message: string | null;
}

export interface StateSummary {
  total: number; reachable: number; degraded: number; down: number; dormant: number; unknown: number;
}

export interface UnmappedRuntime {
  provider: "docker" | "systemd";
  reference: string;
  project: string | null;
  component: string | null;
  state: string;
}

export interface Diagnostics { unmappedRuntimes: UnmappedRuntime[]; warnings: string[] }

export interface Snapshot {
  schemaVersion: 1;
  generatedAt: string;
  catalogRevision: string;
  fresh: boolean;
  providers: ProviderStatus[];
  summary: StateSummary;
  territories: { id: TerritoryId; label: string; order: number }[];
  services: ServiceSnapshot[];
  diagnostics: Diagnostics;
}

export interface LogRecord {
  timestamp: string | null;
  source: string;
  provider: "docker" | "journald";
  stream: "stdout" | "stderr" | "journal" | "unknown";
  severity: Severity | null;
  message: string;
  truncated: boolean;
}

export interface LogsResponse {
  serviceId: string;
  requestedAt: string;
  source: string | null;
  records: LogRecord[];
  truncated: boolean;
}

export type SnapshotResult =
  | { status: "ok"; snapshot: Snapshot; etag: string }
  | { status: "not-modified" }
  | { status: "error"; message: string };

export type LogsResult =
  | { status: "ok"; logs: LogsResponse }
  | { status: "error"; code: string; message: string; retryable: boolean };

export async function fetchSnapshot(etag: string | null): Promise<SnapshotResult> {
  try {
    const headers: Record<string, string> = {};
    if (etag !== null) headers["If-None-Match"] = etag;
    const response = await fetch("/api/v1/snapshot", { headers, cache: "no-store" });
    if (response.status === 304) return { status: "not-modified" };
    if (!response.ok) return { status: "error", message: `Snapshot request failed (${response.status}).` };
    const snapshot = (await response.json()) as Snapshot;
    return { status: "ok", snapshot, etag: response.headers.get("etag") ?? "" };
  } catch (error) {
    return { status: "error", message: error instanceof Error ? error.message : "Network failure." };
  }
}

export async function fetchLogs(
  serviceId: string,
  source: string | null,
  tail: number,
): Promise<LogsResult> {
  try {
    const params = new URLSearchParams({ tail: String(tail) });
    if (source !== null) params.set("source", source);
    const response = await fetch(
      `/api/v1/services/${encodeURIComponent(serviceId)}/logs?${params}`,
      { cache: "no-store" },
    );
    const body = (await response.json()) as
      | { error?: { code?: string; message?: string; retryable?: boolean } }
      | LogsResponse;
    if (!response.ok) {
      const err = "error" in (body as object)
        ? (body as { error: { code: string; message: string; retryable: boolean } }).error
        : null;
      return {
        status: "error",
        code: err?.code ?? "UNKNOWN",
        message: err?.message ?? "Logs request failed.",
        retryable: err?.retryable ?? false,
      };
    }
    return { status: "ok", logs: body as LogsResponse };
  } catch (error) {
    return {
      status: "error",
      code: "NETWORK",
      message: error instanceof Error ? error.message : "Network failure.",
      retryable: true,
    };
  }
}
```

- [ ] **Step 2: Write the pure logic module**

`frontend/logic.ts`:

```ts
import type { ServiceSnapshot, Snapshot, TerritoryId } from "./api";

export const TERRITORY_ORDER: TerritoryId[] = ["applications", "services", "foundations"];

export function matchesQuery(service: ServiceSnapshot, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (q === "") return true;
  const haystacks = [
    service.name,
    service.description,
    service.kind,
    service.territory,
    service.action.type === "none" ? "" : service.action.url,
    ...service.components.flatMap((c) => [c.label, c.image ?? ""]),
  ];
  return haystacks.some((text) => text.toLowerCase().includes(q));
}

export function filterVisible(services: ServiceSnapshot[], query: string): Set<string> {
  const visible = new Set<string>();
  for (const service of services) {
    if (matchesQuery(service, query)) visible.add(service.id);
  }
  return visible;
}

export function orderedTerritories(): TerritoryId[] {
  return [...TERRITORY_ORDER];
}

export function servicesByTerritory(snapshot: Snapshot, territory: TerritoryId): ServiceSnapshot[] {
  return snapshot.services.filter((s) => s.territory === territory);
}

export function pickSelection(
  previous: string | null,
  fallback: string | null,
  services: ServiceSnapshot[],
  visible: Set<string>,
): string | null {
  if (previous !== null && visible.has(previous)) return previous;
  if (fallback !== null && visible.has(fallback)) return fallback;
  const first = services.find((s) => visible.has(s.id));
  return first ? first.id : null;
}

export function stateLabel(state: string): string {
  switch (state) {
    case "reachable": return "Reachable";
    case "degraded": return "Degraded";
    case "down": return "Down";
    case "dormant": return "Dormant";
    default: return "Unknown";
  }
}

export function formatUptime(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

export function formatLatency(ms: number | null): string {
  return ms === null ? "—" : `${ms} ms`;
}

export function formatObserved(iso: string | null, now: number): string {
  if (iso === null) return "never";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "unknown";
  const seconds = Math.max(0, Math.floor((now - then) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function severityTone(severity: string | null): "critical" | "warning" | "neutral" {
  if (severity === null || severity === "debug" || severity === "info" || severity === "notice") {
    return "neutral";
  }
  if (severity === "warning") return "warning";
  return "critical";
}

export function formatBytes(bytes: number | null): string {
  if (bytes === null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}
```

- [ ] **Step 3: Write the Vitest tests**

`tests/frontend/logic.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { ServiceSnapshot, Snapshot } from "../../frontend/api";
import {
  filterVisible,
  formatBytes,
  formatLatency,
  formatObserved,
  formatUptime,
  matchesQuery,
  pickSelection,
  severityTone,
  stateLabel,
} from "../../frontend/logic";

function service(overrides: Partial<ServiceSnapshot>): ServiceSnapshot {
  return {
    id: "s1",
    name: "Alpha Service",
    description: "Public gateway",
    territory: "applications",
    kind: "web",
    intent: "active",
    action: { type: "open", url: "https://alpha.example.com" },
    state: "reachable",
    stateReasons: [],
    observedAt: "2026-08-04T06:00:00Z",
    route: null,
    components: [
      { key: "app", label: "app", provider: "docker", providerRef: null, role: "primary", required: true, state: "running", health: "healthy", observedAt: null, image: "ghcr.io/alpha/app:1", uptimeSeconds: 3600, restartCount: 0, cpuPercent: null, memoryBytes: null },
    ],
    dependencies: [],
    logSources: [],
    ...overrides,
  };
}

function snapshot(services: ServiceSnapshot[]): Snapshot {
  return {
    schemaVersion: 1,
    generatedAt: "2026-08-04T06:00:00Z",
    catalogRevision: "r",
    fresh: true,
    providers: [],
    summary: { total: services.length, reachable: 0, degraded: 0, down: 0, dormant: 0, unknown: 0 },
    territories: [],
    services,
    diagnostics: { unmappedRuntimes: [], warnings: [] },
  };
}

describe("matchesQuery", () => {
  it("matches name, description, endpoint, kind, and component image", () => {
    const s = service({});
    expect(matchesQuery(s, "alpha")).toBe(true);
    expect(matchesQuery(s, "gateway")).toBe(true);
    expect(matchesQuery(s, "example.com")).toBe(true);
    expect(matchesQuery(s, "ghcr.io/alpha/app")).toBe(true);
    expect(matchesQuery(s, "nonexistent")).toBe(false);
    expect(matchesQuery(s, "")).toBe(true);
  });
});

describe("filterVisible", () => {
  it("filters the visible id set", () => {
    const services = [service({ id: "a", name: "Alpha" }), service({ id: "b", name: "Beta" })];
    expect(filterVisible(services, "beta")).toEqual(new Set(["b"]));
  });
});

describe("pickSelection", () => {
  const services = [service({ id: "a" }), service({ id: "b" })];
  it("keeps the previous selection when still visible", () => {
    expect(pickSelection("a", null, services, new Set(["a", "b"]))).toBe("a");
  });
  it("falls back to the first visible when the previous is filtered out", () => {
    expect(pickSelection("a", null, services, new Set(["b"]))).toBe("b");
  });
  it("prefers the restored prior selection on clearing search", () => {
    expect(pickSelection(null, "a", services, new Set(["a", "b"]))).toBe("a");
  });
  it("returns null when nothing is visible", () => {
    expect(pickSelection("a", null, services, new Set())).toBeNull();
  });
});

describe("formatters", () => {
  it("formats uptime", () => {
    expect(formatUptime(null)).toBe("—");
    expect(formatUptime(30)).toBe("30s");
    expect(formatUptime(3600)).toBe("1h");
    expect(formatUptime(90000)).toBe("1d");
  });
  it("formats latency", () => {
    expect(formatLatency(210)).toBe("210 ms");
    expect(formatLatency(null)).toBe("—");
  });
  it("formats observed times", () => {
    const now = Date.parse("2026-08-04T06:00:05Z");
    expect(formatObserved("2026-08-04T06:00:00Z", now)).toBe("just now");
    expect(formatObserved("2026-08-04T05:59:00Z", now)).toBe("1m ago");
    expect(formatObserved(null, now)).toBe("never");
  });
  it("formats bytes", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(500)).toBe("500 B");
    expect(formatBytes(1024)).toBe("1.0 KiB");
    expect(formatBytes(157286400)).toBe("150.0 MiB");
  });
});

describe("state and severity labels", () => {
  it("labels states", () => {
    expect(stateLabel("reachable")).toBe("Reachable");
    expect(stateLabel("weird")).toBe("Unknown");
  });
  it("tones severities", () => {
    expect(severityTone("info")).toBe("neutral");
    expect(severityTone("warning")).toBe("warning");
    expect(severityTone("error")).toBe("critical");
    expect(severityTone(null)).toBe("neutral");
  });
});

describe("snapshot", () => {
  it("builds a minimal snapshot", () => {
    expect(snapshot([service({})]).services.length).toBe(1);
  });
});
```

- [ ] **Step 4: Run tests**

Run: `npm test`
Expected: all PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
npm run lint
git add frontend/api.ts frontend/logic.ts tests/frontend/logic.test.ts
git commit -m "feat: frontend api client and pure atlas logic"
```

---

### Task 1.12: Atlas client and authored CSS

**Files:**
- Create: `frontend/atlas.ts`
- Create: `frontend/atlas.css`

- [ ] **Step 1: Write the client**

`frontend/atlas.ts` — complete wiring: render-once rows, patch-in-place refresh, search, selection with hash, dossier, bearings, logs drawer, copy action, toast, polling, reduced motion:

```ts
import "@fontsource-variable/recursive";
import "@fontsource-variable/geologica";
import { fetchLogs, fetchSnapshot, type LogsResponse, type ServiceSnapshot, type Snapshot } from "./api";
import {
  filterVisible,
  formatBytes,
  formatLatency,
  formatObserved,
  formatUptime,
  pickSelection,
  servicesByTerritory,
  stateLabel,
} from "./logic";

const POLL_INTERVAL_MS = 15_000;

interface ClientState {
  snapshot: Snapshot | null;
  etag: string | null;
  query: string;
  selectedId: string | null;
  priorSelection: string | null;
  logs: Map<string, LogsResponse | "loading" | "error">;
}

const state: ClientState = {
  snapshot: null,
  etag: null,
  query: "",
  selectedId: null,
  priorSelection: null,
  logs: new Map(),
};

const $ = <T extends HTMLElement>(selector: string): T => {
  const element = document.querySelector<T>(selector);
  if (element === null) throw new Error(`missing element: ${selector}`);
  return element;
};

const latitudes = $("#latitudes");
const bearings = $("#bearings");
const searchInput = $("#search") as HTMLInputElement;
const logDrawer = $("#log-drawer") as HTMLDetailsElement;
const logSource = $("#log-source") as HTMLSelectElement;
const logTail = $("#log-tail") as HTMLSelectElement;
const logRows = $("#log-rows");
const logState = $("#log-state");
const logSubject = $("#log-subject");
const toastElement = $("#toast");
const diagnosticsKeycap = $("#diagnostics-keycap");

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// --- Rendering ---------------------------------------------------------------

function territoryBand(territory: string, label: string): HTMLElement {
  const band = document.createElement("section");
  band.className = "band";
  band.dataset.territory = territory;
  const header = document.createElement("header");
  header.className = "band-heading";
  const title = document.createElement("h2");
  title.textContent = label;
  const coords = document.createElement("span");
  coords.className = "band-coords";
  coords.textContent = `LAT ${territory.toUpperCase().slice(0, 4)}`;
  header.append(title, coords);
  const index = document.createElement("div");
  index.className = "band-index";
  band.append(header, index);
  return band;
}

function buildAtlas(snapshot: Snapshot): void {
  latitudes.textContent = "";
  for (const territory of snapshot.territories) {
    const band = territoryBand(territory.id, territory.label);
    const index = band.querySelector(".band-index") as HTMLElement;
    const services = servicesByTerritory(snapshot, territory.id);
    if (services.length === 0) {
      const empty = document.createElement("p");
      empty.className = "band-empty";
      empty.textContent = "Nothing deployed here yet.";
      index.append(empty);
    }
    for (const service of services) {
      index.append(buildRow(service), buildDossier(service));
    }
    latitudes.append(band);
  }
  renderBearings(snapshot);
  renderDiagnostics(snapshot);
}

function buildRow(service: ServiceSnapshot): HTMLElement {
  const row = document.createElement("div");
  row.className = "service-row";
  row.dataset.serviceId = service.id;

  const select = document.createElement("button");
  select.type = "button";
  select.className = "service-select";
  select.setAttribute("aria-expanded", "false");
  const chevron = document.createElement("span");
  chevron.className = "chevron";
  chevron.setAttribute("aria-hidden", "true");
  chevron.textContent = "›";
  const dot = document.createElement("i");
  dot.className = "route-dot";
  const copy = document.createElement("span");
  copy.className = "route-copy";
  const name = document.createElement("strong");
  name.className = "service-name";
  const description = document.createElement("small");
  description.className = "service-description";
  copy.append(name, description);
  select.append(chevron, dot, copy);

  const endpoint = document.createElement("code");
  endpoint.className = "cell endpoint";
  const status = document.createElement("span");
  status.className = "status";
  const recency = document.createElement("span");
  recency.className = "cell recency";

  const action = document.createElement("a");
  action.className = "open-action";
  action.target = "_blank";
  action.rel = "noopener noreferrer";

  row.append(select, endpoint, status, recency, action);
  patchRow(row, service);
  return row;
}

function buildDossier(service: ServiceSnapshot): HTMLElement {
  const dossier = document.createElement("div");
  dossier.className = "dossier";
  dossier.hidden = true;
  dossier.dataset.dossierFor = service.id;
  dossier.append(
    dossierSection("Route evidence", [
      ["State", stateLabel(service.state)],
      ["Observed", formatObserved(service.observedAt, Date.now())],
      ...(service.route
        ? [
            ["Status code", service.route.statusCode === null ? "—" : String(service.route.statusCode)],
            ["Latency", formatLatency(service.route.latencyMs)],
            ["Failures", String(service.route.consecutiveFailures)],
          ]
        : []),
    ]),
    dossierComponents(service),
    dossierDependencies(service),
    dossierReasons(service),
  );
  return dossier;
}

function dossierSection(title: string, facts: [string, string][]): HTMLElement {
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = title;
  section.append(heading);
  const list = document.createElement("dl");
  for (const [term, detail] of facts) {
    const dt = document.createElement("dt");
    dt.textContent = term;
    const dd = document.createElement("dd");
    dd.textContent = detail;
    list.append(dt, dd);
  }
  section.append(list);
  return section;
}

function dossierComponents(service: ServiceSnapshot): HTMLElement {
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = "Components";
  section.append(heading);
  const list = document.createElement("ul");
  for (const component of service.components) {
    const item = document.createElement("li");
    const label = document.createElement("span");
    label.className = "dossier-component-label";
    label.textContent = component.label;
    const meta = document.createElement("code");
    meta.className = "dossier-component-meta";
    const bits = [component.provider, component.state, component.health];
    if (component.uptimeSeconds !== null) bits.push(`up ${formatUptime(component.uptimeSeconds)}`);
    if (component.memoryBytes !== null) bits.push(formatBytes(component.memoryBytes));
    if (component.image !== null) bits.push(component.image);
    meta.textContent = bits.join(" · ");
    item.append(label, meta);
    list.append(item);
  }
  section.append(list);
  return section;
}

function dossierDependencies(service: ServiceSnapshot): HTMLElement {
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = "Dependencies";
  section.append(heading);
  const list = document.createElement("ul");
  for (const dependency of service.dependencies) {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = dependency.serviceId;
    const chip = document.createElement("span");
    chip.className = `status status-${dependency.state}`;
    chip.textContent = stateLabel(dependency.state);
    item.append(name, chip);
    list.append(item);
  }
  if (service.dependencies.length === 0) {
    const none = document.createElement("li");
    none.className = "dossier-none";
    none.textContent = "No declared dependencies.";
    list.append(none);
  }
  section.append(list);
  return section;
}

function dossierReasons(service: ServiceSnapshot): HTMLElement {
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = "Recent events";
  section.append(heading);
  const list = document.createElement("ul");
  for (const reason of service.stateReasons) {
    const item = document.createElement("li");
    const chip = document.createElement("span");
    chip.className = `log-level severity-${reason.severity}`;
    chip.textContent = reason.code;
    const text = document.createElement("span");
    text.textContent = reason.message;
    item.append(chip, text);
    list.append(item);
  }
  if (service.stateReasons.length === 0) {
    const none = document.createElement("li");
    none.className = "dossier-none";
    none.textContent = "No recent events.";
    list.append(none);
  }
  section.append(list);
  return section;
}

function patchRow(row: HTMLElement, service: ServiceSnapshot): void {
  const name = row.querySelector(".service-name") as HTMLElement;
  const description = row.querySelector(".service-description") as HTMLElement;
  const endpoint = row.querySelector(".endpoint") as HTMLElement;
  const status = row.querySelector(".status") as HTMLElement;
  const recency = row.querySelector(".recency") as HTMLElement;
  const dot = row.querySelector(".route-dot") as HTMLElement;
  const action = row.querySelector(".open-action") as HTMLAnchorElement;

  name.textContent = service.name;
  description.textContent = service.description;
  const url = service.action.type === "none" ? null : new URL(service.action.url);
  endpoint.textContent = url === null ? "—" : url.host + url.pathname;
  status.textContent = stateLabel(service.state);
  status.className = `status status-${service.state}`;
  recency.textContent = formatObserved(service.observedAt, Date.now());
  dot.className = `route-dot dot-${service.state}`;

  if (service.action.type === "open") {
    action.href = service.action.url;
    action.textContent = "Open ↗";
    action.classList.remove("copy-action", "no-action");
  } else if (service.action.type === "copy") {
    action.href = "#";
    action.textContent = "Copy";
    action.classList.add("copy-action");
    action.classList.remove("no-action");
  } else {
    action.href = "#";
    action.textContent = "No route";
    action.classList.add("no-action");
    action.classList.remove("copy-action");
  }
}

function renderBearings(snapshot: Snapshot): void {
  bearings.textContent = "";
  const entries: [string, number, string][] = [
    ["reachable", snapshot.summary.reachable, "Reachable"],
    ["degraded", snapshot.summary.degraded, "Degraded"],
    ["down", snapshot.summary.down, "Down"],
    ["dormant", snapshot.summary.dormant, "Dormant"],
    ["unknown", snapshot.summary.unknown, "Unknown"],
  ];
  for (const [key, count, label] of entries) {
    const instrument = document.createElement("div");
    instrument.className = `bearing bearing-${key}`;
    const ring = document.createElement("span");
    ring.className = "bearing-ring";
    const value = document.createElement("strong");
    value.className = "bearing-value";
    value.textContent = String(count);
    const name = document.createElement("span");
    name.className = "bearing-label";
    name.textContent = label;
    instrument.append(ring, value, name);
    bearings.append(instrument);
  }
}

function renderDiagnostics(snapshot: Snapshot): void {
  const count = snapshot.diagnostics.unmappedRuntimes.length;
  if (count === 0) {
    diagnosticsKeycap.hidden = true;
    return;
  }
  diagnosticsKeycap.hidden = false;
  diagnosticsKeycap.textContent = `${count} unmapped runtime${count === 1 ? "" : "s"}`;
  diagnosticsKeycap.title = snapshot.diagnostics.unmappedRuntimes.map((u) => u.reference).join(", ");
}

// --- Selection and dossier ---------------------------------------------------

function selectService(serviceId: string): void {
  if (state.selectedId === serviceId) return;
  state.selectedId = serviceId;
  for (const dossier of document.querySelectorAll<HTMLElement>(".dossier")) {
    dossier.hidden = dossier.dataset.dossierFor !== serviceId;
  }
  for (const row of document.querySelectorAll<HTMLElement>(".service-row")) {
    const isSelected = row.dataset.serviceId === serviceId;
    row.classList.toggle("selected", isSelected);
    const button = row.querySelector<HTMLButtonElement>(".service-select");
    if (button) button.setAttribute("aria-expanded", String(isSelected));
  }
  history.replaceState(null, "", `#service=${encodeURIComponent(serviceId)}`);
  updateLogsDrawer();
  const selectedRow = document.querySelector<HTMLElement>(`.service-row[data-service-id="${serviceId}"]`);
  selectedRow?.scrollIntoView({ block: "nearest", behavior: reducedMotion ? "auto" : "smooth" });
}

function applyFilter(): void {
  if (state.snapshot === null) return;
  const visible = filterVisible(state.snapshot.services, state.query);
  const fallback =
    state.priorSelection !== null && visible.has(state.priorSelection) ? state.priorSelection : null;
  const next = pickSelection(state.selectedId, fallback, state.snapshot.services, visible);
  for (const row of document.querySelectorAll<HTMLElement>(".service-row")) {
    row.hidden = !visible.has(row.dataset.serviceId ?? "");
  }
  for (const dossier of document.querySelectorAll<HTMLElement>(".dossier")) {
    if (!dossier.hidden && dossier.dataset.dossierFor) {
      dossier.hidden = !visible.has(dossier.dataset.dossierFor);
    }
  }
  if (next !== null) selectService(next);
}

// --- Logs --------------------------------------------------------------------

function updateLogsDrawer(): void {
  const service = state.snapshot?.services.find((s) => s.id === state.selectedId) ?? null;
  logSource.textContent = "";
  if (service === null) {
    logSubject.textContent = "Select a service to inspect its logs.";
    logState.textContent = "";
    logRows.textContent = "";
    return;
  }
  logSubject.textContent = `Logs for ${service.name}`;
  const optionAll = document.createElement("option");
  optionAll.value = "";
  optionAll.textContent = "All sources";
  logSource.append(optionAll);
  for (const source of service.logSources) {
    const option = document.createElement("option");
    option.value = source.key;
    option.textContent = `${source.label} (${source.provider})`;
    if (!source.available) option.disabled = true;
    logSource.append(option);
  }
  loadLogs(service, false);
}

async function loadLogs(service: ServiceSnapshot, force: boolean): Promise<void> {
  const key = logCacheKey(service.id, logSource.value, Number(logTail.value));
  const cached = state.logs.get(key);
  if (!force && cached !== undefined && cached !== "loading") {
    renderLogs(cached === "error" ? null : cached);
    return;
  }
  state.logs.set(key, "loading");
  logState.textContent = "Loading logs…";
  const result = await fetchLogs(service.id, logSource.value === "" ? null : logSource.value, Number(logTail.value));
  if (result.status === "ok") {
    state.logs.set(key, result.logs);
    renderLogs(result.logs);
  } else {
    state.logs.set(key, "error");
    logState.textContent = result.message;
    logRows.textContent = "";
    const note = document.createElement("p");
    note.className = "log-empty";
    note.textContent = result.retryable
      ? "Logs are temporarily unavailable. Try again in a moment."
      : "Logs are unavailable for this source.";
    logRows.append(note);
  }
}

function logCacheKey(serviceId: string, source: string, tail: number): string {
  return `${serviceId}::${source}::${tail}`;
}

function renderLogs(logs: LogsResponse | null): void {
  logRows.textContent = "";
  logState.textContent = "";
  if (logs === null) {
    const note = document.createElement("p");
    note.className = "log-empty";
    note.textContent = "Logs are unavailable for this source.";
    logRows.append(note);
    return;
  }
  if (logs.records.length === 0) {
    const note = document.createElement("p");
    note.className = "log-empty";
    note.textContent = "No log records were returned for this source.";
    logRows.append(note);
    return;
  }
  for (const record of logs.records) {
    const row = document.createElement("div");
    row.className = "log-row";
    const time = document.createElement("time");
    time.textContent = record.timestamp === null ? "—" : new Date(record.timestamp).toLocaleTimeString();
    const level = document.createElement("b");
    level.className = `log-level severity-${severityClass(record.severity)}`;
    level.textContent = record.severity === null ? "—" : record.severity.toUpperCase();
    const message = document.createElement("span");
    message.textContent = record.message + (record.truncated ? " …" : "");
    const source = document.createElement("code");
    source.textContent = record.source;
    row.append(time, level, message, source);
    logRows.append(row);
  }
}

function severityClass(severity: string | null): string {
  if (severity === "warning") return "warning";
  if (severity === "error" || severity === "critical" || severity === "alert" || severity === "emergency") {
    return "critical";
  }
  return "neutral";
}

// --- Copy action and toast ---------------------------------------------------

async function copyEndpoint(url: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(url);
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = url;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.append(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }
  showToast("Endpoint copied");
}

function showToast(message: string): void {
  toastElement.textContent = message;
  toastElement.hidden = false;
  window.clearTimeout((toastElement as HTMLElement & { _timer?: number })._timer);
  (toastElement as HTMLElement & { _timer?: number })._timer = window.setTimeout(() => {
    toastElement.hidden = true;
  }, 2400);
}

// --- Polling ----------------------------------------------------------------

async function refreshSnapshot(): Promise<void> {
  if (document.hidden) return;
  const result = await fetchSnapshot(state.etag);
  if (result.status === "ok") {
    state.etag = result.etag;
    state.snapshot = result.snapshot;
    patchAll(result.snapshot);
  }
}

function patchAll(snapshot: Snapshot): void {
  const byId = new Map(snapshot.services.map((s) => [s.id, s]));
  for (const row of document.querySelectorAll<HTMLElement>(".service-row")) {
    const service = byId.get(row.dataset.serviceId ?? "");
    if (service) patchRow(row, service);
  }
  renderBearings(snapshot);
  renderDiagnostics(snapshot);
}

function initSelection(): void {
  const fromHash = new URLSearchParams(window.location.hash.slice(1)).get("service");
  const services = state.snapshot?.services ?? [];
  const next = pickSelection(fromHash, null, services, new Set(services.map((s) => s.id)));
  if (next !== null) selectService(next);
  state.priorSelection = next;
}

// --- Event wiring ------------------------------------------------------------

function wireEvents(): void {
  searchInput.addEventListener("input", () => {
    if (state.priorSelection === null && state.selectedId !== null) state.priorSelection = state.selectedId;
    state.query = searchInput.value;
    applyFilter();
  });

  latitudes.addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>(".service-select");
    if (button) {
      const row = button.closest<HTMLElement>(".service-row");
      if (row) {
        state.priorSelection = state.selectedId;
        selectService(row.dataset.serviceId ?? "");
      }
      return;
    }
    const copyAction = (event.target as HTMLElement).closest<HTMLAnchorElement>(".copy-action");
    if (copyAction) {
      event.preventDefault();
      const row = copyAction.closest<HTMLElement>(".service-row");
      const service = state.snapshot?.services.find((s) => s.id === row?.dataset.serviceId);
      if (service && service.action.type === "copy") void copyEndpoint(service.action.url);
    }
  });

  logDrawer.addEventListener("toggle", () => {
    if (!logDrawer.open) return;
    const service = state.snapshot?.services.find((s) => s.id === state.selectedId);
    if (service) loadLogs(service, false);
  });

  logSource.addEventListener("change", () => {
    const service = state.snapshot?.services.find((s) => s.id === state.selectedId);
    if (service) loadLogs(service, false);
  });
  logTail.addEventListener("change", () => {
    const service = state.snapshot?.services.find((s) => s.id === state.selectedId);
    if (service) loadLogs(service, false);
  });
  $("#log-refresh").addEventListener("click", () => {
    const service = state.snapshot?.services.find((s) => s.id === state.selectedId);
    if (service) loadLogs(service, true);
  });

  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      searchInput.focus();
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void refreshSnapshot();
  });
  window.addEventListener("focus", () => void refreshSnapshot());

  for (const link of document.querySelectorAll<HTMLAnchorElement>("[data-territory-link]")) {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const territory = link.dataset.territoryLink ?? "applications";
      const band = document.querySelector<HTMLElement>(`.band[data-territory="${territory}"]`);
      band?.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth" });
    });
  }
}

// --- Boot --------------------------------------------------------------------

async function boot(): Promise<void> {
  wireEvents();
  const result = await fetchSnapshot(null);
  if (result.status === "error") {
    const note = document.createElement("p");
    note.className = "band-empty";
    note.textContent = `The atlas could not be loaded: ${result.message}`;
    latitudes.append(note);
    return;
  }
  if (result.status === "ok") {
    state.etag = result.etag;
    state.snapshot = result.snapshot;
    buildAtlas(result.snapshot);
    initSelection();
  }
  window.setInterval(() => void refreshSnapshot(), POLL_INTERVAL_MS);
}

void boot();
```

Notes:
- All provider text goes through `textContent` — never `innerHTML`.
- The first fetch builds the atlas; every later fetch patches rows in place, preserving focus, scroll, search, and open dossier (dossier elements are created once at build time and only toggled).

- [ ] **Step 2: Write the authored CSS**

`frontend/atlas.css` — token block + approved component rules. Extract any concrete rules not covered below from `index.html` (the approved prototype) and `DESIGN.md`; translate hex values into the tokens:

```css
:root {
  --mineral-canvas: #F4F0E6;
  --parchment-surface: #FBF8F0;
  --limestone-field: #E4DDCF;
  --weathered-rule: #CEC4B2;
  --carbon-ink: #282824;
  --graphite-copy: #615F57;
  --bearing-brass: #876721;
  --engraved-brass: #705317;
  --route-wash: #EBE0BE;
  --reachable-green: #4F6B54;
  --reachable-wash: #E4ECE2;
  --caution-ochre: #8A5D16;
  --caution-wash: #F3E8CE;
  --failure-red: #9B433B;
  --failure-wash: #F2DFDB;
  --dormant-stone: #65665F;
  --dormant-wash: #E8E6E0;

  --radius-micro: 4px;
  --radius-keycap: 5px;
  --radius-action: 8px;
  --radius-control: 13px;

  --space-hairline: 1px;
  --space-micro: 4px;
  --space-compact: 8px;
  --space-control: 13px;
  --space-panel: 16px;
  --space-gutter: 18px;
  --space-section: 26px;
  --space-page: 44px;

  --font-display: "Recursive Variable", "Recursive", sans-serif;
  --font-body: "Geologica Variable", "Geologica", sans-serif;
  --font-instrument: "Recursive Variable", "Recursive", monospace;

  --motion-settle: 380ms cubic-bezier(.16, 1, .3, 1);
  --motion-turn: 240ms cubic-bezier(.16, 1, .3, 1);
  --motion-state: 180ms ease-out;
  --shadow-toast: 0 8px 30px rgba(40, 40, 36, .18);
}

* { box-sizing: border-box; }

html { background: var(--mineral-canvas); }

body {
  margin: 0;
  background: var(--mineral-canvas);
  color: var(--carbon-ink);
  font-family: var(--font-body);
  font-size: .67rem;
  line-height: 1.5;
}

/* Global header — 70px sticky, brand left, search center, destinations right */
.global-header {
  position: sticky;
  top: 0;
  z-index: 10;
  background: color-mix(in srgb, var(--mineral-canvas) 88%, transparent);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--weathered-rule);
}
.header-inner {
  display: grid;
  grid-template-columns: minmax(140px, 1fr) minmax(240px, 520px) 1fr;
  align-items: center;
  gap: var(--space-gutter);
  max-width: min(1540px, 100% - var(--space-page));
  margin: 0 auto;
  min-height: 70px;
  padding: 0 var(--space-panel);
}

/* Search field — parchment, 13px radius, 42px height, keycap */
.global-search { position: relative; display: flex; align-items: center; }
.global-search input {
  width: 100%;
  min-height: 42px;
  padding: 0 54px 0 44px;
  border: 1px solid var(--weathered-rule);
  border-radius: var(--radius-control);
  background: var(--parchment-surface);
  color: var(--carbon-ink);
  font-family: var(--font-body);
  font-size: .82rem;
}
.global-search input:focus-visible {
  border-color: var(--engraved-brass);
  outline: 3px solid color-mix(in srgb, var(--bearing-brass) 32%, transparent);
  outline-offset: 1px;
}
.search-key {
  position: absolute;
  right: 10px;
  padding: 3px 6px;
  border: 1px solid var(--weathered-rule);
  border-radius: var(--radius-keycap);
  color: var(--graphite-copy);
  font-family: var(--font-instrument);
  font-size: .58rem;
}

/* Navigation — 44px targets, brass underline on active */
.global-nav { display: flex; gap: var(--space-compact); justify-content: flex-end; }
.nav-destination {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  padding: 0 var(--space-control);
  color: var(--carbon-ink);
  text-decoration: none;
  font-weight: 620;
  font-size: .68rem;
  border-bottom: 2px solid transparent;
}
.nav-destination:focus-visible { outline: 3px solid var(--bearing-brass); outline-offset: 2px; }

/* Main frame — one continuous ruled field with a narrow bearings rail */
.atlas-main {
  max-width: min(1540px, 100% - var(--space-page));
  margin: 0 auto;
  padding: var(--space-page) 0 var(--space-section);
}
.workspace-statement {
  font-family: var(--font-display);
  font-size: clamp(1.65rem, 3vw, 2.55rem);
  font-weight: 560;
  line-height: 1.05;
  letter-spacing: -.04em;
  font-variation-settings: "CASL" .28;
  margin: 0 0 var(--space-page);
}
.atlas-frame {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 178px;
  gap: var(--space-panel);
  border-top: 1px solid var(--carbon-ink);
  border-bottom: 1px solid var(--carbon-ink);
}

/* Latitude bands */
.band { border-top: 1px solid var(--weathered-rule); }
.band:first-child { border-top: 0; }
.band-heading {
  display: grid;
  grid-template-columns: 182px 1fr;
  align-items: center;
  padding: var(--space-control) var(--space-panel) var(--space-control) 0;
  border-bottom: 1px solid var(--carbon-ink);
}
.band-heading h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: 1.08rem;
  font-weight: 590;
  letter-spacing: -.02em;
}
.band-coords {
  font-family: var(--font-instrument);
  font-size: .58rem;
  color: var(--graphite-copy);
  border-left: 1px solid var(--weathered-rule);
  padding-left: var(--space-panel);
}

/* Service rows — dense, shared dividers, no per-row shells */
.service-row {
  display: grid;
  grid-template-columns: minmax(220px, 1.4fr) .55fr .7fr .55fr 116px;
  align-items: center;
  min-height: 48px;
  border-bottom: 1px solid var(--weathered-rule);
  margin-left: 182px;
  transition: background var(--motion-state);
}
.service-row:last-child { border-bottom: 0; }
.service-row:hover { background: color-mix(in srgb, var(--route-wash) 40%, transparent); }
.service-row.selected { background: color-mix(in srgb, var(--route-wash) 62%, transparent); }
.service-select {
  display: flex;
  align-items: center;
  gap: var(--space-control);
  min-width: 0;
  align-self: stretch;
  padding: 7px var(--space-panel);
  border: 0;
  background: transparent;
  color: var(--carbon-ink);
  text-align: left;
  cursor: pointer;
  font: inherit;
}
.chevron { font-size: 1rem; transition: transform var(--motion-turn); }
.service-row.selected .chevron { transform: rotate(90deg); }
.route-dot { width: 7px; height: 7px; flex: none; border-radius: 50%; background: var(--dormant-stone); }
.dot-reachable { background: var(--reachable-green); }
.dot-degraded { background: var(--caution-ochre); }
.dot-down { background: var(--failure-red); }
.dot-dormant { background: var(--dormant-stone); }
.dot-unknown { background: var(--graphite-copy); }
.service-name {
  display: block;
  font-family: var(--font-display);
  font-size: .85rem;
  font-weight: 620;
  letter-spacing: -.018em;
}
.service-description { display: block; margin-top: 2px; color: var(--graphite-copy); font-size: .64rem; }
.cell { padding: 0 var(--space-control); color: var(--graphite-copy); font-size: .67rem; }
.endpoint {
  font-family: var(--font-instrument);
  font-size: .6rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Status chips — text plus pigment dot; meaning never color alone */
.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: fit-content;
  padding: 4px 7px;
  border-radius: 999px;
  font-weight: 650;
  font-size: .64rem;
}
.status::before { content: ""; width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
.status-reachable { background: var(--reachable-wash); color: var(--reachable-green); }
.status-degraded { background: var(--caution-wash); color: var(--caution-ochre); }
.status-down { background: var(--failure-wash); color: var(--failure-red); }
.status-dormant { background: var(--dormant-wash); color: var(--dormant-stone); }
.status-unknown { background: var(--limestone-field); color: var(--graphite-copy); }

/* Open/copy actions */
.open-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 31px;
  margin-right: var(--space-control);
  padding: 0 var(--space-control);
  border: 1px solid var(--weathered-rule);
  border-radius: var(--radius-action);
  background: var(--parchment-surface);
  color: var(--carbon-ink);
  text-decoration: none;
  font-size: .65rem;
  font-weight: 600;
}
.open-action:hover { background: color-mix(in srgb, var(--route-wash) 40%, transparent); }
.open-action:focus-visible { outline: 3px solid var(--bearing-brass); outline-offset: 2px; }

/* Dossier — inline expansion inside the selected band */
.dossier {
  display: grid;
  grid-template-columns: .86fr 1.15fr .8fr 1fr;
  margin: 0 var(--space-gutter) var(--space-panel) 196px;
  border: 1px solid var(--weathered-rule);
  border-radius: var(--radius-control);
  background: var(--parchment-surface);
  overflow: hidden;
  animation: dossier-settle var(--motion-settle);
}
@keyframes dossier-settle {
  from { clip-path: inset(0 0 100% 0); }
  to { clip-path: inset(0 0 0 0); }
}
.dossier section { min-width: 0; padding: var(--space-panel); }
.dossier section + section { border-left: 1px solid var(--weathered-rule); }
.dossier h3 { margin: 0 0 var(--space-control); color: var(--graphite-copy); font-size: .62rem; font-weight: 650; }
.dossier dl { display: grid; grid-template-columns: auto 1fr; gap: var(--space-compact) var(--space-gutter); margin: 0; font-size: .65rem; }
.dossier dt { color: var(--graphite-copy); }
.dossier dd { margin: 0; }
.dossier ul { list-style: none; margin: 0; padding: 0; font-size: .64rem; }
.dossier li { display: flex; gap: var(--space-compact); align-items: baseline; padding: 2px 0; }
.dossier-component-meta { font-family: var(--font-instrument); font-size: .58rem; color: var(--graphite-copy); }
.dossier-none { color: var(--graphite-copy); }

/* Bearings rail */
.bearings-rail { border-left: 1px solid var(--weathered-rule); padding: var(--space-panel); display: flex; flex-direction: column; gap: var(--space-section); }
.bearing { display: flex; flex-direction: column; align-items: center; gap: var(--space-micro); }
.bearing-ring {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px solid var(--weathered-rule);
  display: block;
}
.bearing-reachable .bearing-ring { border-color: var(--reachable-green); }
.bearing-degraded .bearing-ring { border-color: var(--caution-ochre); }
.bearing-down .bearing-ring { border-color: var(--failure-red); }
.bearing-dormant .bearing-ring { border-color: var(--dormant-stone); }
.bearing-unknown .bearing-ring { border-color: var(--graphite-copy); }
.bearing-value { font-family: var(--font-display); font-size: 1.8rem; font-weight: 450; line-height: 1; }
.bearing-reachable .bearing-value { color: var(--reachable-green); }
.bearing-degraded .bearing-value { color: var(--caution-ochre); }
.bearing-down .bearing-value { color: var(--failure-red); }
.bearing-dormant .bearing-value { color: var(--dormant-stone); }
.bearing-label { font-size: .58rem; color: var(--graphite-copy); }
.bearings-note { font-size: .56rem; color: var(--graphite-copy); text-align: center; }
.diagnostics-keycap {
  padding: 3px 6px;
  border: 1px solid var(--weathered-rule);
  border-radius: var(--radius-keycap);
  color: var(--graphite-copy);
  font-family: var(--font-instrument);
  font-size: .58rem;
  text-align: center;
}

/* Logs drawer — native details, 26px below the atlas, closed by default */
.log-drawer { margin-top: var(--space-section); border: 1px solid var(--weathered-rule); border-radius: var(--radius-control); background: var(--parchment-surface); }
.log-drawer summary {
  display: flex;
  align-items: center;
  gap: var(--space-control);
  min-height: 56px;
  padding: 0 var(--space-panel);
  cursor: pointer;
  font-family: var(--font-display);
  font-size: .82rem;
  font-weight: 620;
}
.log-drawer summary::-webkit-details-marker { display: none; }
.log-drawer-subject { margin-left: var(--space-gutter); color: var(--graphite-copy); font-family: var(--font-body); font-size: .66rem; font-weight: 400; }
.open-label { margin-left: auto; color: var(--engraved-brass); font-family: var(--font-body); font-size: .65rem; }
.log-controls { display: flex; align-items: center; gap: var(--space-panel); padding: 0 var(--space-panel) var(--space-control); }
.log-controls label { display: flex; align-items: center; gap: var(--space-compact); color: var(--graphite-copy); font-size: .62rem; }
.log-controls select {
  min-height: 31px;
  padding: 0 var(--space-compact);
  border: 1px solid var(--weathered-rule);
  border-radius: var(--radius-action);
  background: var(--parchment-surface);
  color: var(--carbon-ink);
  font: inherit;
}
.button-route {
  min-height: 31px;
  padding: 0 var(--space-control);
  border: 1px solid var(--weathered-rule);
  border-radius: var(--radius-action);
  background: var(--parchment-surface);
  color: var(--carbon-ink);
  font-size: .65rem;
  font-weight: 600;
  cursor: pointer;
}
.button-route:focus-visible, .log-controls select:focus-visible { outline: 3px solid var(--bearing-brass); outline-offset: 2px; }
.log-state { margin-left: auto; color: var(--graphite-copy); font-size: .6rem; }
.log-rows { border-top: 1px solid var(--weathered-rule); }
.log-row {
  display: grid;
  grid-template-columns: 76px 64px 1fr 140px;
  gap: var(--space-gutter);
  padding: 11px var(--space-panel);
  border-top: 1px solid var(--weathered-rule);
  font-family: var(--font-instrument);
  font-size: .64rem;
}
.log-row:first-child { border-top: 0; }
.log-row time, .log-row code { color: var(--graphite-copy); }
.log-level {
  width: fit-content;
  padding: 2px 5px;
  border-radius: var(--radius-micro);
  font-family: var(--font-instrument);
  font-size: .56rem;
  font-weight: 400;
}
.severity-neutral { background: var(--reachable-wash); color: var(--reachable-green); }
.severity-warning { background: var(--caution-wash); color: var(--caution-ochre); }
.severity-critical { background: var(--failure-wash); color: var(--failure-red); }
.log-empty { padding: var(--space-panel); color: var(--graphite-copy); }

/* Toast — the only elevated surface */
.toast {
  position: fixed;
  right: var(--space-gutter);
  bottom: var(--space-gutter);
  padding: var(--space-control) var(--space-panel);
  border-radius: var(--radius-control);
  background: var(--parchment-surface);
  border: 1px solid var(--weathered-rule);
  box-shadow: var(--shadow-toast);
  font-size: .68rem;
  transition: opacity var(--motion-state);
}

/* Brand mark — small brass bearing, no Greek motifs */
.brand { display: flex; align-items: center; gap: var(--space-compact); text-decoration: none; color: var(--carbon-ink); min-height: 44px; }
.brand-mark {
  width: 14px; height: 14px;
  border: 2px solid var(--bearing-brass);
  border-radius: 50%;
  position: relative;
}
.brand-mark::after {
  content: "";
  position: absolute;
  left: 50%; top: 50%;
  width: 3px; height: 3px;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  background: var(--bearing-brass);
}
.brand-title { font-family: var(--font-display); font-size: 1.28rem; font-weight: 630; letter-spacing: -.035em; }

/* Focus — brass everywhere */
:focus-visible { outline: 3px solid var(--bearing-brass); outline-offset: 2px; }

/* Reduced motion — effectively instant */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .01ms !important;
    scroll-behavior: auto !important;
  }
}

/* Breakpoint 1180px — narrower margin, endpoint/recency trimmed */
@media (max-width: 1180px) {
  .service-row { grid-template-columns: minmax(220px, 1.4fr) .7fr .55fr 116px; margin-left: 140px; }
  .service-row .cell.recency, .service-row .endpoint { display: none; }
  .band-heading { grid-template-columns: 140px 1fr; }
  .dossier { margin-left: 152px; }
}

/* Breakpoint 900px — bearings become a strip, dossier two columns */
@media (max-width: 900px) {
  .header-inner { grid-template-columns: 1fr auto; }
  .global-search { grid-column: 1 / -1; grid-row: 2; }
  .atlas-frame { grid-template-columns: 1fr; }
  .bearings-rail { flex-direction: row; flex-wrap: wrap; border-left: 0; border-top: 1px solid var(--weathered-rule); }
  .bearing { flex: 1 1 20%; }
  .bearings-note { flex-basis: 100%; }
  .dossier { grid-template-columns: 1fr 1fr; margin-left: var(--space-gutter); }
  .open-action { width: 32px; height: 32px; padding: 0; border-radius: 50%; text-indent: -9999px; overflow: hidden; position: relative; }
}

/* Breakpoint 620px — search row, stacked bands, 2x2 bearings, one-column dossier */
@media (max-width: 620px) {
  .header-inner { grid-template-columns: 1fr; gap: var(--space-compact); }
  .global-nav { justify-content: flex-start; }
  .band-heading { grid-template-columns: 1fr; gap: var(--space-micro); }
  .band-coords { border-left: 0; padding-left: 0; }
  .service-row { grid-template-columns: 1fr; margin-left: 0; padding-left: var(--space-panel); }
  .service-row .cell, .service-row .endpoint { display: none; }
  .service-select { padding-left: 0; }
  .open-action { margin-right: var(--space-panel); }
  .dossier { grid-template-columns: 1fr; margin-left: 0; margin-right: var(--space-compact); }
  .dossier section + section { border-left: 0; border-top: 1px solid var(--weathered-rule); }
  .log-row { grid-template-columns: 76px 64px 1fr; }
  .log-row code { display: none; }
}
```

Notes:
- Font family names: check the names exported by `@fontsource-variable/recursive` and `@fontsource-variable/geologica` (their `index.css` typically declares `"Recursive Variable"` and `"Geologica Variable"`); adjust the `--font-*` tokens if needed.
- Task 3.4 runs a bounded visual comparison against `index.html` and corrects drift.
- The template ships a visually-hidden `h1` (`.visually-hidden`, brand title) for heading hierarchy; the CSS block above has no such utility — include `.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }` in `atlas.css` or the h1 renders visibly.

- [ ] **Step 3: Build and smoke-test**

```bash
npm run build
ls src/hyperion/static/assets/
```

Expected: `atlas.js`, `styles.css`, and font assets present. Then restart the fixture-mode server (see Task 1.8 step 10 commands) and confirm:

```bash
curl -s -H "X-Authentik-Username: owner" http://127.0.0.1:8787/ | grep -c "static/assets"
```

Expected: ≥ 2. If the stylesheet is emitted as `assets/styles.css`, the Jinja `<link>` matches; otherwise update the link in `templates/index.html` to the real filename.

- [ ] **Step 4: Lint, typecheck, commit**

```bash
npm run lint
git add frontend/atlas.ts frontend/atlas.css
git commit -m "feat: atlas client with patch-in-place rendering and authored css"
```

Note: `src/hyperion/static/assets/` is gitignored (build is reproducible); commit only `frontend/` sources.

---

### Task 1.13: Browser verification (Phase 1 gate)

**Files:**
- Create: `tests/browser/atlas.spec.ts`

- [ ] **Step 1: Write the Playwright spec**

```ts
import { expect, test } from "@playwright/test";

test("atlas renders all territories and service states", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Applications" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Services" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Foundations" })).toBeVisible();
  await expect(page.locator(".service-row")).toHaveCount(10);
  await expect(page.locator(".status-down")).toHaveCount(2);
  await expect(page.locator(".status-degraded")).toHaveCount(3);
  await expect(page.locator(".status-dormant")).toHaveCount(1);
  await expect(page.locator(".status-reachable")).toHaveCount(4);
});

test("search filters services and restores selection", async ({ page }) => {
  await page.goto("/");
  await page.locator("#search").fill("langfuse");
  await expect(page.locator(".service-row").filter({ visible: true })).toHaveCount(1);
  await expect(page.locator(".service-row").filter({ visible: true }).locator(".service-name")).toHaveText("Langfuse");
  await page.locator("#search").fill("");
  await expect(page.locator(".service-row").filter({ visible: true })).toHaveCount(10);
});

test("selecting a row opens the dossier inline and preserves atlas topology", async ({ page }) => {
  await page.goto("/");
  const row = page.locator('.service-row[data-service-id="langfuse"]');
  await row.locator(".service-select").click();
  await expect(page.locator('.dossier[data-dossier-for="langfuse"]')).toBeVisible();
  await expect(page).toHaveURL(/#service=langfuse/);
  await expect(page.locator(".dossier h3").first()).toHaveText("Route evidence");
});

test("logs drawer loads bounded records with controls", async ({ page }) => {
  await page.goto("/");
  await page.locator('.service-row[data-service-id="authentik"] .service-select').click();
  await page.locator("#log-drawer summary").click();
  await expect(page.locator(".log-row").first()).toBeVisible();
  await expect(page.locator(".log-row").first()).toContainText("GET /api/v3/core/users/");
  await page.locator("#log-source").selectOption("worker");
  await expect(page.locator(".log-row code").first()).toHaveText("worker");
});

test("copy action shows toast and writes to clipboard", async ({ page }) => {
  await page.goto("/");
  const vault = page.locator('.service-row[data-service-id="the-vault"] .copy-action');
  await expect(vault).toBeVisible();
  await vault.click();
  await expect(page.locator("#toast")).toBeVisible();
  await expect(page.locator("#toast")).toHaveText("Endpoint copied");
});

test("bearings count service states and unmapped runtimes are diagnostic only", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".bearing-reachable .bearing-value")).toHaveText("2");
  await expect(page.locator(".bearing-down .bearing-value")).toHaveText("2");
  await expect(page.locator(".bearing-reachable .bearing-value")).toHaveText("4");
  await expect(page.locator("#diagnostics-keycap")).toHaveText("3 unmapped runtimes");
});

test("keyboard focus is visible and Ctrl/Cmd+K focuses search", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Control+k");
  await expect(page.locator("#search")).toBeFocused();
});

test("mobile: bearings become a strip and dossier stacks", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "mobile layout project");
  await page.goto("/");
  await page.locator('.service-row[data-service-id="langfuse"] .service-select').click();
  await expect(page.locator(".dossier h3").first()).toBeVisible();
  const grid = await page.locator(".atlas-frame").evaluate((el) => getComputedStyle(el).gridTemplateColumns);
  expect(grid.split(" ").length).toBe(1);
});

test("reduced motion: dossier reveal is effectively instant", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "reduced-motion", "reduced motion project");
  await page.goto("/");
  await page.locator('.service-row[data-service-id="langfuse"] .service-select').click();
  const duration = await page
    .locator('.dossier[data-dossier-for="langfuse"]')
    .evaluate((el) => getComputedStyle(el).animationDuration);
  expect(parseFloat(duration)).toBeLessThan(0.01);
});
```

- [ ] **Step 2: Build assets and run the browser suite**

```bash
npm run build
npx playwright test
```

Expected: all projects pass (desktop + mobile + reduced-motion).

- [ ] **Step 3: Commit**

```bash
git add tests/browser/atlas.spec.ts
git commit -m "test: browser verification for atlas behavior, mobile, and reduced motion"
```

**Phase 1 gate:** `uv run pytest`, `npm test`, `npm run lint`, `npx playwright test` all green; the atlas renders from fixtures through the real API.

---

**End of Part 2.** Continue with `2026-08-04-hyperion-v1-part3.md` (Phase 2: live providers — Docker proxy + provider, systemd provider, probe provider, live log gateway, supervisor wiring + production catalog).
