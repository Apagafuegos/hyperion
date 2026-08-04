"""Application assembly, lifespan, and security headers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import RequestResponseEndpoint

from .api.errors import ApiError, api_error_handler, validation_error_handler
from .api.routes import router as api_router
from .catalog import CatalogError, load_catalog
from .models import Catalog
from .providers.base import RouteProbeProvider, RuntimeProvider
from .providers.combined import CombinedRuntimeProvider
from .providers.fixture import FixtureProbeProvider, FixtureRuntimeProvider
from .services.logs import LogGateway
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
        # A failed initial cycle must not prevent startup: the supervisor keeps
        # running and publishes as soon as a cycle succeeds.
        await _run_cycle(runtime_provider, probe_provider, catalog, store)
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
        except (TimeoutError, asyncio.CancelledError):
            pass
    await _close_providers(app)


def _build_runtime_provider(settings: Settings) -> RuntimeProvider:
    if settings.fixture_mode:
        return FixtureRuntimeProvider(
            FIXTURES_DIR / "fixture-evidence.json", FIXTURES_DIR / "fixture-logs.json"
        )
    # Phase 2 live providers; not imported in fixture mode.
    from .providers.docker import DockerProvider
    from .providers.systemd import SystemdProvider  # type: ignore[import-untyped]

    # DockerProvider.observe returns a single ProviderObservation while the
    # combined provider currently expects one list per runtime provider; the
    # adapter lands with the systemd provider task.
    return CombinedRuntimeProvider(
        cast(RuntimeProvider, DockerProvider(settings.docker_host)), SystemdProvider()
    )


def _build_probe_provider(settings: Settings) -> RouteProbeProvider:
    if settings.fixture_mode:
        return FixtureProbeProvider(FIXTURES_DIR / "fixture-probes.json")
    # Phase 2 live provider; not imported in fixture mode.
    from .providers.probe import ProbeProvider  # type: ignore[import-untyped]

    return cast(RouteProbeProvider, ProbeProvider())


def _probe_urls(catalog: Catalog) -> tuple[str, ...]:
    return tuple(s.route_probe.url for s in catalog.services if s.route_probe is not None)


async def _run_cycle(
    runtime_provider: RuntimeProvider,
    probe_provider: RouteProbeProvider,
    catalog: Catalog,
    store: SnapshotStore,
) -> bool:
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
    async def security_headers(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(api_router)

    @app.get("/", include_in_schema=False)
    def index(request: Request) -> Response:
        identity = request.headers.get("X-Authentik-Username")
        if not identity or not identity.strip():
            raise ApiError(
                401, "AUTHENTICATION_REQUIRED", "Authentik identity header is required.", False
            )
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        return cast(Response, templates.TemplateResponse(request, "index.html", {}))

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


app = create_app()
