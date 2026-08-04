"""Combined runtime provider: delegates by binding provider."""

from __future__ import annotations

import asyncio
from datetime import datetime

from ..models import Catalog
from .base import LogBinding, LogRecordIn, ProviderObservation, RuntimeProvider


class CombinedRuntimeProvider(RuntimeProvider):
    def __init__(self, docker: RuntimeProvider, systemd: RuntimeProvider) -> None:
        self._docker = docker
        self._systemd = systemd

    async def observe(self, catalog: Catalog) -> list[ProviderObservation]:
        docker_obs, systemd_obs = await asyncio.gather(
            self._docker.observe(catalog), self._systemd.observe(catalog)
        )
        return [*docker_obs, *systemd_obs]

    async def read_logs(
        self, binding: LogBinding, tail: int, before: datetime | None
    ) -> list[LogRecordIn]:
        provider = self._docker if binding.provider == "docker" else self._systemd
        return await provider.read_logs(binding, tail, before)
