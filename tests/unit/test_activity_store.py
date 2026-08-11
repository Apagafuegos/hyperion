"""Activity store: schema, idempotency, pruning, availability behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hyperion.services.activity import ActivityStore


def _store(tmp_path: Path, retention_days: int = 90):
    return ActivityStore(tmp_path / "activity.db", retention_days=retention_days)


def test_records_and_queries_newest_first(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(
        "operation", "t3code.service", "restart requested",
        result="info", identity="owner", target_type="unit",
    )
    store.record(
        "schedule_execution", "backup", "backup ran",
        result="success", target_type="schedule",
    )
    store.record(
        "service_transition", "mcp-observatory", "went down",
        result="warning", target_type="service",
    )

    response = store.query(limit=10)
    assert len(response.records) == 3
    # Newest first.
    assert response.records[0].kind == "service_transition"
    assert response.records[0].message == "went down"


def test_dedupe_key_makes_insertion_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("unit_transition", "t3code.service", "first", dedupe_key="k1")
    store.record("unit_transition", "t3code.service", "duplicate", dedupe_key="k1")
    response = store.query(limit=10)
    assert len(response.records) == 1
    assert response.records[0].message == "first"


def test_since_filter(tmp_path: Path) -> None:
    store = _store(tmp_path)
    now = datetime(2026, 8, 9, 9, 0, 0, tzinfo=UTC)
    store.record(
        "operation", "a", "old",
        occurred_at=now - timedelta(hours=2), target_type="system",
    )
    store.record(
        "operation", "b", "recent",
        occurred_at=now - timedelta(minutes=10), target_type="system",
    )
    since = now - timedelta(hours=1)
    records = store.since(since)
    assert [r.message for r in records] == ["recent"]


def test_pruning_removes_expired(tmp_path: Path) -> None:
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    store = _store(tmp_path, retention_days=1)
    store.record(
        "operation", "old", "expired",
        occurred_at=now - timedelta(days=5),
        target_type="system",
    )
    store.record(
        "operation", "fresh", "kept",
        occurred_at=now - timedelta(minutes=10),
        target_type="system",
    )
    # Pruning runs on every write; query after the second write.
    response = store.query(limit=10)
    assert [r.message for r in response.records] == ["kept"]


def test_unwritable_store_is_degraded_not_fatal(tmp_path: Path) -> None:
    # A path whose parent is a regular file cannot be created; the store must
    # degrade quietly instead of raising at construction.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    store = ActivityStore(blocker / "sub" / "activity.db")
    store.record("operation", "t3code", "dropped silently", target_type="unit")
    response = store.query(limit=10)
    assert response.records == []


def test_readonly_db_queries_return_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("operation", "a", "before", target_type="system")
    store.close()
    # Simulate a read-only mount by pointing at a directory.
    path = tmp_path / "ro.db"
    path.mkdir()
    store2 = ActivityStore(path)
    response = store2.query(limit=10)
    assert response.records == []


def test_unknown_kind_and_result_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.record("not-a-kind", "x", "msg")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        store.record("operation", "x", "msg", result="not-a-result")  # type: ignore[arg-type]
