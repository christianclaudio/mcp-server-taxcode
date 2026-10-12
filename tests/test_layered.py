"""Tests for FastMCP 4 Server Composition, Hierarchical Middleware, ToolTransform, and Discovery."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import MiddlewareContext
from mcp.types import TextContent

from taxcode_mcp import server
from taxcode_mcp.config import settings
from taxcode_mcp.errors import SafetyViolationError
from taxcode_mcp.middleware import (
    ItemsDomainGuardMiddleware,
    ParentAuditMiddleware,
    ReadOnlyGateMiddleware,
)
from taxcode_mcp.server import create_server

# Tests that run the Code Mode sandbox skip without the code-mode extra; in CI the
# conftest guard aborts the session instead, so these never skip silently there.
CODE_MODE_SKIP_REASON = "needs the code-mode extra (fastmcp[code-mode] / pydantic-monty)"


@pytest.mark.asyncio
async def test_server_composition_mounting() -> None:
    """Verify root gateway mounts domain sub-servers under their respective namespaces."""
    app = create_server(profile="full", enable_tool_search=False)
    tools = await app.list_tools()
    tool_names = {t.name for t in tools}

    expected = {
        "system_get_health_status",
        "items_list_items",
        "items_get_item",
        "items_create_item",
        "items_delete_item",
        "items_bulk_delete_items",
    }
    assert expected == tool_names

    # Check resources namespacing
    resources = await app.list_resources()
    resource_uris = {str(r.uri) for r in resources}
    assert any("reference/capabilities" in uri for uri in resource_uris)

    # Check prompts namespacing
    prompts = await app.list_prompts()
    prompt_names = {p.name for p in prompts}
    assert "items_analyze_item" in prompt_names


@pytest.mark.asyncio
async def test_hierarchical_middleware_execution_order() -> None:
    """Verify Parent -> Child -> Handler -> Child -> Parent execution flow."""
    events: list[str] = []

    # Mock call_next
    async def fake_call_next(ctx: MiddlewareContext) -> dict[str, str]:
        events.append("handler")
        return {"status": "ok"}

    parent_audit = ParentAuditMiddleware()
    context = MiddlewareContext(
        method="tools/call",
        message=SimpleNamespace(name="items_list_items", arguments={}),
    )

    res = await parent_audit.on_message(context, fake_call_next)
    assert res == {"status": "ok"}
    assert "handler" in events


@pytest.mark.asyncio
async def test_parent_middleware_error_logging(caplog: pytest.LogCaptureFixture) -> None:
    """Verify ParentAuditMiddleware logs errors with redacted secrets and re-raises."""
    parent_audit = ParentAuditMiddleware()

    async def error_call_next(ctx: MiddlewareContext) -> None:
        raise ValueError("Secret Bearer sk-secret-12345 leaked in error")

    context = MiddlewareContext(
        method="tools/call",
        message=SimpleNamespace(name="items_get_item", arguments={"item_id": "123"}),
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ValueError):
            await parent_audit.on_message(context, error_call_next)

    assert "sk-secret-12345" not in caplog.text


@pytest.mark.asyncio
async def test_readonly_gate_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify ReadOnlyGateMiddleware blocks mutating tools and allows read-only tools."""
    # The gate resolves tools on the serving FastMCP instance to read readOnlyHint.
    # Built before read-only is switched on, so writes stay resolvable (not filtered).
    serving = SimpleNamespace(fastmcp=create_server(profile="full"))
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    gate = ReadOnlyGateMiddleware()

    async def allowed_call_next(ctx: MiddlewareContext) -> str:
        return "allowed"

    read_ctx = MiddlewareContext(
        method="tools/call",
        message=SimpleNamespace(name="system_get_health_status", arguments={}),
        fastmcp_context=serving,  # type: ignore[arg-type]
    )
    assert await gate.on_message(read_ctx, allowed_call_next) == "allowed"

    mutate_ctx = MiddlewareContext(
        method="tools/call",
        message=SimpleNamespace(name="items_create_item", arguments={}),
        fastmcp_context=serving,  # type: ignore[arg-type]
    )
    with pytest.raises(SafetyViolationError) as exc_info:
        await gate.on_message(mutate_ctx, allowed_call_next)
    assert "read-only mode" in str(exc_info.value)


