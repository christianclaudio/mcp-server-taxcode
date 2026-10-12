"""Protocol integration tests for MCPServer JSON-RPC initialization and tool discovery."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import pytest
from fastmcp import Client

from scripts.check_tool_contract import EXPECTED_TOOLS
from taxcode_mcp import server
from taxcode_mcp.server import mcp


def test_stdio_initialize_handshake() -> None:
    """Verify end-to-end JSON-RPC initialization handshake over stdio."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "taxcode_mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    init_payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2026-07-28",
            "capabilities": {},
            "clientInfo": {"name": "pytest-protocol-client", "version": "1.0.0"},
        },
    }

    try:
        stdout_data, stderr_data = proc.communicate(
            input=json.dumps(init_payload) + "\n", timeout=10
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("Stdio initialization handshake timed out.")

    lines = [line.strip() for line in stdout_data.split("\n") if line.strip()]
    assert lines, f"No stdio response received from server. Stderr: {stderr_data}"

    response = json.loads(lines[0])
    assert "result" in response, f"Invalid handshake response: {response}"
    assert "protocolVersion" in response["result"]
    assert response["result"]["serverInfo"]["name"] == "taxcode-mcp"


@pytest.mark.asyncio
async def test_dynamic_tools_listing() -> None:
    """Verify MCPServer dynamically advertises tools with valid schemas and annotations."""
    tools = await mcp.list_tools()
    assert len(tools) > 0

    for tool in tools:
        assert tool.name
        assert tool.description
        assert (
            getattr(tool, "input_schema", None) is not None
            or getattr(tool, "parameters", None) is not None
        )

        assert tool.annotations is not None
        # Assert MCP 2.x annotation schema presence
        assert hasattr(tool.annotations, "read_only_hint")
        assert hasattr(tool.annotations, "destructive_hint")


@pytest.mark.asyncio
async def test_dynamic_tool_call_dispatch() -> None:
    """Verify tool dispatch via FastMCP in-memory Client and direct call_tool."""
    async with Client(mcp) as client:
        res = await client.call_tool("system_get_health_status", {})
        assert res is not None
        assert len(res.content) > 0
        assert not res.is_error

    direct_res = await mcp.call_tool("system_get_health_status", {})
    assert direct_res is not None
    assert len(direct_res.content) > 0
    assert not direct_res.is_error


@pytest.mark.asyncio
async def test_in_memory_client_lifespan() -> None:
    """Verify FastMCP 4 in-memory Client initializes server lifespan context."""
    async with Client(mcp) as client:
        tools = await client.list_tools()
        # The tool contract owns the expected set and count (AGENTS.md Key Paths).
        assert {t.name for t in tools} == set(EXPECTED_TOOLS)
        assert len(tools) == len(EXPECTED_TOOLS)
        res = await client.call_tool("items_list_items", {"limit": 5, "offset": 0})
        assert not res.is_error
        assert len(res.content) > 0


@pytest.mark.asyncio
async def test_dynamic_resources_and_prompts() -> None:
    """Verify native resources and prompts discovery on MCPServer."""
    resources = await mcp.list_resources()
    assert any("reference/capabilities" in str(r.uri) for r in resources)

    prompts = await mcp.list_prompts()
    assert any("analyze_item" in p.name for p in prompts)


@pytest.mark.asyncio
async def test_stateless_streamable_http_standalone_post(
    mock_transport: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Streamable HTTP in stateless mode accepts standalone requests without session ID."""
    import httpx

    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))

    app = mcp.streamable_http_app(stateless_http=True, json_response=True)
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            # 1. Standalone server/discover (MCP 2026-07-28 negotiation)
            discover_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {"_meta": meta},
            }
            res = await client.post(
                "/mcp",
                json=discover_payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "server/discover",
                },
            )
            assert res.status_code == 200
            assert "Mcp-Session-Id" not in res.headers
            data = res.json()
            assert "result" in data
            assert "supportedVersions" in data["result"]
            assert "2026-07-28" in data["result"]["supportedVersions"]

            # 2. Standalone tools/list with routing headers and _meta
            list_payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {"_meta": meta},
            }
            res_list = await client.post(
                "/mcp",
                json=list_payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/list",
                },
            )
            assert res_list.status_code == 200
            assert "Mcp-Session-Id" not in res_list.headers
            list_data = res_list.json()
            assert "result" in list_data
            assert "tools" in list_data["result"]
            assert len(list_data["result"]["tools"]) > 0

            # 3. Standalone tools/call with Mcp-Name and _meta
            tool_payload = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "system_get_health_status",
                    "arguments": {},
                    "_meta": meta,
                },
            }
            res_tool = await client.post(
                "/mcp",
                json=tool_payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "system_get_health_status",
                },
            )
            assert res_tool.status_code == 200
            assert "Mcp-Session-Id" not in res_tool.headers
            tool_data = res_tool.json()
            assert "result" in tool_data
            assert "content" in tool_data["result"]
            assert json.loads(tool_data["result"]["content"][0]["text"]) == {
                "status": "success",
                "data": {"status": "healthy", "uptime": 1234},
            }
