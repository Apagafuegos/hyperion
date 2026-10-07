"""Streamable HTTP MCP adapter for Hyperion's allowlisted HTTP API."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, InputRequiredResult, Tool, ToolAnnotations
from pydantic import ValidationError
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .diagnostics.models import (
    DIAGNOSTIC_INPUTS,
    EndpointListenersResult,
    ListenersInput,
    ProcessInput,
    ProcessResult,
    RuntimeInput,
    ServiceRuntimeResult,
)
from .privacy import redact as _redact

_WRITE_AUTH: ContextVar[bool] = ContextVar("hyperion_mcp_write_auth", default=False)
_SERVICE_ID = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _utc(value: str | datetime) -> str:
    try:
        date = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
    except ValueError as exc:
        raise ToolError("Invalid timestamp") from exc
    if date.tzinfo is None:
        raise ToolError("Timestamp must include a timezone")
    return date.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _now() -> str:
    return _utc(datetime.now(UTC))


def _service(value: str) -> str:
    if not _SERVICE_ID.fullmatch(value):
        raise ToolError("Invalid service ID")
    return value


@dataclass(frozen=True)
class MCPSettings:
    upstream_url: str
    read_token: str
    write_token: str
    restart_services: frozenset[str]
    idempotency_db: Path
    host: str = "127.0.0.1"
    port: int = 8101
    allowed_hosts: tuple[str, ...] = ("127.0.0.1:*", "localhost:*", "[::1]:*")

    @classmethod
    def from_env(cls) -> MCPSettings:
        read = os.environ.get("HYPERION_MCP_TOKEN", "")
        if len(read) < 32:
            raise ValueError("HYPERION_MCP_TOKEN must contain at least 32 characters")
        write = os.environ.get("HYPERION_MCP_WRITE_TOKEN", "")
        if write and (len(write) < 32 or hmac.compare_digest(read, write)):
            raise ValueError("HYPERION_MCP_WRITE_TOKEN must be distinct and at least 32 characters")
        allowed = frozenset(
            filter(None, os.environ.get("HYPERION_MCP_RESTART_SERVICES", "").split(","))
        )
        if any(not _SERVICE_ID.fullmatch(item) for item in allowed):
            raise ValueError("Invalid HYPERION_MCP_RESTART_SERVICES entry")
        return cls(
            upstream_url=os.environ.get(
                "HYPERION_MCP_UPSTREAM_URL", "http://127.0.0.1:8787"
            ).rstrip("/"),
            read_token=read,
            write_token=write,
            restart_services=allowed,
            idempotency_db=Path(
                os.environ.get(
                    "HYPERION_MCP_IDEMPOTENCY_DB", "/var/lib/hyperion/mcp-idempotency.db"
                )
            ),
            host=os.environ.get("HYPERION_MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("HYPERION_MCP_PORT", "8101")),
            allowed_hosts=("127.0.0.1:*", "localhost:*", "[::1]:*")
            + tuple(filter(None, os.environ.get("HYPERION_MCP_ALLOWED_HOSTS", "").split(","))),
        )


class HyperionGateway:
    def __init__(self, settings: MCPSettings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            if self.client is not None:
                response = await self.client.request(method, path, **kwargs)
            else:
                async with httpx.AsyncClient(
                    base_url=self.settings.upstream_url,
                    headers={
                        "Authorization": "Bearer " + os.environ.get("HYPERION_READ_TOKEN", "")
                    },
                    timeout=10,
                    trust_env=False,
                ) as client:
                    response = await client.request(method, path, **kwargs)
            if response.status_code >= 400:
                if path.startswith("/api/v1/diagnostics/") and response.status_code in {403, 410}:
                    raise ToolError(
                        "Process reference expired or is outside this credential's "
                        "service scope; rediscover it"
                    )
                if response.status_code == 404:
                    raise ToolError("Service or resource is not in Hyperion's allowlist")
                raise ToolError(f"Hyperion request failed (HTTP {response.status_code})")
            body = response.json()
            if not isinstance(body, dict):
                raise ToolError("Hyperion returned an invalid response")
            return body
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ToolError("Hyperion is unavailable or timed out") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ToolError("Hyperion returned an invalid response") from exc

    async def _snapshot_service(self, service: str) -> tuple[dict[str, Any], dict[str, Any]]:
        service = _service(service)
        snapshot = await self._request("GET", "/api/v1/snapshot")
        for item in snapshot.get("services", []):
            if isinstance(item, dict) and item.get("id") == service:
                return snapshot, item
        raise ToolError(
            "Service is not in Hyperion's catalog; use list_services to discover its exact ID"
        )

    async def list_services(
        self, query: str = "", limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100 or offset < 0 or len(query) > 200:
            raise ToolError(
                "Use limit 1..100, a nonnegative offset, and a query up to 200 characters"
            )
        snapshot = await self._request("GET", "/api/v1/snapshot")
        normalized = re.sub(r"[^a-z0-9]", "", query.casefold())
        items = []
        for item in snapshot.get("services", []):
            if not isinstance(item, dict):
                continue
            searchable = re.sub(
                r"[^a-z0-9]", "", (str(item.get("id", "")) + str(item.get("name", ""))).casefold()
            )
            if normalized and normalized not in searchable:
                continue
            items.append(
                {
                    "service": item["id"],
                    "name": _redact(str(item.get("name", item["id"]))),
                    "state": item.get("state", "unknown"),
                    "observed_at": _utc(item["observedAt"]),
                    "source_uri": f"hyperion://services/{item['id']}",
                    "diagnostic_endpoints": sorted(
                        (item.get("diagnosticEndpoints") or {}).get("endpoints", {})
                    ),
                }
            )
        items.sort(key=lambda item: item["service"])
        return {
            "services": items[offset : offset + limit],
            "total": len(items),
            "truncated": offset + limit < len(items),
            "next_offset": offset + limit if offset + limit < len(items) else None,
            "observed_at": _now(),
            "source_uri": "hyperion://catalog/services",
        }

    async def get_service(self, service: str) -> dict[str, Any]:
        snapshot, item = await self._snapshot_service(service)
        state = {
            "reachable": "active",
            "dormant": "inactive",
            "down": "failed",
            "degraded": "degraded",
        }.get(str(item.get("state")), "degraded")
        observed = _utc(item["observedAt"])
        age_seconds = (
            datetime.now(UTC) - datetime.fromisoformat(observed.replace("Z", "+00:00"))
        ).total_seconds()
        fresh = bool(snapshot.get("fresh")) and -5 <= age_seconds <= 45
        if not fresh and state == "active":
            state = "degraded"
        checks = [
            {
                "component": component["key"],
                "state": component["state"],
                "health": component["health"],
            }
            for component in item.get("components", [])
            if isinstance(component, dict)
        ]
        return {
            "service": service,
            "state": state,
            "healthy": state == "active" and fresh,
            "observed_at": observed,
            "source_uri": f"hyperion://services/{service}/health/{quote(observed, safe='')}",
            "checks": checks,
            "fresh": fresh,
            "diagnostic_endpoints": (item.get("diagnosticEndpoints") or {}).get("endpoints", {}),
        }

    async def get_service_runtime(self, service: str) -> ServiceRuntimeResult:
        arguments = RuntimeInput(service=service)
        body = await self._request(
            "GET", "/api/v1/diagnostics/service-runtime", params=arguments.model_dump()
        )
        try:
            return ServiceRuntimeResult.model_validate_json(json.dumps(body), strict=True)
        except ValidationError as exc:
            raise ToolError("Hyperion returned invalid runtime evidence") from exc

    async def get_endpoint_listeners(
        self, service: str, endpoint_id: str
    ) -> EndpointListenersResult:
        arguments = ListenersInput(service=service, endpoint_id=endpoint_id)
        body = await self._request(
            "GET", "/api/v1/diagnostics/endpoint-listeners", params=arguments.model_dump()
        )
        try:
            return EndpointListenersResult.model_validate_json(json.dumps(body), strict=True)
        except ValidationError as exc:
            raise ToolError("Hyperion returned invalid listener evidence") from exc

    async def inspect_process(self, service: str, process_ref: str) -> ProcessResult:
        arguments = ProcessInput(service=service, process_ref=process_ref)
        body = await self._request(
            "GET", "/api/v1/diagnostics/process", params=arguments.model_dump()
        )
        try:
            return ProcessResult.model_validate_json(json.dumps(body), strict=True)
        except ValidationError as exc:
            raise ToolError("Hyperion returned invalid process evidence") from exc

    async def get_logs(
        self,
        service: str,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        await self._snapshot_service(service)
        if not 1 <= limit <= 500:
            raise ToolError("limit must be between 1 and 500")
        lower = _utc(since) if since else None
        upper_value = cursor or until
        upper = _utc(upper_value) if upper_value is not None else None
        if cursor and until and _utc(cursor) > _utc(until):
            upper = _utc(until)
        # Hyperion accepts only these bounded tail sizes.
        tail = next(size for size in (50, 100, 250, 500) if size >= limit)
        data = await self._request(
            "GET",
            f"/api/v1/services/{service}/logs",
            params={"tail": tail, **({"before": upper} if upper else {})},
        )
        records = data.get("records")
        if not isinstance(records, list):
            raise ToolError("Hyperion returned an invalid log response")
        entries = []
        for record in records:
            if not isinstance(record, dict):
                raise ToolError("Hyperion returned an invalid log response")
            stamp = _utc(record["timestamp"]) if record.get("timestamp") else None
            if stamp and lower and stamp < lower:
                continue
            if stamp and upper and stamp >= upper:
                continue
            source = str(record.get("source", "unknown"))
            message = _redact(str(record.get("message", "")))
            digest = hashlib.sha256(f"{stamp}:{source}:{message}".encode()).hexdigest()[:16]
            source_uri = f"hyperion://services/{service}/logs/{quote(source, safe='')}/{digest}"
            entries.append(
                {
                    "timestamp": stamp,
                    "level": record.get("severity") or "unknown",
                    "message": message,
                    "source_uri": source_uri,
                }
            )
        filtered_count = len(entries)
        entries = entries[-limit:]
        truncated = bool(data.get("truncated")) or len(records) >= tail or filtered_count > limit
        next_cursor = entries[0]["timestamp"] if truncated and entries else None
        result: dict[str, Any] = {
            "service": service,
            "entries": entries,
            "truncated": truncated,
            "observed_at": _utc(data["requestedAt"]),
            "source_uri": f"hyperion://services/{service}/logs",
        }
        if next_cursor:
            result["next_cursor"] = next_cursor
        return result

    async def get_deployment(
        self, service: str, deployment_id: str | None = None
    ) -> dict[str, Any]:
        _, item = await self._snapshot_service(service)
        primary = [
            component
            for component in item.get("components", [])
            if component.get("role") == "primary"
        ]
        if len(primary) != 1 or primary[0].get("provider") != "docker":
            raise ToolError("Deployment provenance requires one primary Docker component")
        provenance = primary[0].get("deployment")
        if not isinstance(provenance, dict):
            raise ToolError("Deployment labels are missing from the primary Docker component")
        fields = {
            "deployment_id": provenance.get("deploymentId"),
            "commit_sha": provenance.get("commitSha"),
            "started_at": provenance.get("startedAt"),
            "completed_at": provenance.get("completedAt"),
            "status": provenance.get("status"),
            "environment": provenance.get("environment"),
        }
        if any(value is None or not str(value).strip() for value in fields.values()):
            raise ToolError("Deployment labels are incomplete on the primary Docker component")
        current_id = str(fields["deployment_id"])
        sha = str(fields["commit_sha"])
        if not _SHA.fullmatch(sha):
            raise ToolError("Docker revision label is not a full commit SHA")
        if deployment_id is not None and deployment_id != current_id:
            raise ToolError("Requested deployment is not the currently running deployment")
        try:
            started = _utc(str(fields["started_at"]))
            completed = _utc(str(fields["completed_at"]))
        except ToolError as exc:
            raise ToolError(
                "Deployment timestamp labels must be timezone-aware ISO timestamps"
            ) from exc
        observed_at = _utc(primary[0].get("observedAt") or item["observedAt"])
        return {
            "service": service,
            "deployment_id": current_id,
            "commit_sha": sha,
            "started_at": started,
            "completed_at": completed,
            "status": str(fields["status"]),
            "environment": str(fields["environment"]),
            "observed_at": observed_at,
            "source_uri": f"hyperion://deployments/{service}/{quote(current_id, safe='')}",
        }

    async def restart_service(self, service: str, idempotency_key: str) -> dict[str, Any]:
        service = _service(service)
        if not _WRITE_AUTH.get() or not self.settings.write_token:
            raise ToolError("Write credential required")
        if service not in self.settings.restart_services:
            raise ToolError("Service is not allowlisted for restart")
        if not 8 <= len(idempotency_key) <= 64 or not re.fullmatch(
            r"[A-Za-z0-9_-]+", idempotency_key
        ):
            raise ToolError("Invalid idempotency key")
        _, item = await self._snapshot_service(service)
        primary = [c for c in item.get("components", []) if c.get("role") == "primary"]
        if len(primary) != 1 or primary[0].get("provider") != "systemd":
            raise ToolError("Restart requires one primary systemd unit")
        unit = primary[0].get("providerRef")
        if not isinstance(unit, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.@-]*\.service", unit
        ):
            raise ToolError("Primary systemd unit is unavailable")
        db = self.settings.idempotency_db
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db, timeout=10) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS requests "
                "(key TEXT PRIMARY KEY, service TEXT NOT NULL, result TEXT)"
            )
            row = conn.execute(
                "SELECT service, result FROM requests WHERE key = ?", (idempotency_key,)
            ).fetchone()
            if row is not None:
                if row[0] != service:
                    raise ToolError("Idempotency key was used for another service")
                if row[1] is None:
                    raise ToolError(
                        "Previous restart outcome is ambiguous; inspect service before retrying"
                    )
                return dict(json.loads(row[1]))
            conn.execute(
                "INSERT INTO requests (key, service, result) VALUES (?, ?, NULL)",
                (idempotency_key, service),
            )
        data = await self._request(
            "POST",
            f"/api/v1/units/{quote(unit, safe='')}/operations",
            headers={"Idempotency-Key": idempotency_key},
            json={"operation": "restart", "expectedState": None, "reason": "Approved MCP restart"},
        )
        if data.get("state") not in {"success", "pending"}:
            raise ToolError(f"Restart was not accepted ({data.get('state', 'unknown')})")
        result = {
            "accepted": True,
            "operation_id": str(data["id"]),
            "accepted_at": _utc(data["requestedAt"]),
            "source_uri": f"hyperion://operations/{quote(str(data['id']), safe='')}",
        }
        with sqlite3.connect(db, timeout=10) as conn:
            conn.execute(
                "UPDATE requests SET result = ? WHERE key = ?",
                (json.dumps(result), idempotency_key),
            )
        return result


class BearerAuth:
    def __init__(self, app: ASGIApp, settings: MCPSettings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path") == "/health/live" and scope.get("method") == "GET":
            await PlainTextResponse("ok")(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        supplied = headers.get(b"authorization", b"").decode("ascii", errors="ignore")
        read = hmac.compare_digest(supplied, f"Bearer {self.settings.read_token}")
        write = bool(self.settings.write_token) and hmac.compare_digest(
            supplied, f"Bearer {self.settings.write_token}"
        )
        if not (read or write):
            await PlainTextResponse(
                "Unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )(scope, receive, send)
            return
        token = _WRITE_AUTH.set(write)
        try:
            await self.app(scope, receive, send)
        finally:
            _WRITE_AUTH.reset(token)


class HyperionMCPServer(MCPServer[Any]):
    """Use the canonical strict input models before SDK argument normalization."""

    async def list_tools(self) -> list[Tool]:
        tools = await super().list_tools()
        for tool in tools:
            if model := DIAGNOSTIC_INPUTS.get(tool.name):
                tool.input_schema = model.model_json_schema()
        return tools

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[Any, Any] | None = None,
    ) -> CallToolResult | InputRequiredResult:
        if model := DIAGNOSTIC_INPUTS.get(name):
            try:
                model.model_validate(arguments)
            except ValidationError as exc:
                raise ToolError(
                    "Invalid diagnostic arguments; follow the advertised resource schema"
                ) from exc
        return await super().call_tool(name, arguments, context)


def create_mcp(settings: MCPSettings, gateway: HyperionGateway | None = None) -> MCPServer[Any]:
    gateway = gateway or HyperionGateway(settings)
    server: MCPServer[Any] = HyperionMCPServer(
        "hyperion-mcp", description="Allowlisted Hyperion service evidence and operations"
    )

    @server.tool(
        description="Discover exact service IDs by name, including t3code for t3-code. "
        "Use before health/log/deployment lookup when the ID is uncertain. Supports pagination.",
        structured_output=True,
    )
    async def list_services(query: str = "", limit: int = 20, offset: int = 0) -> dict[str, Any]:
        return await gateway.list_services(query, limit, offset)

    @server.tool(
        description="Read the latest observed health of one catalog service", structured_output=True
    )
    async def get_service(service: str) -> dict[str, Any]:
        return await gateway.get_service(service)

    @server.tool(
        description="Read fresh systemd runtime, current MainPID, execution history "
        "and unit children. "
        "Counters are historical; call again for a later sample to establish continuing restarts.",
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True),
    )
    async def get_service_runtime(service: str) -> ServiceRuntimeResult:
        return await gateway.get_service_runtime(service)

    @server.tool(
        description="Read fresh host TCP sockets and all visible owners for a "
        "configured endpoint ID. "
        "Discover endpoint IDs with list_services or get_service. Partial owners and IPv6 overlap "
        "do not establish an incompatible bind.",
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True),
    )
    async def get_endpoint_listeners(service: str, endpoint_id: str) -> EndpointListenersResult:
        return await gateway.get_endpoint_listeners(service, endpoint_id)

    @server.tool(
        description="Inspect a short-lived process reference returned for this "
        "service and credential. Checks process identity and resource scope. "
        "The current parent may have changed since launch. Ownership does not "
        "establish that a process is safe to stop; arguments and environment are omitted.",
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True),
    )
    async def inspect_process(service: str, process_ref: str) -> ProcessResult:
        return await gateway.inspect_process(service, process_ref)

    @server.tool(
        description="Read bounded and redacted logs of one catalog service", structured_output=True
    )
    async def get_logs(
        service: str,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return await gateway.get_logs(service, since, until, limit, cursor)

    @server.tool(
        description="Read an explicitly recorded deployment and commit SHA", structured_output=True
    )
    async def get_deployment(service: str, deployment_id: str | None = None) -> dict[str, Any]:
        return await gateway.get_deployment(service, deployment_id)

    @server.tool(
        description="Restart an allowlisted systemd service using an approved idempotency key",
        structured_output=True,
    )
    async def restart_service(service: str, idempotency_key: str) -> dict[str, Any]:
        return await gateway.restart_service(service, idempotency_key)

    return server


def create_app(settings: MCPSettings, gateway: HyperionGateway | None = None) -> ASGIApp:
    return BearerAuth(
        create_mcp(settings, gateway).streamable_http_app(
            streamable_http_path="/mcp",
            stateless_http=True,
            json_response=True,
            host=settings.host,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=list(settings.allowed_hosts),
                allowed_origins=[],
            ),
        ),
        settings,
    )


def main() -> None:
    settings = MCPSettings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
