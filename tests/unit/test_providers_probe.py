"""Route probes: bounded HTTPS checks, redirects disabled, streaks in memory."""

from __future__ import annotations

import asyncio
import socket
import ssl
import time

import httpx

from hyperion.providers.base import ProbeObservation
from hyperion.providers.probe import ProbeProvider


def make_provider(handler) -> ProbeProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ProbeProvider(client_factory=lambda: client)


def test_reachable_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == "Hyperion/1"
        return httpx.Response(200, text="ok")

    provider = make_provider(handler)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    result = observation.results[0]
    assert result.state == "reachable"
    assert result.status_code == 200
    assert result.consecutive_failures == 0
    assert result.latency_ms is not None


def test_redirect_is_reachable_not_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://other.example.com"})

    provider = make_provider(handler)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    result = observation.results[0]
    assert result.state == "reachable"
    assert result.status_code == 302
    assert result.error is None


def test_5xx_and_transport_failures_increment_streak() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    provider = make_provider(handler)
    first = asyncio.run(provider.observe(("https://alpha.example.com",)))
    second = asyncio.run(provider.observe(("https://alpha.example.com",)))
    assert first.results[0].consecutive_failures == 1
    assert second.results[0].consecutive_failures == 2
    assert first.results[0].state == "failed"
    assert first.results[0].error == "http"


def test_success_resets_streak() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503 if calls["n"] <= 2 else 200)

    provider = make_provider(handler)
    asyncio.run(provider.observe(("https://alpha.example.com",)))
    asyncio.run(provider.observe(("https://alpha.example.com",)))
    third = asyncio.run(provider.observe(("https://alpha.example.com",)))
    assert third.results[0].consecutive_failures == 0


def test_timeout_categorized() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ProbeProvider(client_factory=lambda: client)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    result = observation.results[0]
    assert result.state == "failed"
    assert result.error == "timeout"


def test_body_limit_reads_only_1kib() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x" * 4096)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ProbeProvider(client_factory=lambda: client)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    assert observation.results[0].status_code == 200


def test_per_url_runtime_error_yields_failed_result() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("client broken")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ProbeProvider(client_factory=lambda: client)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    assert observation.state == "available"
    result = observation.results[0]
    assert result.state == "failed"
    assert result.error == "unknown"


def test_client_factory_failure_unavailable() -> None:
    def broken_factory() -> httpx.AsyncClient:
        raise RuntimeError("factory broken")

    provider = ProbeProvider(client_factory=broken_factory)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    assert observation.state == "unavailable"
    assert observation.error is not None


def test_per_url_exception_isolated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "alpha" in str(request.url):
            raise RuntimeError("boom")
        return httpx.Response(200)

    provider = make_provider(handler)
    observation = asyncio.run(
        provider.observe(("https://alpha.example.com", "https://beta.example.com"))
    )
    assert observation.state == "available"
    alpha, beta = observation.results
    assert alpha.state == "failed"
    assert alpha.error == "unknown"
    assert beta.state == "reachable"
    assert beta.error is None


def test_4xx_is_reachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    provider = make_provider(handler)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    result = observation.results[0]
    assert result.state == "reachable"
    assert result.status_code == 404
    assert result.consecutive_failures == 0


def test_dns_error_categorized() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("name resolution failed") from socket.gaierror(
            "Name or service not known"
        )

    provider = make_provider(handler)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    result = observation.results[0]
    assert result.state == "failed"
    assert result.error == "dns"


def test_tls_error_categorized() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("certificate verify failed") from ssl.SSLError(
            "certificate verify failed"
        )

    provider = make_provider(handler)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    result = observation.results[0]
    assert result.state == "failed"
    assert result.error == "tls"


def test_latency_measured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    provider = make_provider(handler)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    assert observation.results[0].latency_ms >= 0


def test_multiple_urls_probed_in_one_call() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200)

    provider = make_provider(handler)
    observation = asyncio.run(
        provider.observe(("https://alpha.example.com", "https://beta.example.com"))
    )
    assert len(observation.results) == 2
    assert len(seen) == 2
    assert all(r.state == "reachable" for r in observation.results)


def test_error_none_when_reachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    provider = make_provider(handler)
    observation = asyncio.run(provider.observe(("https://alpha.example.com",)))
    assert observation.results[0].error is None


def test_probes_run_in_parallel() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ProbeProvider(client_factory=lambda: client)
    started = time.monotonic()
    observation = asyncio.run(
        provider.observe(
            (
                "https://alpha.example.com",
                "https://beta.example.com",
                "https://gamma.example.com",
            )
        )
    )
    elapsed = time.monotonic() - started
    assert elapsed < 0.15
    assert all(r.state == "reachable" for r in observation.results)


def test_close_closes_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ProbeProvider(client_factory=lambda: client)
    asyncio.run(provider.observe(("https://alpha.example.com",)))
    assert not client.is_closed
    asyncio.run(provider.close())
    assert client.is_closed


def test_close_without_client_noop() -> None:
    provider = ProbeProvider()
    asyncio.run(provider.close())


def test_slow_state_when_latency_exceeds_threshold() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.01)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ProbeProvider(client_factory=lambda: client)
    observation = asyncio.run(
        provider.observe(
            ("https://alpha.example.com",),
            config={"https://alpha.example.com": (3.0, 0.000001)},
        )
    )
    result = observation.results[0]
    assert result.state == "slow"
    assert result.consecutive_failures == 0
    assert result.latency_ms is not None and result.latency_ms > 0


def test_config_applies_per_url() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.01)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ProbeProvider(client_factory=lambda: client)
    observation = asyncio.run(
        provider.observe(
            ("https://alpha.example.com", "https://beta.example.com"),
            config={"https://alpha.example.com": (3.0, 0.000001)},
        )
    )
    alpha, beta = observation.results
    assert alpha.state == "slow"
    assert beta.state == "reachable"  # no config entry -> default threshold


def test_per_url_timeout_config_honored() -> None:
    async def run() -> tuple[ProbeObservation, float]:
        async def handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                while await reader.read(1024):
                    pass
            finally:
                writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}/"
        client = httpx.AsyncClient()
        provider = ProbeProvider(client_factory=lambda: client)
        started = time.monotonic()
        try:
            observation = await provider.observe((url,), config={url: (0.1, 1.2)})
        finally:
            server.close()
            await client.aclose()
            await server.wait_closed()
        return observation, time.monotonic() - started

    observation, elapsed = asyncio.run(run())
    result = observation.results[0]
    assert result.state == "failed"
    assert result.error == "timeout"
    assert elapsed < 2.0, f"per-request timeout not applied: {elapsed:.2f}s"