@pytest.mark.asyncio
async def test_child_items_domain_guard_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify ItemsDomainGuardMiddleware restricts bulk destructive actions without env gate."""
    guard = ItemsDomainGuardMiddleware()

    async def dummy_next(ctx: MiddlewareContext) -> str:
        return "proceed"

    bulk_ctx = MiddlewareContext(
        method="tools/call",
        message=SimpleNamespace(name="bulk_delete_items", arguments={}),
    )

    # 1. Blocked when MCP_ALLOW_BULK_DESTRUCTIVE=False
    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", False)
    with pytest.raises(SafetyViolationError) as exc:
        await guard.on_message(bulk_ctx, dummy_next)
    assert "Bulk destructive operations disabled" in str(exc.value)

    # 2. Allowed when MCP_ALLOW_BULK_DESTRUCTIVE=True
    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", True)
    assert await guard.on_message(bulk_ctx, dummy_next) == "proceed"


@pytest.mark.asyncio
async def test_tool_transform_argument_hiding(
    mock_transport: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify ToolTransform hides client_id from input schema but injects default at execution."""

    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))

    app = create_server(profile="items", enable_tool_search=False)
    tools = await app.list_tools()
    create_tool = next(t for t in tools if t.name == "items_create_item")

    # client_id is hidden from client-visible input schema
    params = create_tool.parameters.get("properties", {})
    assert "name" in params
    assert "client_id" not in params

    # Calling tool without client_id executes with the transformed default
    res = await app.call_tool("items_create_item", {"name": "TransformTest"})
    assert not res.is_error
    assert len(res.content) > 0


@pytest.mark.asyncio
async def test_profile_based_composition() -> None:
    """Verify profiles mount only designated domain sub-servers or disable mutating tools."""
    # 1. System profile
    sys_app = create_server(profile="system")
    sys_tools = [t.name for t in await sys_app.list_tools()]
    assert sys_tools == ["system_get_health_status"]

    # 2. Items profile
    items_app = create_server(profile="items")
    items_tools = [t.name for t in await items_app.list_tools()]
    assert "items_list_items" in items_tools
    assert "system_get_health_status" not in items_tools

    # 3. Readonly profile
    ro_app = create_server(profile="readonly")
    ro_tools = [t.name for t in await ro_app.list_tools()]
    assert "system_get_health_status" in ro_tools
    assert "items_list_items" in ro_tools
    assert "items_create_item" not in ro_tools
    assert "items_delete_item" not in ro_tools


