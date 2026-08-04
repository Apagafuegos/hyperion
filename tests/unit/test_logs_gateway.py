"""Log gateway behavior: merge ordering and tail semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from hyperion.catalog import load_catalog
from hyperion.providers.base import LogBinding, LogRecordIn
from hyperion.services.logs import LogGateway

FIXTURES = Path(__file__).parents[1] / "fixtures"

NOW = datetime(2026, 8, 4, 6, 0, 0, tzinfo=UTC)

RESOLVED = {("authentik", "server"): "authentik-server-1"}


class FakeRuntime:
    """RuntimeProvider that serves canned records for any binding."""

    def __init__(self, records: list[LogRecordIn]) -> None:
        self.records = records

    async def observe(self, catalog):
        return []

    async def read_logs(self, binding: LogBinding, tail, before):
        return self.records


def rec(timestamp: datetime | None, message: str) -> LogRecordIn:
    return LogRecordIn(
        timestamp=timestamp,
        source="s",
        provider="docker",
        stream="stdout",
        severity="info",
        message=message,
    )


def make_gateway(records: list[LogRecordIn]) -> LogGateway:
    catalog = load_catalog(FIXTURES / "fixture-services.yaml")
    return LogGateway(FakeRuntime(records), catalog, resolve_references=lambda: RESOLVED)


async def test_none_timestamped_records_sort_last() -> None:
    older = rec(NOW - timedelta(minutes=1), "timestamped-older")
    untimed = rec(None, "untimed")
    gateway = make_gateway([untimed, older])
    response = await gateway.read("authentik", "server", 100, None)
    assert [r.message for r in response.records] == ["timestamped-older", "untimed"]
