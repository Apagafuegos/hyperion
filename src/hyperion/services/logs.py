"""Log gateway: allowlist resolution and bounded on-demand log reads.

Tail semantics (§9): each source is fetched with the requested tail and the
merged, time-ordered result is trimmed to the tail total. With N sources the
merged set may briefly hold up to N * tail records (per-source over-fetch is
accepted), but the final response always honors tail; the over-fetch is
bounded and never leaks into the response.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from ..api.errors import ApiError
from ..models import Catalog, LogRecord, LogsResponse
from ..providers.base import LogBinding, LogRecordIn, RuntimeProvider

# Aware sentinel for log sorting: sorts last and never mixes naive/aware datetimes.
_SENTINEL = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)


class LogGateway:
    def __init__(
        self,
        runtime_provider: RuntimeProvider,
        catalog: Catalog | Callable[[], Catalog],
        timeout: float = 5.0,
        resolve_references: Callable[[], dict[tuple[str, str], str]] | None = None,
    ) -> None:
        self._provider = runtime_provider
        self._resolve_catalog = catalog if callable(catalog) else lambda: catalog
        self._timeout = timeout
        self._resolve_references = resolve_references or (lambda: {})

    async def read(
        self, service_id: str, source: str | None, tail: int, before: datetime | None
    ) -> LogsResponse:
        catalog = self._resolve_catalog()
        service = next((s for s in catalog.services if s.service_id == service_id), None)
        if service is None:
            raise ApiError(404, "SERVICE_NOT_FOUND", "No such service in the catalog.", False)
        requested = [source] if source is not None else list(service.logs.sources)
        for name in requested:
            if name not in service.logs.sources:
                raise ApiError(404, "LOG_SOURCE_NOT_FOUND", "Log source is not allowlisted.", False)

        references = self._resolve_references()
        records: list[LogRecord] = []
        # One unresolvable source fails the whole request: the error envelope
        # is single, and silently skipping sources would drop data.
        for name in requested:
            component = next(
                (c for c in service.runtime.components if c.selector == name), None
            )
            if component is None:
                continue
            provider: Literal["docker", "systemd"] = (
                "systemd" if service.runtime.provider == "systemd" else "docker"
            )
            if provider == "docker":
                reference = references.get((service_id, name))
                if reference is None:
                    raise ApiError(
                        503,
                        "LOG_SOURCE_UNAVAILABLE",
                        "Logs are unavailable while the container reference is unknown.",
                        True,
                    )
            else:
                reference = component.selector
            binding = LogBinding(
                provider=provider,
                service_id=service_id,
                source=name,
                reference=reference,
            )
            try:
                fetched = await asyncio.wait_for(
                    self._provider.read_logs(binding, tail, before), timeout=self._timeout
                )
            except TimeoutError:
                raise ApiError(
                    503, "PROVIDER_TIMEOUT", "The log provider timed out.", True
                ) from None
            except Exception as exc:
                raise ApiError(
                    503, "LOG_SOURCE_UNAVAILABLE", "Logs are unavailable for this component.", True
                ) from exc
            records.extend(_normalize(fetched))

        # Entries without a timestamp sort after timestamped entries (the
        # first tuple element short-circuits, so naive/aware never mix);
        # stable sort preserves provider order within equal keys.
        records.sort(
            key=lambda record: (record.timestamp is None, record.timestamp or _SENTINEL)
        )
        if len(records) > tail:
            records = records[-tail:]
            truncated = True
        else:
            truncated = False
        return LogsResponse(
            service_id=service_id,
            requested_at=datetime.now(UTC),
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
