"""Log gateway provider-failure mapping to contract error codes."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hyperion.api.errors import ApiError
from hyperion.catalog import load_catalog
from hyperion.services.logs import LogGateway

FIXTURES = Path(__file__).parents[1] / "fixtures"


def catalog():
    return load_catalog(FIXTURES / "fixture-services.yaml")


class RaisingRuntime:
    """RuntimeProvider whose read_logs always raises."""

    async def observe(self, catalog):
        return []

    async def read_logs(self, binding, tail, before):
        raise RuntimeError("docker exploded")


class SlowRuntime:
    """RuntimeProvider whose read_logs hangs past the gateway timeout."""

    async def observe(self, catalog):
        return []

    async def read_logs(self, binding, tail, before):
        await asyncio.sleep(10)
        return []


async def test_provider_exception_maps_to_log_source_unavailable() -> None:
    gateway = LogGateway(RaisingRuntime(), catalog())
    with pytest.raises(ApiError) as exc:
        await gateway.read("authentik", "server", 100, None)
    assert exc.value.status_code == 503
    assert exc.value.code == "LOG_SOURCE_UNAVAILABLE"
    assert exc.value.retryable is True


async def test_provider_timeout_maps_to_provider_timeout() -> None:
    gateway = LogGateway(SlowRuntime(), catalog(), timeout=0.1)
    with pytest.raises(ApiError) as exc:
        await gateway.read("authentik", "server", 100, None)
    assert exc.value.status_code == 503
    assert exc.value.code == "PROVIDER_TIMEOUT"
    assert exc.value.retryable is True
