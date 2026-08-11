"""Bounded SQLite activity store under Hyperion's writable state directory.

Versioned schema migrations, stable event identity, timestamp ordering,
idempotent insertion for repeated provider reports, automatic pruning, and
clear behavior when the database is unavailable or read-only.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from ..models import ActivityKind, ActivityRecord, ActivityResponse, ActivityResult

logger = logging.getLogger("hyperion.activity")

_SCHEMA_VERSION = 1
_DEFAULT_RETENTION_DAYS = 90

_DDL = """
CREATE TABLE IF NOT EXISTS activity (
    id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target TEXT NOT NULL,
    identity TEXT NOT NULL,
    result TEXT NOT NULL,
    message TEXT NOT NULL,
    evidence TEXT NOT NULL,
    dedupe_key TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS activity_dedupe ON activity (dedupe_key)
    WHERE dedupe_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS activity_occurred ON activity (occurred_at);
CREATE INDEX IF NOT EXISTS activity_kind ON activity (kind, occurred_at);
"""

_KINDS = {
    "host_threshold", "unit_transition", "service_transition",
    "schedule_execution", "schedule_change", "operation",
}
_RESULTS = {"success", "failure", "warning", "pending", "info", "denied"}


class ActivityUnavailable(RuntimeError):
    """The activity database cannot be opened or written."""


class ActivityStore:
    """Thread-safe, bounded SQLite activity store."""

    def __init__(
        self,
        path: Path,
        retention_days: int = _DEFAULT_RETENTION_DAYS,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = path
        self._retention_days = retention_days
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._open()

    def _open(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False: the RLock serializes all access, and the
            # lifespan may construct the store on a different thread than the
            # request handler threads that query it.
            conn = sqlite3.connect(self._path, timeout=5.0, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            with conn:
                conn.executescript(_DDL)
            self._migrate(conn)
            self._conn = conn
        except (OSError, sqlite3.Error) as exc:
            logger.error("activity store unavailable at %s: %s", self._path, exc)
            self._conn = None

    def _migrate(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("PRAGMA user_version").fetchone()
        version = row[0] if row else 0
        if version > _SCHEMA_VERSION:
            raise ActivityUnavailable(
                f"activity schema {version} is newer than supported {_SCHEMA_VERSION}"
            )
        if version < _SCHEMA_VERSION:
            with conn:
                conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            logger.info("activity schema migrated to %s", _SCHEMA_VERSION)

    def _prune(self) -> None:
        if self._conn is None:
            return
        cutoff = (self._now() - timedelta(days=self._retention_days)).isoformat()
        try:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM activity WHERE occurred_at < ?", (cutoff,)
                )
        except sqlite3.Error as exc:
            logger.error("activity pruning failed: %s", exc)

    def record(
        self,
        kind: ActivityKind,
        target: str,
        message: str,
        *,
        result: ActivityResult = "info",
        identity: str = "system",
        target_type: Literal["host", "unit", "service", "schedule", "system"] = "system",
        occurred_at: datetime | None = None,
        dedupe_key: str | None = None,
        evidence: dict[str, str] | None = None,
    ) -> ActivityRecord:
        """Insert one event; a duplicate dedupe_key is ignored."""
        if kind not in _KINDS:
            raise ValueError(f"unknown activity kind {kind!r}")
        if result not in _RESULTS:
            raise ValueError(f"unknown activity result {result!r}")
        record = ActivityRecord(
            id=uuid.uuid4().hex[:16],
            occurred_at=occurred_at or self._now(),
            kind=kind,
            target_type=target_type,
            target=target[:128],
            identity=identity[:64],
            result=result,
            message=message[:512],
            evidence=evidence or {},
        )
        if self._conn is None:
            logger.warning("activity record dropped: store unavailable")
            return record
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT OR IGNORE INTO activity "
                    "(id, occurred_at, kind, target_type, target, identity, result, "
                    " message, evidence, dedupe_key) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.id,
                        record.occurred_at.isoformat(),
                        record.kind,
                        record.target_type,
                        record.target,
                        record.identity,
                        record.result,
                        record.message,
                        _json_dumps(record.evidence),
                        dedupe_key,
                    ),
                )
                self._prune()
        except sqlite3.Error as exc:
            logger.error("activity insert failed: %s", exc)
            raise ActivityUnavailable(str(exc)) from exc
        return record

    def query(
        self,
        *,
        since: datetime | None = None,
        limit: int = 100,
        kind: str | None = None,
        target: str | None = None,
    ) -> ActivityResponse:
        """Return events newest-first within the optional window."""
        if self._conn is None:
            return ActivityResponse(since=since, requested_at=self._now(), records=[])
        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("occurred_at >= ?")
            params.append(since.isoformat())
        if kind in _KINDS:
            clauses.append("kind = ?")
            params.append(kind)
        if target is not None:
            clauses.append("target = ?")
            params.append(target[:128])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT id, occurred_at, kind, target_type, target, identity, result, "
            f"message, evidence FROM activity{where} "
            f"ORDER BY occurred_at DESC LIMIT ?"
        )
        params.append(max(1, min(limit, 200)))
        try:
            with self._lock:
                rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            logger.error("activity query failed: %s", exc)
            raise ActivityUnavailable(str(exc)) from exc
        records = [_row_to_record(row) for row in rows]
        return ActivityResponse(since=since, requested_at=self._now(), records=records)

    def since(self, since: datetime) -> list[ActivityRecord]:
        return self.query(since=since).records

    def last_occurred(self) -> datetime | None:
        """Newest recorded occurrence, for 'since your last visit' markers."""
        if self._conn is None:
            return None
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT occurred_at FROM activity ORDER BY occurred_at DESC LIMIT 1"
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        return _parse_iso(row[0])

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None


def _row_to_record(row: tuple[Any, ...]) -> ActivityRecord:
    return ActivityRecord(
        id=row[0],
        occurred_at=_parse_iso(row[1]),
        kind=row[2],
        target_type=row[3],
        target=row[4],
        identity=row[5],
        result=row[6],
        message=row[7],
        evidence=_json_loads(row[8]),
    )


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _json_dumps(value: dict[str, str]) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str) -> dict[str, str]:
    import json

    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if isinstance(loaded, dict):
        return {str(k): str(v) for k, v in loaded.items()}
    return {}
