"""Immutable in-memory snapshot store."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta

from ..models import AtlasSnapshot, ProviderStatus

FRESH_WINDOW = timedelta(seconds=45)


class SnapshotStore:
    """Holds the latest immutable snapshot and swaps references atomically."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot: AtlasSnapshot | None = None
        self._etag: str | None = None

    def publish(self, snapshot: AtlasSnapshot) -> None:
        etag = _compute_etag(snapshot)
        with self._lock:
            self._snapshot = snapshot
            self._etag = etag

    def get(self) -> tuple[AtlasSnapshot, str] | None:
        with self._lock:
            if self._snapshot is None or self._etag is None:
                return None
            return self._snapshot, self._etag


def _compute_etag(snapshot: AtlasSnapshot) -> str:
    canonical = json.dumps(
        snapshot.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f'"{digest}"'


def compute_fresh(
    providers: dict[str, ProviderStatus], required: set[str], now: datetime
) -> bool:
    """The snapshot is fresh while every required provider observed within the window."""
    for name in required:
        status = providers.get(name)
        if status is None:
            continue
        if status.state == "unavailable":
            return False
        if status.observed_at is None or now - status.observed_at > FRESH_WINDOW:
            return False
    return True
