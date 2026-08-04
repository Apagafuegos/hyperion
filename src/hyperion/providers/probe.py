"""Route probe provider: bounded HTTPS checks with in-memory failure streaks."""

from __future__ import annotations

import logging
import socket
import ssl
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

import httpx

from .base import ProbeEvidence, ProbeObservation

logger = logging.getLogger("hyperion.probe")

BODY_LIMIT = 1024
USER_AGENT = "Hyperion/1"
_CONCURRENT = 4

ErrorKind = Literal["dns", "timeout", "tls", "connection", "http", "unknown"]


def _categorize_error(exc: Exception) -> ErrorKind:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        cause = exc.__cause__ or exc
        if isinstance(cause, socket.gaierror):
            return "dns"
        if isinstance(cause, ssl.SSLError) or "ssl" in str(cause).lower():
            return "tls"
        return "connection"
    if isinstance(exc, httpx.TransportError):
        return "connection"
    return "unknown"


class ProbeProvider:
    """Shared pooled client, bounded concurrency, streaks keyed by URL."""

    def __init__(self, client_factory: Callable[[], httpx.AsyncClient] | None = None) -> None:
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT},
                timeout=httpx.Timeout(3.0),
                limits=httpx.Limits(
                    max_connections=_CONCURRENT, max_keepalive_connections=_CONCURRENT
                ),
            )
        )
        self._client: httpx.AsyncClient | None = None
        self._streaks: dict[str, int] = {}

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def observe(self, probes: tuple[str, ...]) -> ProbeObservation:
        observed_at = datetime.now(UTC)
        results: list[ProbeEvidence] = []
        try:
            client = await self._http()
            for url in probes:
                results.append(await self._probe_one(client, url, observed_at))
        except Exception as exc:  # provider boundary
            logger.warning("probe observation failed: %s", exc)
            return ProbeObservation(
                provider="probe",
                state="unavailable",
                observed_at=observed_at,
                error=str(exc)[:240],
            )
        return ProbeObservation(
            provider="probe",
            state="available",
            observed_at=observed_at,
            results=results,
        )

    async def _probe_one(
        self, client: httpx.AsyncClient, url: str, observed_at: datetime
    ) -> ProbeEvidence:
        started = time.monotonic()
        status_code: int | None = None
        error: ErrorKind | None = None
        accepted = False
        try:
            async with client.stream(
                "GET", url, headers={"User-Agent": USER_AGENT}
            ) as response:
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size >= BODY_LIMIT:
                        break
                status_code = response.status_code
                accepted = 100 <= status_code < 500
        except httpx.TimeoutException as exc:
            error = "timeout"
            logger.debug("probe timeout %s: %s", url, exc)
        except httpx.ConnectError as exc:
            error = _categorize_error(exc)
            logger.debug("probe connect error %s: %s", url, exc)
        except httpx.TransportError as exc:
            error = _categorize_error(exc)
            logger.debug("probe transport error %s: %s", url, exc)

        latency_ms = int((time.monotonic() - started) * 1000)
        if accepted:
            self._streaks[url] = 0
        else:
            self._streaks[url] = self._streaks.get(url, 0) + 1
        state: Literal["reachable", "failed"] = "reachable" if accepted else "failed"
        return ProbeEvidence(
            url=url,
            state=state,
            status_code=status_code,
            latency_ms=latency_ms,
            consecutive_failures=self._streaks[url],
            observed_at=observed_at,
            error=error,
        )