@pytest.mark.asyncio
async def test_opt_in_tool_search(mock_transport: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify --enable-tool-search replaces flat list with search_tools + call_tool."""
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))

    search_app = create_server(enable_tool_search=True)
    tools = await search_app.list_tools()
    tool_names = [t.name for t in tools]

    # Replaced by FastMCP synthetic search tools
    assert "search_tools" in tool_names
    assert "call_tool" in tool_names
    assert "system_get_health_status" not in tool_names

    # Discover tool via search
    search_res = await search_app.call_tool("search_tools", {"pattern": "health"})
    assert not search_res.is_error
    assert "system_get_health_status" in str(search_res.content)

    # Execute tool via call_tool
    call_res = await search_app.call_tool(
        "call_tool",
        {"name": "system_get_health_status", "arguments": {}},
    )
    assert not call_res.is_error
    assert "healthy" in str(call_res.content)


def test_main_profile_and_tool_search_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify CLI flags --profile and --enable-tool-search reconfigure server gateway."""
    fake_run = MagicMock()
    monkeypatch.setattr(server.mcp, "run", fake_run)
    monkeypatch.setattr(FastMCP, "run", fake_run)

    monkeypatch.setattr(
        "sys.argv",
        ["taxcode-mcp", "--profile", "system", "--enable-tool-search", "--transport", "stdio"],
    )
    server.main()
    assert server.mcp is not None


@pytest.mark.asyncio
async def test_curated_profile_stays_flat_without_discovery_meta(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Curated profiles expose a small flat domain set; never search/Code Mode meta-tools."""
    with caplog.at_level(logging.WARNING):
        sys_app = create_server(profile="system", enable_tool_search=True, enable_code_mode=False)
    sys_names = {t.name for t in await sys_app.list_tools()}
    assert sys_names == {"system_get_health_status"}
    assert "search_tools" not in sys_names
    assert "call_tool" not in sys_names
    assert "search" not in sys_names
    assert "execute" not in sys_names
    assert any("Tool Search requested with profile='system'" in r.message for r in caplog.records)

    items_app = create_server(profile="items", enable_tool_search=False)
    items_names = {t.name for t in await items_app.list_tools()}
    assert "items_list_items" in items_names
    assert "system_get_health_status" not in items_names
    assert "search_tools" not in items_names
    assert "call_tool" not in items_names


@pytest.mark.asyncio
async def test_full_tool_search_lists_discovery_meta_only(
    mock_transport: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """full + enable_tool_search=True → tools/list is discovery meta-tools only."""
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))

    search_app = create_server(
        profile="full",
        enable_tool_search=True,
        tool_search_backend="regex",
    )
    tool_names = [t.name for t in await search_app.list_tools()]
    assert tool_names == ["search_tools", "call_tool"]
    assert "system_get_health_status" not in tool_names
    assert "items_list_items" not in tool_names


@pytest.mark.asyncio
async def test_readonly_blocks_mutations_via_search_call_tool(
    mock_transport: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ReadOnlyGateMiddleware still raises SafetyViolationError through call_tool proxy."""
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))
    monkeypatch.setattr(settings, "MCP_READONLY", True)

    # Build full+search without the create_server disable path re-reading a stale flag:
    # create_server also disables mutation tags when MCP_READONLY is set, so nested tools
    # are hidden. Middleware unwrap must still fire on the outer call_tool invocation.
    app = create_server(profile="full", enable_tool_search=True)

    with pytest.raises(SafetyViolationError) as exc_info:
        await app.call_tool(
            "call_tool",
            {"name": "items_create_item", "arguments": {"name": "blocked"}},
        )
    assert "read-only mode" in str(exc_info.value)
    assert "items_create_item" in str(exc_info.value)


@pytest.mark.asyncio
async def test_readonly_gate_unwraps_call_tool_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit: ReadOnlyGateMiddleware resolves nested name inside call_tool arguments."""
    serving = SimpleNamespace(fastmcp=create_server(profile="full", enable_tool_search=True))
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    gate = ReadOnlyGateMiddleware()

    async def allowed_call_next(ctx: MiddlewareContext) -> str:
        return "allowed"

    proxy_ctx = MiddlewareContext(
        method="tools/call",
        message=SimpleNamespace(
            name="call_tool",
            arguments={"name": "items_delete_item", "arguments": {"item_id": "1"}},
        ),
        fastmcp_context=serving,  # type: ignore[arg-type]
    )
    with pytest.raises(SafetyViolationError) as exc_info:
        await gate.on_message(proxy_ctx, allowed_call_next)
    assert "items_delete_item" in str(exc_info.value)

    search_only_ctx = MiddlewareContext(
        method="tools/call",
        message=SimpleNamespace(
            name="call_tool",
            arguments={"name": "system_get_health_status", "arguments": {}},
        ),
        fastmcp_context=serving,  # type: ignore[arg-type]
    )
    assert await gate.on_message(search_only_ctx, allowed_call_next) == "allowed"


def test_tool_search_and_code_mode_are_mutually_exclusive() -> None:
    """Enabling both discovery modes raises ValueError."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_server(profile="full", enable_tool_search=True, enable_code_mode=True)


