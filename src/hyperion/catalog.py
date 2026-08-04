"""Catalog loading, semantic validation, and revision hashing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import Catalog


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
