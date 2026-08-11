"""Refresh supervisor: monotonic scheduling, no overlapping cycles."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from ..catalog import CatalogError, load_catalog
from ..models import AtlasSnapshot, Catalog
from ..providers.base import ProbeObservation, RouteProbeProvider, RuntimeProvider
from .reconciler import reconcile
from .snapshots import SnapshotStore

logger = logging.getLogger("hyperion.supervisor")

RUNTIME_INTERVAL_SECONDS = 15
PROBE_INTERVAL_SECONDS = 30


class RefreshSupervisor:
    def __init__(
        self,
        runtime_provider: RuntimeProvider,
        probe_provider: RouteProbeProvider,
        catalog: Catalog,
        store: SnapshotStore,
        base_catalog: Catalog | None = None,
        catalog_path: Path | None = None,
        on_catalog: Callable[[Catalog], None] | None = None,
        on_snapshot: Callable[[AtlasSnapshot], None] | None = None,
        runtime_interval: float = RUNTIME_INTERVAL_SECONDS,
        probe_interval: float = PROBE_INTERVAL_SECONDS,
    ) -> None:
        self._runtime_provider = runtime_provider
        self._probe_provider = probe_provider
        self._catalog = catalog
        self._base_catalog = base_catalog or catalog
        self._catalog_path = catalog_path
        self._on_catalog = on_catalog
        self._on_snapshot = on_snapshot
        self._store = store
        self._runtime_interval = runtime_interval
        self._probe_interval = probe_interval
        self._probe_observation: ProbeObservation | None = None
        self._cancelled = False

    def _probe_urls(self) -> tuple[str, ...]:
        return tuple(
            s.route_probe.url for s in self._catalog.services if s.route_probe is not None
        )

    def _probe_config(self) -> dict[str, tuple[float, float]]:
        return {
            s.route_probe.url: (
                s.route_probe.timeout_ms / 1000,
                s.route_probe.slow_after_ms / 1000,
            )
            for s in self._catalog.services
            if s.route_probe is not None
        }

    async def run(self) -> None:
        """Run both loops until cancelled; cycles are sequential, never queued."""
        tasks = [
            asyncio.create_task(self._runtime_loop()),
            asyncio.create_task(self._probe_loop()),
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            self._cancelled = True
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _runtime_loop(self) -> None:
        while not self._cancelled:
            started = asyncio.get_running_loop().time()
            await self._runtime_cycle()
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.0, self._runtime_interval - elapsed))

    async def _probe_loop(self) -> None:
        while not self._cancelled:
            started = asyncio.get_running_loop().time()
            await self._probe_cycle()
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.0, self._probe_interval - elapsed))

    async def _runtime_cycle(self) -> None:
        try:
            await self._refresh_catalog()
            runtime_observations = await self._runtime_provider.observe(self._catalog)
            probe_observation = self._probe_observation or await self._probe_provider.observe(
                self._probe_urls(), self._probe_config()
            )
            snapshot = reconcile(self._catalog, runtime_observations, probe_observation)
            self._store.publish(snapshot)
            if self._on_snapshot is not None:
                self._on_snapshot(snapshot)
        except Exception as exc:  # provider boundary; keep the last valid snapshot
            logger.error("runtime refresh cycle failed: %s", exc)

    async def _refresh_catalog(self) -> None:
        """Reload the overlay and merge the current Docker inventory."""
        if self._catalog_path is not None:
            try:
                self._base_catalog = load_catalog(self._catalog_path)
            except CatalogError as exc:
                logger.error("catalog reload rejected; keeping last valid catalog: %s", exc)

        discover = getattr(self._runtime_provider, "discover_catalog", None)
        if discover is None:
            updated = self._base_catalog
        else:
            try:
                updated = await discover(self._base_catalog)
            except Exception as exc:
                logger.error("Docker catalog discovery failed; keeping current catalog: %s", exc)
                return
        self._catalog = updated
        if self._on_catalog is not None:
            self._on_catalog(updated)

    async def _probe_cycle(self) -> None:
        try:
            self._probe_observation = await self._probe_provider.observe(
                self._probe_urls(), self._probe_config()
            )
        except Exception as exc:  # keep the last probe observation
            logger.error("probe refresh cycle failed: %s", exc)
