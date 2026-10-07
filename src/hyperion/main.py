"""Application assembly, lifespan, and security headers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import RequestResponseEndpoint

from .api.diagnostics import router as diagnostics_router
from .api.errors import ApiError, api_error_handler, validation_error_handler
from .api.resources import router as resources_router
from .api.routes import router as api_router
from .catalog import CatalogError, load_catalog
from .diagnostics.provider import DiagnosticsProvider
from .models import AtlasSnapshot, Catalog
from .ops import OperationHelperClient
from .providers.base import RouteProbeProvider, RuntimeProvider
from .providers.combined import CombinedRuntimeProvider
from .providers.fixture import FixtureProbeProvider, FixtureRuntimeProvider
from .providers.host import HostProvider
from .providers.schedules import ScheduleProvider
from .providers.systemd_units import SystemdUnitProvider
from .services.activity import ActivityStore
from .services.host_sampler import HostSampler
from .services.logs import LogGateway
from .services.managed_schedules import ManagedScheduleManager, OpsHelper
from .services.operations import OperationCoordinator
from .services.reconciler import reconcile
from .services.snapshots import SnapshotStore
from .services.supervisor import RefreshSupervisor
from .settings import Settings

logger = logging.getLogger("hyperion")

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "base-uri 'self'; form-action 'none'; frame-ancestors 'none'; object-src 'none'"
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
FIXTURES_DIR = Path(__file__).parents[2] / "tests" / "fixtures"
TEMPLATES = Jinja2Templates(directory=str(TEMPLATES_DIR))

WORKSPACES = ("overview", "atlas", "schedules", "units", "activity")

WORKSPACE_STATEMENTS = {
    "overview": "One glance at the VPS: condition, attention, and upcoming work.",
    "atlas": "Everything deployed, mapped to one calm surface.",
    "schedules": "What is scheduled to run, when, and by whom.",
    "units": "The systemd inventory behind the deployed services.",
    "activity": "What changed, recovered, or was operated recently.",
}

WORKSPACE_SEARCH = {
    "overview": "Search services, schedules, activity…",
    "atlas": "Find services, endpoints…",
    "schedules": "Search schedules, commands, sources…",
    "units": "Search units, descriptions, states…",
    "activity": "Search targets, events, identities…",
}

PROTECTED_UNITS = {
    "ssh.service",
    "sshd.service",
    "ssh.socket",
    "systemd-networkd.service",
    "NetworkManager.service",
    "networking.service",
    "network.service",
    "docker.service",
    "caddy.service",
    "authentik-server.service",
    "authentik-worker.service",
}


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    _configure_logging(settings)
    store = SnapshotStore()
    app.state.snapshot_store = store
    app.state.supervisor_task = None
    app.state.runtime_provider = None
    app.state.probe_provider = None
    app.state.log_gateway = None

    activity = ActivityStore(settings.state_dir / "activity.db", settings.activity_retention_days)
    app.state.activity_store = activity
    if settings.fixture_mode:
        _seed_fixture_activity(activity)

    base_catalog = None
    try:
        base_catalog = load_catalog(settings.catalog_path)
    except CatalogError as exc:
        logger.error("catalog invalid at startup; readiness will remain false: %s", exc)
    catalog = base_catalog
    app.state.catalog = catalog
    # Fixture mode must never inspect the preview machine's live processes.
    app.state.diagnostics_provider = DiagnosticsProvider(enabled=not settings.fixture_mode)

    # Host telemetry is independent of service reconciliation.
    host_provider = _build_host_provider(settings)
    host_sampler = HostSampler(
        host_provider,
        interval_seconds=settings.host_sample_interval,
        history_seconds=settings.host_history_seconds,
    )
    app.state.host_sampler = host_sampler
    try:
        initial_host = await host_provider.observe()
        host_sampler.record(initial_host)
    except Exception as exc:  # provider boundary
        logger.error("initial host sample failed: %s", exc)
    host_sampler.start()

    # Units and schedules inventory providers.
    unit_provider = _build_unit_provider(settings)
    app.state.unit_provider = unit_provider
    schedule_provider = _build_schedule_provider(settings)
    app.state.schedule_provider = schedule_provider

    # Operation coordinator over the privileged boundary.
    operation_helper = _build_operation_helper(settings)
    operation_coordinator = OperationCoordinator(
        operation_helper,
        activity,
        allowed_units=_allowlisted_units(base_catalog),
        protected_units=set(PROTECTED_UNITS),
    )
    app.state.operation_coordinator = operation_coordinator

    schedule_helper: OpsHelper = operation_helper
    if settings.fixture_mode:
        schedule_helper = _build_fixture_ops_helper(settings)
    managed_schedules = ManagedScheduleManager(
        settings.state_dir,
        activity,
        helper=schedule_helper,
        managed_unit_dir=settings.managed_unit_dir,
    )
    app.state.managed_schedules = managed_schedules

    if base_catalog is not None:
        runtime_provider = _build_runtime_provider(settings)
        probe_provider = _build_probe_provider(settings)
        catalog = await _discover_catalog(runtime_provider, base_catalog)
        app.state.catalog = catalog
        app.state.runtime_provider = runtime_provider
        app.state.probe_provider = probe_provider
        app.state.log_gateway = LogGateway(
            runtime_provider,
            lambda: app.state.catalog,
            resolve_references=_resolve_log_references(store),
        )
        # A failed initial cycle must not prevent startup: the supervisor keeps
        # running and publishes as soon as a cycle succeeds.
        await _run_cycle(runtime_provider, probe_provider, catalog, store)
        supervisor = RefreshSupervisor(
            runtime_provider=runtime_provider,
            probe_provider=probe_provider,
            catalog=catalog,
            store=store,
            base_catalog=base_catalog,
            catalog_path=settings.catalog_path,
            on_catalog=lambda updated: setattr(app.state, "catalog", updated),
            on_snapshot=lambda snapshot: _record_service_activity(snapshot, activity),
        )
        app.state.supervisor_task = asyncio.create_task(supervisor.run())
    yield
    task = app.state.supervisor_task
    if task is not None:
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            pass
    await host_sampler.stop()
    activity.close()
    await _close_providers(app)


def _seed_fixture_activity(activity: ActivityStore) -> None:
    """Seed deterministic activity records so fixture mode renders meaningfully."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    activity.record(
        "operation",
        "t3code.service",
        "restart requested for t3code.service.",
        result="info",
        identity="operator",
        target_type="unit",
        occurred_at=now - timedelta(hours=2),
        dedupe_key="fixture:op:1",
        evidence={"operation": "restart"},
    )
    activity.record(
        "schedule_execution",
        "backup",
        "backup.timer executed backup.service successfully.",
        result="success",
        identity="system",
        target_type="schedule",
        occurred_at=now - timedelta(hours=6),
        dedupe_key="fixture:schedule:1",
        evidence={"source": "systemd"},
    )
    activity.record(
        "service_transition",
        "mcp-observatory",
        "Service mcp-observatory changed from reachable to down.",
        result="warning",
        identity="system",
        target_type="service",
        occurred_at=now - timedelta(hours=20),
        dedupe_key="fixture:svc:1",
        evidence={"from": "reachable", "to": "down"},
    )
    activity.record(
        "host_threshold",
        "meridian-vps",
        "Storage utilization on /var/lib/docker exceeded 85%.",
        result="warning",
        identity="system",
        target_type="host",
        occurred_at=now - timedelta(days=1),
        dedupe_key="fixture:host:1",
        evidence={"metric": "storage", "value": "90.0"},
    )


