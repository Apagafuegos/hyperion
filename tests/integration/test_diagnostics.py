"""Host provider -> authorized HTTP -> MCP schema/structured evidence acceptance."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from mcp.server.mcpserver.exceptions import ToolError

from hyperion.mcp import HyperionGateway, MCPSettings, create_app, create_mcp


@pytest.fixture
def authorized_host(client, diagnostics_host, monkeypatch):
    monkeypatch.setenv("HYPERION_READ_TOKEN", "h" * 32)
    client.app.state.diagnostics_provider = diagnostics_host.provider
    return client, {"Authorization": "Bearer " + "h" * 32}


async def test_http_mcp_flow_validates_resource_schemas_and_returns_observations(
    authorized_host, diagnostics_host, tmp_path
):
    client, headers = authorized_host
    host = diagnostics_host
    host.add_process(22, "/system.slice/t3code.service/workers", inodes=(77,))
    host.add_socket()
    settings = MCPSettings(
        upstream_url="http://testserver",
        read_token="r" * 32,
        write_token="",
        restart_services=frozenset(),
        idempotency_db=tmp_path / "unused.db",
    )

    def handle(request):
        response = client.get(str(request.url), headers=headers)
        return httpx.Response(response.status_code, content=response.content)

    async with httpx.AsyncClient(
        base_url="http://testserver", transport=httpx.MockTransport(handle)
    ) as upstream:
        server = create_mcp(settings, HyperionGateway(settings, upstream))
        tools = {t.name: t for t in await server.list_tools()}
        for name in ("get_service_runtime", "get_endpoint_listeners", "inspect_process"):
            assert tools[name].input_schema["additionalProperties"] is False
            assert tools[name].output_schema["additionalProperties"] is False
            assert tools[name].annotations.read_only_hint is True
        discovery = await server.call_tool("list_services", {"query": "t3"})
        assert discovery.structured_content["services"][0]["diagnostic_endpoints"] == ["primary"]
        runtime = await server.call_tool("get_service_runtime", {"service": "t3-code"})
        listener = await server.call_tool(
            "get_endpoint_listeners", {"service": "t3-code", "endpoint_id": "primary"}
        )
        reference = listener.structured_content["data"]["listeners"][0]["owners"][0]["process_ref"]
        process = await server.call_tool(
            "inspect_process", {"service": "t3-code", "process_ref": reference}
        )
        assert process.structured_content["data"]["relationship_to_service"]["kind"] == "same_unit"
        for name, result in (
            ("get_service_runtime", runtime),
            ("get_endpoint_listeners", listener),
            ("inspect_process", process),
        ):
            Draft202012Validator(tools[name].output_schema).validate(result.structured_content)
            assert json.loads(result.content[0].text) == result.structured_content
            assert result.structured_content["source_uri"].startswith(
                "hyperion://diagnostics/local-host/diag_"
            )
        for name, args in (
            ("get_service_runtime", {"service": "t3-code", "unit": "ssh.service"}),
            ("get_service_runtime", {"service": 11}),
            (
                "get_endpoint_listeners",
                {"service": "t3-code", "endpoint_id": "primary", "port": 22},
            ),
            ("inspect_process", {"service": "t3-code", "process_ref": "22"}),
            ("get_endpoint_listeners", {"service": "t3-code", "endpoint_id": "missing"}),
        ):
            with pytest.raises(ToolError):
                await server.call_tool(name, args)


def test_http_auth_and_reference_cannot_expand_scope(authorized_host, monkeypatch):
    client, headers = authorized_host
    path = "/api/v1/diagnostics/service-runtime"
    assert client.get(path, params={"service": "t3-code"}).status_code == 401
    assert client.get(path, params={"service": "missing"}, headers=headers).status_code == 404
    assert (
        client.get(
            path, params={"service": "t3-code", "unit": "ssh.service"}, headers=headers
        ).status_code
        == 422
    )
    runtime = client.get(path, params={"service": "t3-code"}, headers=headers)
    assert runtime.status_code == 200 and runtime.headers["cache-control"] == "private, no-store"
    reference = runtime.json()["data"]["main_process_ref"]
    assert (
        client.get(
            "/api/v1/diagnostics/process",
            params={"service": "authentik", "process_ref": reference},
            headers=headers,
        ).status_code
        == 403
    )
    monkeypatch.setenv("HYPERION_READ_TOKEN", "n" * 32)
    rotated = {"Authorization": "Bearer " + "n" * 32}
    assert (
        client.get(
            "/api/v1/diagnostics/process",
            params={"service": "t3-code", "process_ref": reference},
            headers=rotated,
        ).status_code
        == 403
    )
    assert client.post(path, json={"service": "t3-code"}, headers=rotated).status_code == 405


async def test_partial_observation_remains_structured(authorized_host, diagnostics_host, tmp_path):
    client, headers = authorized_host
    diagnostics_host.add_socket()
    settings = MCPSettings(
        upstream_url="http://testserver",
        read_token="r" * 32,
        write_token="",
        restart_services=frozenset(),
        idempotency_db=tmp_path / "unused.db",
    )

    def handle(request):
        response = client.get(str(request.url), headers=headers)
        return httpx.Response(response.status_code, content=response.content)

    async with httpx.AsyncClient(
        base_url="http://testserver", transport=httpx.MockTransport(handle)
    ) as upstream:
        server = create_mcp(settings, HyperionGateway(settings, upstream))
        result = await server.call_tool(
            "get_endpoint_listeners", {"service": "t3-code", "endpoint_id": "primary"}
        )
        assert not result.is_error and result.structured_content["status"] == "partial"
        assert result.structured_content["data"]["listeners"][0]["owners"] is None
        assert result.structured_content["issues"]


async def test_malformed_upstream_output_is_a_tool_error(tmp_path):
    settings = MCPSettings(
        upstream_url="http://testserver",
        read_token="r" * 32,
        write_token="",
        restart_services=frozenset(),
        idempotency_db=tmp_path / "unused.db",
    )
    async with httpx.AsyncClient(
        base_url="http://testserver",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"status": "complete", "listeners": []})
        ),
    ) as upstream:
        server = create_mcp(settings, HyperionGateway(settings, upstream))
        with pytest.raises(ToolError, match="invalid listener evidence"):
            await server.call_tool(
                "get_endpoint_listeners", {"service": "t3-code", "endpoint_id": "primary"}
            )


def test_mcp_http_transport_enforces_additional_properties(tmp_path):
    settings = MCPSettings(
        upstream_url="http://testserver",
        read_token="r" * 32,
        write_token="",
        restart_services=frozenset(),
        idempotency_db=tmp_path / "unused.db",
    )
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8101") as transport:
        response = transport.post(
            "/mcp",
            headers={
                "Authorization": "Bearer " + "r" * 32,
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "get_service_runtime",
                    "arguments": {"service": "t3-code", "unit": "ssh.service"},
                },
            },
        )
    assert response.json()["result"]["isError"] is True
    assert "Invalid diagnostic arguments" in response.text


def test_admin_display_name_cannot_share_a_connector_reference(authorized_host):
    import hashlib

    client, headers = authorized_host
    runtime = client.get(
        "/api/v1/diagnostics/service-runtime", params={"service": "t3-code"}, headers=headers
    )
    reference = runtime.json()["data"]["main_process_ref"]
    colliding_name = "connector:" + hashlib.sha256(("h" * 32).encode()).hexdigest()
    result = client.get(
        "/api/v1/diagnostics/process",
        params={"service": "t3-code", "process_ref": reference},
        headers={"X-Authentik-Username": colliding_name},
    )
    assert result.status_code == 403