@pytest.mark.asyncio
async def test_full_code_mode_attaches_when_available(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """full + enable_code_mode attaches experimental meta-tools when CodeMode imports."""
    pytest.importorskip("pydantic_monty", reason=CODE_MODE_SKIP_REASON)

    with caplog.at_level(logging.WARNING):
        app = create_server(profile="full", enable_code_mode=True, enable_tool_search=False)
    names = {t.name for t in await app.list_tools()}
    assert "execute" in names
    assert "search_tools" not in names
    assert "system_get_health_status" not in names

    # Curated profile refuses Code Mode attach
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        curated = create_server(profile="items", enable_code_mode=True)
    curated_names = {t.name for t in await curated.list_tools()}
    assert "execute" not in curated_names
    assert "items_list_items" in curated_names
    assert any("Code Mode requested with profile='items'" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_bm25_tool_search_backend(
    mock_transport: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tool_search_backend='bm25' still collapses tools/list to discovery meta-tools."""
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))

    app = create_server(profile="full", enable_tool_search=True, tool_search_backend="bm25")
    tool_names = [t.name for t in await app.list_tools()]
    assert tool_names == ["search_tools", "call_tool"]

    search_res = await app.call_tool("search_tools", {"query": "health"})
    assert not search_res.is_error
    assert "system_get_health_status" in str(search_res.content)


def test_main_code_mode_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI --enable-code-mode reconfigures the gateway on profile=full."""
    fake_run = MagicMock()
    monkeypatch.setattr(server.mcp, "run", fake_run)
    monkeypatch.setattr(FastMCP, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "taxcode-mcp",
            "--profile",
            "full",
            "--enable-code-mode",
            "--transport",
            "stdio",
        ],
    )
    server.main()
    assert server.mcp is not None


@pytest.mark.asyncio
async def test_code_mode_skips_attach_without_sandbox(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Without pydantic-monty (no fastmcp[code-mode]), Code Mode is skipped with a warning.

    The CodeMode import itself succeeds on a plain install; only the sandbox is missing.
    """
    real_find_spec = server.importlib.util.find_spec

    def fake_find_spec(name: str, *args: object, **kwargs: object) -> object:
        if name == "pydantic_monty":
            return None
        return real_find_spec(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(server.importlib.util, "find_spec", fake_find_spec)
    with caplog.at_level(logging.WARNING):
        app = create_server(profile="full", enable_code_mode=True, enable_tool_search=False)
    names = {t.name for t in await app.list_tools()}
    assert "execute" not in names
    assert "search" not in names
    assert "system_get_health_status" in names
    assert "items_list_items" in names
    assert any(
        "pydantic-monty" in r.message and "mcp-server-taxcode[code-mode]" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_code_mode_execute_runs_real_tool() -> None:
    """execute runs Python in the Code Mode sandbox and reaches a real catalog tool."""
    pytest.importorskip("pydantic_monty", reason=CODE_MODE_SKIP_REASON)
    app = create_server(profile="full", enable_code_mode=True, enable_tool_search=False)
    code = "res = await call_tool('items_get_item', {'item_id': 'abc'})\nreturn res"
    async with Client(app) as client:
        arith = await client.call_tool("execute", {"code": "return 1 + 1"}, raise_on_error=False)
        res = await client.call_tool("execute", {"code": code}, raise_on_error=False)
    assert not arith.is_error, arith.content
    assert [c.text for c in arith.content if isinstance(c, TextContent)] == ["2"]
    assert not res.is_error, res.content
    assert "Item abc" in str(res.content)


@pytest.mark.asyncio
async def test_readonly_gate_call_tool_without_nested_name_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """call_tool without a usable nested name is classified as itself: unannotated → refused."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    gate = ReadOnlyGateMiddleware()
    serving = SimpleNamespace(fastmcp=create_server(profile="full", enable_tool_search=True))

    async def allowed_call_next(ctx: MiddlewareContext) -> str:
        return "allowed"

    for message in (
        SimpleNamespace(name="call_tool", arguments=None),
        SimpleNamespace(name="call_tool", arguments={"name": 123}),
        SimpleNamespace(name="call_tool", arguments={"name": ""}),
    ):
        ctx = MiddlewareContext(
            method="tools/call",
            message=message,
            fastmcp_context=serving,  # type: ignore[arg-type]
        )
        with pytest.raises(SafetyViolationError, match="'call_tool' blocked"):
            await gate.on_message(ctx, allowed_call_next)


@pytest.mark.asyncio
async def test_readonly_gate_without_server_context_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no serving FastMCP context the annotation cannot be read, so the call is refused."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    gate = ReadOnlyGateMiddleware()

    async def allowed_call_next(ctx: MiddlewareContext) -> str:
        return "allowed"

    ctx = MiddlewareContext(
        method="tools/call",
        message=SimpleNamespace(name="system_get_health_status", arguments={}),
    )
    with pytest.raises(SafetyViolationError, match="system_get_health_status"):
        await gate.on_message(ctx, allowed_call_next)
