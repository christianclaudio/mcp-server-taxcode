"""End-to-end live testing across all dynamically discovered MCP tools."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from fastmcp.exceptions import ToolError

from taxcode_mcp.errors import SafetyViolationError, redact_secrets
from taxcode_mcp.server import client, mcp

SAFE_TOOL_FIXTURES: dict[str, dict[str, Any]] = {
    # Destructive tools (tested safely with confirm=False)
    "delete_item": {"item_id": "probe-test", "confirm": False},
    "items_delete_item": {"item_id": "probe-test", "confirm": False},
    "bulk_delete_items": {"item_ids": ["probe-test"], "confirm": False},
    "items_bulk_delete_items": {"item_ids": ["probe-test"], "confirm": False},
    # Parameterized read tools with probe IDs
    "get_item": {"item_id": "probe-test"},
    "items_get_item": {"item_id": "probe-test"},
}

SAFE_ARGUMENTLESS_LIST_TOOLS: set[str] = {
    "get_health_status",
    "system_get_health_status",
    "list_items",
    "items_list_items",
}

INTENTIONALLY_SKIPPED_MUTATING_TOOLS: set[str] = {
    "create_item",
    "items_create_item",
}


async def dispatch_tool_call(
    tool_name: str,
    is_destructive: bool,
    required_args: list[str] | None = None,
    allow_destructive_probe: bool = False,
) -> tuple[str, bool, str | None]:
    """Execute a single tool call and return (status, is_error, error_message)."""
    if is_destructive and not allow_destructive_probe:
        return ("SKIP", False, "Skipped destructive tool to protect live state")

    try:
        if tool_name in SAFE_TOOL_FIXTURES:
            res = await mcp.call_tool(tool_name, SAFE_TOOL_FIXTURES[tool_name])
        elif tool_name in SAFE_ARGUMENTLESS_LIST_TOOLS:
            args = {"limit": 5} if "list" in tool_name else {}
            res = await mcp.call_tool(tool_name, args)
        elif tool_name in INTENTIONALLY_SKIPPED_MUTATING_TOOLS:
            return ("SKIP", False, "Intentionally skipped mutating tool to protect live state")
        else:
            return ("FAIL", True, f"Unclassified tool '{tool_name}' is not in a safety allowlist")

        if hasattr(res, "content"):
            content_str = (
                res.content[0].text if res.content and hasattr(res.content[0], "text") else ""
            )
            if "requires explicit confirm=True" in content_str or "Safety Gate:" in content_str:
                return ("EXPECTED_ERROR", False, None)

            is_err = getattr(res, "is_error", getattr(res, "isError", False))
            status = "FAIL" if is_err else "PASS"
            return (status, is_err, None if not is_err else redact_secrets(content_str))

        return ("PASS", False, None)
    except Exception as exc:
        if isinstance(exc.__cause__, SafetyViolationError) or isinstance(exc, SafetyViolationError):
            return ("EXPECTED_ERROR", False, None)
        if isinstance(exc, ToolError) and ("404" in str(exc) or "not found" in str(exc).lower()):
            return ("EXPECTED_ERROR", False, None)
        return ("FAIL", True, redact_secrets(str(exc)))


@pytest.mark.asyncio
async def test_dispatch_tool_call_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify tool invocation logic and dispatch behavior using HTTP transport mocking."""
    request_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        url_str = str(request.url)
        if "items/probe-test" in url_str:
            return httpx.Response(200, json={"id": "probe-test", "name": "Test Item"})
        if "items" in url_str:
            return httpx.Response(200, json={"items": [{"id": "1", "name": "First Item"}]})
        return httpx.Response(200, json={})

    mock_transport = httpx.MockTransport(mock_handler)
    mock_http = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com/v1")
    monkeypatch.setattr(client, "_custom_client", mock_http)

    # 1. Successful non-destructive parameterized read tool
    status, is_err, err = await dispatch_tool_call(
        "items_get_item", is_destructive=False, required_args=["item_id"]
    )
    assert status == "PASS"
    assert not is_err
    assert err is None

    # 2a. Destructive tool skipped by default in live dispatch mode
    status, is_err, err = await dispatch_tool_call(
        "items_delete_item", is_destructive=True, required_args=["item_id"]
    )
    assert status == "SKIP"
    assert not is_err

    # 2b. Destructive tool with safety gate probe enabled returns EXPECTED_ERROR
    requests_before_delete = request_count
    status, is_err, err = await dispatch_tool_call(
        "items_delete_item",
        is_destructive=True,
        required_args=["item_id"],
        allow_destructive_probe=True,
    )
    assert status == "EXPECTED_ERROR"
    assert not is_err
    assert request_count == requests_before_delete

    # 3. List tool from positive allowlist with pagination argument
    status, is_err, err = await dispatch_tool_call("items_list_items", is_destructive=False)
    assert status == "PASS"
    assert not is_err

    # 4. Intentionally skipped mutating tool
    status, is_err, err = await dispatch_tool_call(
        "items_create_item", is_destructive=False, required_args=["name"]
    )
    assert status == "SKIP"
    assert not is_err

    # 5. Unclassified unknown tool returns FAIL rather than executing unchecked
    status, is_err, err = await dispatch_tool_call(
        "unknown_future_tool", is_destructive=False, required_args=["foo"]
    )
    assert status == "FAIL"
    assert is_err
    assert "is not in a safety allowlist" in str(err)

    # 6. Expected 404/not-found response returns EXPECTED_ERROR
    def not_found_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"status": "error", "message": "Item not found"})

    nf_transport = httpx.MockTransport(not_found_handler)
    nf_http = httpx.AsyncClient(transport=nf_transport, base_url="https://api.example.com/v1")
    monkeypatch.setattr(client, "_custom_client", nf_http)

    status, is_err, err = await dispatch_tool_call(
        "items_get_item", is_destructive=False, required_args=["item_id"]
    )
    assert status == "EXPECTED_ERROR"
    assert not is_err

    # 7. Error response with 500 status code fails with secret redaction
    monkeypatch.setenv("TAXCODE_MCP_API_KEY", "secret-api-key")

    def err_handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("transport broken with TAXCODE_MCP_API_KEY=secret-api-key")

    err_transport = httpx.MockTransport(err_handler)
    err_http = httpx.AsyncClient(transport=err_transport, base_url="https://api.example.com/v1")
    monkeypatch.setattr(client, "_custom_client", err_http)

    status, is_err, err = await dispatch_tool_call(
        "items_get_item", is_destructive=False, required_args=["item_id"]
    )

    assert status == "FAIL"
    assert is_err
    assert err is not None
    assert "secret-api-key" not in err
    assert "[REDACTED]" in err


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_all_discovered_tools_live() -> None:
    """Dynamically discover and exercise registered tools against live endpoints."""
    if "api.example.com" in client.base_url:
        pytest.skip("Live E2E test requires a non-placeholder API base URL")

    tools = await mcp.list_tools()
    assert len(tools) > 0, "No tools registered in MCPServer"

    results: list[dict[str, Any]] = []

    for tool in tools:
        t0 = time.perf_counter()
        tool_name = tool.name
        annotations = tool.annotations
        is_destructive = getattr(annotations, "destructive_hint", False)
        required_args = tool.input_schema.get("required", []) if tool.input_schema else []

        status, is_err, err_msg = await dispatch_tool_call(tool_name, is_destructive, required_args)
        latency_ms = (time.perf_counter() - t0) * 1000

        res_entry: dict[str, Any] = {
            "tool": tool_name,
            "status": status,
            "latency_ms": latency_ms,
            "is_error": is_err,
        }
        if err_msg:
            res_entry["error"] = err_msg
        results.append(res_entry)

    failed = [r for r in results if r["status"] == "FAIL"]
    assert not failed, f"E2E live tool verification failed for: {failed}"
    executed = [r for r in results if r["status"] == "PASS"]
    assert len(executed) > 0, "Expected at least one tool to be safely executed"
