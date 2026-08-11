"""Catalog loading, semantic validation, and revision hashing."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import Catalog, DockerComponent, Service

_COMPONENT_ROLES = {"primary", "worker", "dependency", "sidecar", "shared"}
_TERRITORIES = {"applications", "services", "foundations"}
_KINDS = {"web", "api", "mcp", "worker", "infrastructure"}


@dataclass(frozen=True)
class DiscoveredDockerComponent:
    """Compose identity and Hyperion labels read from a Docker container."""

    project: str
    component: str
    labels: dict[str, str]


class CatalogError(ValueError):
    """Raised when the manifest cannot be loaded or violates semantics."""


def _format_errors(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"])
        parts.append(f"{loc}: {error['msg']}")
    return "; ".join(parts)


def load_catalog(path: Path) -> Catalog:
    """Load, validate, and normalize the manifest at `path`."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogError(f"cannot read catalog: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogError(f"invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise CatalogError("catalog must be a mapping")
    try:
        catalog = Catalog.model_validate(raw)
    except ValidationError as exc:
        raise CatalogError(_format_errors(exc)) from exc
    validate_semantics(catalog)
    return catalog


def validate_semantics(catalog: Catalog) -> None:
    """Enforce the semantic invariants from TECHNICAL-DESIGN.md section 5.1."""
    errors: list[str] = []
    by_id = {service.service_id: service for service in catalog.services}
    if len(by_id) != len(catalog.services):
        errors.append("service ids must be unique")

    owners: dict[tuple[str, str], list[str]] = {}
    for service in catalog.services:
        selectors = [component.selector for component in service.runtime.components]
        if len(selectors) != len(set(selectors)):
            errors.append(f"{service.service_id}: component selectors must be unique")
        primaries = [c for c in service.runtime.components if c.role == "primary"]
        if len(primaries) != 1:
            errors.append(f"{service.service_id}: exactly one primary component is required")
        declared = set(selectors)
        unknown_sources = set(service.logs.sources) - declared
        if unknown_sources:
            errors.append(
                f"{service.service_id}: log sources not declared as components: "
                f"{sorted(unknown_sources)}"
            )
        for dependency in service.dependencies:
            if dependency == service.service_id:
                errors.append(f"{service.service_id}: dependency cannot be self-referential")
            if dependency not in by_id:
                errors.append(f"{service.service_id}: unknown dependency {dependency!r}")
        if service.route_probe is not None and service.route_probe.follow_redirects:
            errors.append(f"{service.service_id}: followRedirects must remain false in v1")

        runtime = service.runtime
        if runtime.provider == "docker-compose":
            for component in runtime.components:
                if component.role == "shared":
                    continue
                key = (runtime.project, component.selector)
                owners.setdefault(key, []).append(service.service_id)

    for (project, selector), service_ids in owners.items():
        if len(set(service_ids)) > 1:
            errors.append(
                f"docker component {project}/{selector} is owned by multiple services: "
                f"{sorted(set(service_ids))}"
            )

    if errors:
        raise CatalogError("; ".join(errors))


def catalog_revision(catalog: Catalog) -> str:
    """Stable SHA-256 over the normalized catalog; drives snapshot ETags."""
    canonical = json.dumps(catalog.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def merge_discovered_docker(
    base: Catalog, discovered: list[DiscoveredDockerComponent]
) -> Catalog:
    """Promote discovered Compose projects and components into a catalog.

    The file-backed catalog remains the metadata/intent overlay. Docker is the
    operational inventory: unknown projects become usable services and newly
    observed components are appended to an existing project definition.
    """
    projects: dict[str, dict[str, DiscoveredDockerComponent]] = {}
    for item in discovered:
        if not _valid_selector(item.project) or not _valid_selector(item.component):
            continue
        projects.setdefault(item.project, {})[item.component] = item

    excluded = {
        project
        for project, components in projects.items()
        if any(
            _label_bool(item.labels.get("hyperion.enabled")) is False
            for item in components.values()
        )
    }
    projects = {
        project: components
        for project, components in projects.items()
        if project not in excluded
    }

    services = [service.model_copy(deep=True) for service in base.services]
    by_project = {
        service.runtime.project: service
        for service in services
        if service.runtime.provider == "docker-compose"
    }
    used_ids = {service.service_id for service in services}

    for project, components in sorted(projects.items()):
        existing = by_project.get(project)
        if existing is not None:
            _append_discovered_components(existing, components)
            continue
        service = _service_from_project(project, components, used_ids)
        services.append(service)
        used_ids.add(service.service_id)

    merged = Catalog(version=1, services=services)
    validate_semantics(merged)
    return merged


def _append_discovered_components(
    service: Service, discovered: dict[str, DiscoveredDockerComponent]
) -> None:
    assert service.runtime.provider == "docker-compose"
    declared = {component.selector for component in service.runtime.components}
    for selector, item in sorted(discovered.items()):
        if selector in declared:
            continue
        role = item.labels.get("hyperion.role", "sidecar")
        if role not in _COMPONENT_ROLES or role == "primary":
            role = "sidecar"
        required = _label_bool(item.labels.get("hyperion.required")) is True
        service.runtime.components.append(
            DockerComponent.model_validate({
                "selector": selector,
                "label": _trim(item.labels.get("hyperion.component-name"), 64),
                "role": role,
                "required": required,
            })
        )
        if _label_bool(item.labels.get("hyperion.logs")) is not False:
            service.logs.sources.append(selector)


def _service_from_project(
    project: str,
    components: dict[str, DiscoveredDockerComponent],
    used_ids: set[str],
) -> Service:
    ordered = list(sorted(components.values(), key=lambda item: item.component))
    metadata = next(
        (
            item.labels
            for item in ordered
            if _label_bool(item.labels.get("hyperion.primary")) is True
        ),
        ordered[0].labels,
    )
    requested_id = metadata.get("hyperion.id") or _slug(project)
    service_id = _unique_id(requested_id, used_ids)
    primary = next(
        (
            item.component
            for item in ordered
            if _label_bool(item.labels.get("hyperion.primary")) is True
        ),
        project if project in components else ordered[0].component,
    )

    component_defs: list[dict[str, object]] = []
    log_sources: list[str] = []
    for item in ordered:
        raw_role = item.labels.get("hyperion.role")
        role = "primary" if item.component == primary else raw_role or "sidecar"
        if role not in _COMPONENT_ROLES or (role == "primary" and item.component != primary):
            role = "sidecar"
        required_label = _label_bool(item.labels.get("hyperion.required"))
        component_defs.append(
            {
                "selector": item.component,
                "label": _trim(item.labels.get("hyperion.component-name"), 64),
                "role": role,
                "required": item.component == primary if required_label is None else required_label,
            }
        )
        if _label_bool(item.labels.get("hyperion.logs")) is not False:
            log_sources.append(item.component)

    territory = metadata.get("hyperion.territory", "services")
    if territory not in _TERRITORIES:
        territory = "services"
    kind = metadata.get("hyperion.kind", "infrastructure")
    if kind not in _KINDS:
        kind = "infrastructure"
    url = metadata.get("hyperion.url")
    action_type = metadata.get("hyperion.action", "open" if _is_https(url) else "none")
    action: dict[str, str] = (
        {"type": action_type, "url": url or ""}
        if action_type in {"open", "copy"} and _is_https(url)
        else {"type": "none"}
    )
    probe_url = metadata.get("hyperion.probe-url")
    if probe_url is None and _label_bool(metadata.get("hyperion.probe")) is True:
        probe_url = url

    raw: dict[str, object] = {
        "id": service_id,
        "name": _trim(metadata.get("hyperion.name"), 64) or _humanize(project),
        "description": _trim(metadata.get("hyperion.description"), 160)
        or f"Docker Compose project {project} discovered at runtime",
        "territory": territory,
        "kind": kind,
        "action": action,
        "runtime": {
            "provider": "docker-compose",
            "project": project,
            "components": component_defs,
        },
        "logs": {"sources": log_sources},
    }
    if _is_https(probe_url):
        raw["routeProbe"] = {"method": "GET", "url": probe_url}
    return Service.model_validate(raw)


def _valid_selector(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}", value))


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug or not slug[0].isalpha():
        slug = f"docker-{slug}" if slug else "docker-service"
    return slug[:64].rstrip("-")


def _unique_id(requested: str, used: set[str]) -> str:
    base = _slug(requested)
    if base not in used:
        return base
    prefix = _slug(f"docker-{base}")
    if prefix not in used:
        return prefix
    counter = 2
    while True:
        suffix = f"-{counter}"
        candidate = f"{prefix[: 64 - len(suffix)].rstrip('-')}{suffix}"
        if candidate not in used:
            return candidate
        counter += 1


def _humanize(value: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[-_.]+", value) if part)[:64]


def _label_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _trim(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned[:limit] or None


def _is_https(value: str | None) -> bool:
    return value is not None and value.startswith("https://")
