"""Canonical Pydantic models for the Hyperion catalog and API.

The JSON Schema generated from this module is normative and must match
`schema/services.schema.json`; the OpenAPI generated from the FastAPI app must
match `schema/openapi.yaml` (enforced by contract parity tests).

PEP 695 aliases (`type id = ...`) become `$defs` entries named after the alias,
so the shared string types and union discriminators mirror the committed
schema's `$defs` keys exactly. Plain `TypeAlias` assignments are inlined.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Shared field types (mirror schema/services.schema.json $defs)
# ---------------------------------------------------------------------------

type id = Annotated[
    str, Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", min_length=1, max_length=64)
]
type selector = Annotated[
    str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]*$", min_length=1, max_length=128)
]
SystemdUnit: TypeAlias = Annotated[  # noqa: UP040 - `type` keyword would emit a $defs entry
    str,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]*\.service$", min_length=9, max_length=128),
]
HttpsUrl: TypeAlias = Annotated[str, Field(pattern=r"^https://")]  # noqa: UP040 - inlined by design

Territory = Literal["applications", "services", "foundations"]
ServiceKind = Literal["web", "api", "mcp", "worker", "infrastructure"]
ComponentRole = Literal["primary", "worker", "dependency", "sidecar", "shared"]
ComponentState = Literal[
    "running", "starting", "restarting", "paused", "stopped", "missing", "unknown"
]
HealthState = Literal["healthy", "starting", "unhealthy", "unconfigured", "unknown"]
ServiceState = Literal["reachable", "degraded", "down", "dormant", "unknown"]


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------------
# Catalog manifest (schema/services.schema.json)
# ---------------------------------------------------------------------------


class ComponentBase(CatalogModel):
    selector: selector
    label: Annotated[str | None, Field(default=None, min_length=1, max_length=64)]
    role: ComponentRole
    required: bool


class DockerComponent(ComponentBase):
    """Compose service component; selector is a plain Compose service name."""


class SystemdComponent(ComponentBase):
    selector: SystemdUnit


class DockerRuntime(CatalogModel):
    provider: Literal["docker-compose"]
    project: selector
    components: Annotated[list[DockerComponent], Field(min_length=1)]


class SystemdRuntime(CatalogModel):
    provider: Literal["systemd"]
    components: Annotated[list[SystemdComponent], Field(min_length=1)]


type Runtime = DockerRuntime | SystemdRuntime


class OpenAction(CatalogModel):
    type: Literal["open"]
    url: HttpsUrl


class CopyAction(CatalogModel):
    type: Literal["copy"]
    url: HttpsUrl


class NoneAction(CatalogModel):
    type: Literal["none"]


type Action = OpenAction | CopyAction | NoneAction


class RouteProbe(CatalogModel):
    method: Literal["GET", "HEAD"]
    url: HttpsUrl
    accepted_status_classes: Annotated[
        list[Literal[2, 3, 4]],
        Field(
            alias="acceptedStatusClasses",
            default=[2, 3, 4],
            min_length=1,
            json_schema_extra={"uniqueItems": True},
        ),
    ]
    timeout_ms: Annotated[int, Field(alias="timeoutMs", default=3000, ge=250, le=10000)]
    slow_after_ms: Annotated[int, Field(alias="slowAfterMs", default=1200, ge=100, le=10000)]
    failure_threshold: Annotated[int, Field(alias="failureThreshold", default=2, ge=1, le=5)]
    follow_redirects: Annotated[bool, Field(alias="followRedirects", default=False)]


class Logs(CatalogModel):
    sources: Annotated[list[selector], Field(json_schema_extra={"uniqueItems": True})]
    default_tail: Annotated[Literal[50, 100, 250, 500], Field(alias="defaultTail", default=100)]

    @field_validator("sources")
    @classmethod
    def _sources_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("sources must be unique")
        return value


class Service(CatalogModel):
    service_id: Annotated[id, Field(alias="id")]
    name: Annotated[str, Field(min_length=1, max_length=64)]
    description: Annotated[str, Field(min_length=1, max_length=160)]
    territory: Territory
    kind: ServiceKind
    intent: Literal["active", "dormant"] = "active"
    action: Action
    runtime: Runtime
    route_probe: Annotated[RouteProbe | None, Field(alias="routeProbe", default=None)]
    logs: Logs
    dependencies: Annotated[list[id], Field(default=[], json_schema_extra={"uniqueItems": True})]

    @field_validator("dependencies")
    @classmethod
    def _dependencies_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("dependencies must be unique")
        return value


class Catalog(CatalogModel):
    model_config = ConfigDict(
        json_schema_extra={
            "description": "Version 1 manifest for logical services and their "
            "allowlisted runtime components."
        }
    )
    version: Literal[1]
    services: list[Service]