def _build_runtime_provider(settings: Settings) -> RuntimeProvider:
    if settings.fixture_mode:
        return FixtureRuntimeProvider(
            FIXTURES_DIR / "fixture-evidence.json", FIXTURES_DIR / "fixture-logs.json"
        )
    # Phase 2 live providers; not imported in fixture mode.
    from .providers.docker import DockerProvider
    from .providers.systemd import SystemdProvider

    return CombinedRuntimeProvider(DockerProvider(settings.docker_host), SystemdProvider())


def _build_probe_provider(settings: Settings) -> RouteProbeProvider:
    if settings.fixture_mode:
        return FixtureProbeProvider(FIXTURES_DIR / "fixture-probes.json")
    # Phase 2 live provider; not imported in fixture mode.
    from .providers.probe import ProbeProvider

    return cast(RouteProbeProvider, ProbeProvider())


def _build_host_provider(settings: Settings) -> HostProvider:
    if settings.fixture_mode:
        from .providers.fixture import FixtureHostProvider

        return cast(HostProvider, FixtureHostProvider(FIXTURES_DIR / "fixture-host.json"))
    return HostProvider()


def _build_unit_provider(settings: Settings) -> SystemdUnitProvider:
    if settings.fixture_mode:
        from .providers.fixture import FixtureUnitProvider

        return cast(
            SystemdUnitProvider,
            FixtureUnitProvider(
                FIXTURES_DIR / "fixture-units.json", FIXTURES_DIR / "fixture-logs.json"
            ),
        )
    return SystemdUnitProvider()


