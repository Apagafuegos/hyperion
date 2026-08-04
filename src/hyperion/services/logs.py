"""Log gateway: allowlist resolution and bounded on-demand log reads."""

from __future__ import annotations

from datetime import UTC, datetime

from ..api.errors import ApiError
from ..models import Catalog, LogRecord, LogsResponse
from ..providers.base import LogBinding, LogRecordIn, RuntimeProvider


class LogGateway:
    def __init__(self, runtime_provider: RuntimeProvider, catalog: Catalog) -> None:
        self._provider = runtime_provider
        self._catalog = catalog

    async def read(
        self, service_id: str, source: str | None, tail: int, before: datetime | None
    ) -> LogsResponse:
        service = next((s for s in self._catalog.services if s.service_id == service_id), None)
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
