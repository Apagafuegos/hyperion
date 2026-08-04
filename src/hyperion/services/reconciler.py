"""Reconciler: joins catalog entries to discovered runtime evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from ..catalog import catalog_revision
from ..models import (
    Action,
    AtlasSnapshot,
    Catalog,
    ComponentSnapshot,
    ComponentState,
    CopyAction,
    CopyActionSnapshot,
    DependencySnapshot,
    Diagnostics,
    LogSource,
    NoneActionSnapshot,
    OpenAction,
    OpenActionSnapshot,
    ProviderStatus,
    RouteSnapshot,
    Service,
    ServiceAction,
    ServiceSnapshot,
    StateSummary,
    Territory,
    UnmappedRuntime,
)
from ..providers.base import ComponentEvidence, ProbeObservation, ProviderObservation
from ..state import derive_service_state
from .snapshots import compute_fresh

_TERRITORY_LABELS: dict[str, tuple[str, int]] = {
    "applications": ("Applications", 1),
    "services": ("Services", 2),
    "foundations": ("Foundations", 3),
}


def reconcile(
    catalog: Catalog,
    runtime_observations: list[ProviderObservation],
    probe_observation: ProbeObservation,
    now: datetime | None = None,
) -> AtlasSnapshot:
    """Build one immutable snapshot from the catalog and provider evidence."""
    now = now or datetime.now(UTC)
    # The supervisor guarantees at most one observation per provider
    # (CombinedRuntimeProvider emits exactly one docker and one systemd
    # observation), so first-wins status selection and last-write-wins
    # evidence joins cannot diverge; duplicates are a caller violation.
    docker_obs = next((o for o in runtime_observations if o.provider == "docker"), None)
    systemd_obs = next((o for o in runtime_observations if o.provider == "systemd"), None)

    evidence: dict[tuple[str, str], ComponentEvidence] = {}
    for observation in runtime_observations:
        for component in observation.components:
            evidence[(component.service_id, component.selector)] = component

    provider_ok = {
        "docker": docker_obs is not None and docker_obs.state != "unavailable",
        "systemd": systemd_obs is not None and systemd_obs.state != "unavailable",
        "probe": probe_observation.state != "unavailable",
    }
    # A degraded provider inspects only part of the runtime: a declared
    # component without evidence may simply have been missed, so absence
    # may only be concluded "missing" when the provider observation was
    # exactly `available`. Anything else maps absence to "unknown".
    provider_available = {
        "docker": docker_obs is not None and docker_obs.state == "available",
        "systemd": systemd_obs is not None and systemd_obs.state == "available",
    }
    # Missing components are stamped with the observation time so staleness
    # surfaces instead of being masked by the snapshot generation time.
    provider_observed_at = {
        "docker": docker_obs.observed_at if docker_obs is not None else None,
        "systemd": systemd_obs.observed_at if systemd_obs is not None else None,
    }

    services: list[ServiceSnapshot] = []
    for service in catalog.services:
        services.append(
            _build_service(
                service,
                evidence,
                probe_observation,
                provider_ok,
                provider_available,
                provider_observed_at,
                now,
            )
        )
    declared = {service.service_id: service.dependencies for service in catalog.services}
    _link_dependencies(services, declared)

    summary = _summarize(services)
    providers = _provider_statuses(docker_obs, systemd_obs, probe_observation)
    fresh = compute_fresh({p.provider: p for p in providers}, _required_providers(catalog), now)
    diagnostics = _diagnostics(docker_obs, systemd_obs)
    territories = [
        Territory(id=name, label=label, order=order)  # type: ignore[arg-type]
        for name, (label, order) in _TERRITORY_LABELS.items()
    ]
    return AtlasSnapshot(
        schema_version=1,
        generated_at=now,
        catalog_revision=catalog_revision(catalog),
        fresh=fresh,
        providers=providers,
        summary=summary,
        territories=territories,
        services=services,
        diagnostics=diagnostics,
    )


def _build_service(
    service: Service,
    evidence: dict[tuple[str, str], ComponentEvidence],
    probe_observation: ProbeObservation,
    provider_ok: dict[str, bool],
    provider_available: dict[str, bool],
    provider_observed_at: dict[str, datetime | None],
    now: datetime,
) -> ServiceSnapshot:
    components: list[ComponentSnapshot] = []
    provider_name: Literal["docker", "systemd"] = (
        "docker" if service.runtime.provider == "docker-compose" else "systemd"
    )
    for declared_component in service.runtime.components:
        selector = declared_component.selector
        component_evidence = evidence.get((service.service_id, selector))
        label = declared_component.label or declared_component.selector
        if component_evidence is None:
            component_state: ComponentState = (
                "missing" if provider_available[provider_name] else "unknown"
            )
            components.append(
                ComponentSnapshot(
                    key=selector,
                    label=label,
                    provider=provider_name,
                    provider_ref=None,
                    role=declared_component.role,
                    required=declared_component.required,
                    state=component_state,
                    health="unknown",
                    observed_at=provider_observed_at[provider_name] or now,
                    image=None,
                    uptime_seconds=None,
                    restart_count=None,
                    cpu_percent=None,
                    memory_bytes=None,
                )
            )
            continue
        components.append(
            ComponentSnapshot(
                key=selector,
                label=label,
                provider=component_evidence.provider,
                provider_ref=component_evidence.reference,
                role=declared_component.role,
                required=declared_component.required,
                state=component_evidence.state,
                health=component_evidence.health,
                observed_at=component_evidence.observed_at,
                image=component_evidence.image,
                uptime_seconds=component_evidence.uptime_seconds,
                restart_count=component_evidence.restart_count,
                cpu_percent=component_evidence.cpu_percent,
                memory_bytes=component_evidence.memory_bytes,
                ambiguous=component_evidence.ambiguous,
            )
        )

    route: RouteSnapshot | None = None
    probe_config = service.route_probe
    if probe_config is not None:
        route = _route_snapshot(probe_config.url, probe_observation)

    state, reasons = derive_service_state(
        service_id=service.service_id,
        intent=service.intent,
        components=components,
        route=route,
        failure_threshold=probe_config.failure_threshold if probe_config else 2,
        providers_ok=provider_ok,
        now=now,
    )

    log_sources = [
        LogSource(
            key=source,
            label=source,
            provider="journald" if provider_name == "systemd" else "docker",
            available=provider_ok[provider_name],
        )
        for source in service.logs.sources
    ]

    return ServiceSnapshot(
        id=service.service_id,
        name=service.name,
        description=service.description,
        territory=service.territory,
        kind=service.kind,
        intent=service.intent,
        action=_action_snapshot(service.action),
        state=state,
        state_reasons=reasons,
        observed_at=now,
        route=route,
        components=components,
        dependencies=[],
        log_sources=log_sources,
    )


def _link_dependencies(
    services: list[ServiceSnapshot], declared: dict[str, list[str]]
) -> None:
    by_id = {service.id: service for service in services}
    for service in services:
        dependencies = declared.get(service.id, [])
        if not dependencies:
            continue
        service.dependencies = [
            DependencySnapshot(service_id=dep, state=by_id[dep].state)
            for dep in dependencies
        ]


def _route_snapshot(url: str, observation: ProbeObservation) -> RouteSnapshot:
    for result in observation.results:
        if result.url != url:
            continue
        return RouteSnapshot(
            state=result.state,
            status_code=result.status_code,
            latency_ms=result.latency_ms,
            consecutive_failures=result.consecutive_failures,
            observed_at=result.observed_at,
            error=result.error,
        )
    return RouteSnapshot(
        state="unknown",
        status_code=None,
        latency_ms=None,
        consecutive_failures=0,
        observed_at=None,
        error=None,
    )


def _action_snapshot(action: Action) -> ServiceAction:
    if isinstance(action, OpenAction):
        return OpenActionSnapshot(type="open", url=action.url)
    if isinstance(action, CopyAction):
        return CopyActionSnapshot(type="copy", url=action.url)
    return NoneActionSnapshot(type="none")


def _summarize(services: list[ServiceSnapshot]) -> StateSummary:
    counts: dict[str, int] = {
        state: 0 for state in ("reachable", "degraded", "down", "dormant", "unknown")
    }
    for service in services:
        counts[service.state] += 1
    return StateSummary(
        total=len(services),
        reachable=counts["reachable"],
        degraded=counts["degraded"],
        down=counts["down"],
        dormant=counts["dormant"],
        unknown=counts["unknown"],
    )


def _provider_statuses(
    docker_obs: ProviderObservation | None,
    systemd_obs: ProviderObservation | None,
    probe_obs: ProbeObservation,
) -> list[ProviderStatus]:
    statuses: list[ProviderStatus] = []
    pairs: list[
        tuple[
            Literal["docker", "systemd", "probe"],
            ProviderObservation | ProbeObservation | None,
        ]
    ] = [
        ("docker", docker_obs),
        ("systemd", systemd_obs),
        ("probe", probe_obs),
    ]
    for provider, observation in pairs:
        if observation is None:
            statuses.append(
                ProviderStatus(
                    provider=provider,
                    state="unavailable",
                    observed_at=None,
                    message="provider was not configured",
                )
            )
            continue
        statuses.append(
            ProviderStatus(
                provider=provider,
                state=observation.state,
                observed_at=observation.observed_at,
                message=_sanitize(observation.error),
            )
        )
    return statuses


def _sanitize(message: str | None) -> str | None:
    if message is None:
        return None
    cleaned = " ".join(message.split())
    return cleaned if len(cleaned) <= 240 else cleaned[:237] + "..."


def _diagnostics(
    docker_obs: ProviderObservation | None, systemd_obs: ProviderObservation | None
) -> Diagnostics:
    unmapped: list[UnmappedRuntime] = []
    warnings: list[str] = []
    for observation in (docker_obs, systemd_obs):
        if observation is None:
            continue
        for runtime in observation.unmapped:
            unmapped.append(
                UnmappedRuntime(
                    provider=runtime.provider,
                    reference=runtime.reference,
                    project=runtime.project,
                    component=runtime.component,
                    state=runtime.state,
                )
            )
        warnings.extend(_sanitize(c) or "" for c in observation.conflicts)
    return Diagnostics(
        unmapped_runtimes=unmapped, warnings=[w for w in warnings if w][:20]
    )


def _required_providers(catalog: Catalog) -> set[str]:
    required: set[str] = set()
    for service in catalog.services:
        if service.runtime.provider == "docker-compose":
            required.add("docker")
        else:
            required.add("systemd")
        if service.route_probe is not None:
            required.add("probe")
    return required