def _build_schedule_provider(settings: Settings) -> ScheduleProvider:
    if settings.fixture_mode:
        from .providers.fixture import FixtureScheduleProvider

        return cast(
            ScheduleProvider, FixtureScheduleProvider(FIXTURES_DIR / "fixture-schedules.json")
        )
    return ScheduleProvider()


def _build_operation_helper(settings: Settings) -> OperationHelperClient:
    return OperationHelperClient(settings.ops_socket_path)


class FixtureOpsHelper:
    """In-process ops helper for fixture mode.

    Mirrors `write-unit`/`remove-unit` against the managed unit directory and
    reports success for systemctl verbs without executing them, so the managed
    schedules workspace behaves like a local preview.
    """

    def __init__(self, managed_unit_dir: Path) -> None:
        self._dir = Path(managed_unit_dir)

    async def request(
        self, unit: str, operation: str, content: str | None = None
    ) -> dict[str, object]:
        if operation == "write-unit":
            if content is None:
                return {"ok": False, "error": "write-unit requires content", "denied": True}
            path = self._dir / unit
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {"ok": True, "returncode": 0}
        if operation == "remove-unit":
            (self._dir / unit).unlink(missing_ok=True)
            return {"ok": True, "returncode": 0}
        return {"ok": True, "returncode": 0}


def _build_fixture_ops_helper(settings: Settings) -> FixtureOpsHelper:
    return FixtureOpsHelper(settings.managed_unit_dir)


def _allowlisted_units(catalog: Catalog | None) -> set[str]:
    """Units an operator may control: catalog-declared systemd components that
    are not protected. The privileged boundary enforces this independently."""
    if catalog is None:
        return set()
    units: set[str] = set()
    for service in catalog.services:
        if service.runtime.provider != "systemd":
            continue
        for component in service.runtime.components:
            units.add(component.selector)
    return {u for u in units if u not in PROTECTED_UNITS}


def _record_service_activity(snapshot: AtlasSnapshot, activity: ActivityStore) -> None:
    """Record service state transitions as cheap evidence; no raw telemetry."""
    # Guarded by the supervisor: snapshot transitions are compared against the
    # previous snapshot's derived service states. The supervisor stores state
    # in-memory; a cold start records nothing spurious.
    previous = getattr(_record_service_activity, "_previous", None)
    current = {s.id: s.state for s in snapshot.services}
    if previous is not None:
        for service_id, state in current.items():
            prior = previous.get(service_id)
            if prior is not None and prior != state:
                activity.record(
                    "service_transition",
                    service_id,
                    f"Service {service_id} changed from {prior} to {state}.",
                    result="warning" if state in ("down", "degraded") else "info",
                    identity="system",
                    target_type="service",
                    dedupe_key=f"svc:{service_id}:{state}:{snapshot.generated_at.isoformat()}",
                    evidence={"from": str(prior), "to": state},
                )
    _record_service_activity._previous = current  # type: ignore[attr-defined]


def _probe_urls(catalog: Catalog) -> tuple[str, ...]:
    return tuple(s.route_probe.url for s in catalog.services if s.route_probe is not None)


def _probe_config(catalog: Catalog) -> dict[str, tuple[float, float]]:
    return {
        s.route_probe.url: (
            s.route_probe.timeout_ms / 1000,
            s.route_probe.slow_after_ms / 1000,
        )
        for s in catalog.services
        if s.route_probe is not None
    }


def _resolve_log_references(
    store: SnapshotStore,
) -> Callable[[], dict[tuple[str, str], str]]:
    """Resolver for docker container references from the latest snapshot.

    The gateway reads references at request time so bindings reflect the
    most recently published container state (up to ~15s stale); before the
    first cycle publishes, the map is empty and docker reads fail as
    LOG_SOURCE_UNAVAILABLE. Systemd units are not mapped: the unit name is
    the selector.
    """

    def resolve() -> dict[tuple[str, str], str]:
        pair = store.get()
        if pair is None:
            return {}
        snapshot, _ = pair
        references: dict[tuple[str, str], str] = {}
        for service in snapshot.services:
            for component in service.components:
                if component.provider == "docker" and component.provider_ref is not None:
                    references[(service.id, component.key)] = component.provider_ref
        return references

    return resolve


async def _run_cycle(
    runtime_provider: RuntimeProvider,
    probe_provider: RouteProbeProvider,
    catalog: Catalog,
    store: SnapshotStore,
) -> bool:
    try:
        runtime_obs, probe_obs = await asyncio.gather(
            runtime_provider.observe(catalog),
            probe_provider.observe(_probe_urls(catalog), _probe_config(catalog)),
        )
    except Exception as exc:  # provider boundary
        logger.error("initial refresh failed: %s", exc)
        return False
    snapshot = reconcile(catalog, runtime_obs, probe_obs)
    store.publish(snapshot)
    return True


async def _discover_catalog(runtime_provider: RuntimeProvider, base: Catalog) -> Catalog:
    discover = getattr(runtime_provider, "discover_catalog", None)
    if discover is None:
        return base
    try:
        return cast(Catalog, await discover(base))
    except Exception as exc:
        logger.error("initial Docker catalog discovery failed; using file catalog: %s", exc)
        return base


async def _close_providers(app: FastAPI) -> None:
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


def _workspace_template(request: Request, workspace: str) -> Response:
    if workspace not in WORKSPACES:
        raise ApiError(404, "WORKSPACE_NOT_FOUND", "Unknown workspace.", False)
    return cast(
        Response,
        TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "workspace": workspace,
                "fixture_mode": request.app.state.settings.fixture_mode,
                "workspace_statement": WORKSPACE_STATEMENTS[workspace],
                "search_placeholder": WORKSPACE_SEARCH[workspace],
            },
        ),
    )


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
    async def security_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        # Assets and pages are served behind a CDN proxy without content
        # hashing; never let a stale browser/edge copy outlive a deploy.
        # API endpoints keep their explicit, contract-tested policies.
        response.headers.setdefault("Cache-Control", "no-cache")
        return response

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(api_router)
    app.include_router(resources_router)
    app.include_router(diagnostics_router)

    @app.get("/", include_in_schema=False)
    def index(request: Request) -> Response:
        return _require_identity(request, "atlas")

    for workspace in WORKSPACES:
        app.get(f"/{workspace}", include_in_schema=False)(_workspace_route_factory(workspace))

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


def _workspace_route_factory(workspace: str) -> Callable[[Request], Response]:
    def route(request: Request) -> Response:
        return _require_identity(request, workspace)

    route.__name__ = f"workspace_{workspace}"
    return route


def _require_identity(request: Request, workspace: str) -> Response:
    identity = request.headers.get("X-Authentik-Username")
    if not identity or not identity.strip():
        raise ApiError(
            401, "AUTHENTICATION_REQUIRED", "Authentik identity header is required.", False
        )
    return _workspace_template(request, workspace)


app = create_app()
