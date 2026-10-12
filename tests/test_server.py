"""Tests for FastMCP server tools, resources, prompts, MRTR elicitation, and safety gates."""

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from taxcode_mcp import server
from taxcode_mcp.config import settings
from taxcode_mcp.errors import SafetyViolationError


@pytest.mark.asyncio
async def test_server_tools(mock_transport, monkeypatch):
    """Verify server tool execution, input validation, and safety gates."""
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))

    # 1. Health
    health = await server.get_health_status()
    assert health["status"] == "success"
    assert health["data"]["status"] == "healthy"

    # 2. List items
    items = await server.list_items(limit=10, offset=0)
    assert items["status"] == "success"

    # 3. Get item
    item = await server.get_item(item_id="123")
    assert item["status"] == "success"
    assert item["data"]["id"] == "123"

    # 4. Create item
    created = await server.create_item(name="New Item", payload={"description": "Test"})
    assert created["status"] == "success"
    assert created["data"]["created"] is True

    # 5. Delete item (confirm=False should raise SafetyViolationError)
    with pytest.raises(SafetyViolationError):
        await server.delete_item(item_id="123", confirm=False)

    # 6. Delete item (confirm=True)
    deleted = await server.delete_item(item_id="123", confirm=True)
    assert deleted["status"] == "success"
    assert deleted["deleted"] is True

    # 7. Bulk delete (default dry_run=True, but gated by settings)
    with pytest.raises(SafetyViolationError) as exc_info:
        await server.bulk_delete_items(item_ids=["1", "2"], dry_run=True)
    assert "TAXCODE_MCP_ALLOW_BULK_DESTRUCTIVE=1" in str(exc_info.value)

    # Enable bulk destructive
    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", True)
    dry_run_res = await server.bulk_delete_items(item_ids=["1", "2"], dry_run=True)
    assert dry_run_res["status"] == "dry_run"

    # Bulk delete live without confirm
    with pytest.raises(SafetyViolationError):
        await server.bulk_delete_items(item_ids=["1", "2"], dry_run=False, confirm=False)

    # Bulk delete live with confirm
    bulk_res = await server.bulk_delete_items(item_ids=["1", "2"], dry_run=False, confirm=True)
    assert bulk_res["status"] == "success"
    assert bulk_res["deleted_count"] == 2


@pytest.mark.asyncio
async def test_server_read_only_mode(mock_transport, monkeypatch):
    """Verify read-only mode restricts write operations."""
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))
    monkeypatch.setattr(settings, "MCP_READONLY", True)

    with pytest.raises(SafetyViolationError, match="read-only"):
        await server.create_item(name="Forbidden")

    with pytest.raises(SafetyViolationError, match="read-only"):
        await server.delete_item(item_id="1", confirm=True)

    with pytest.raises(SafetyViolationError, match="read-only"):
        await server.bulk_delete_items(item_ids=["1"], dry_run=True)


def _exception_chain(exc: BaseException) -> list[BaseException]:
    """Every exception reachable from ``exc`` through ``__cause__`` and ``__context__``."""
    seen: list[BaseException] = []
    pending: list[BaseException | None] = [exc]
    while pending:
        current = pending.pop()
        if current is None or any(current is s for s in seen):
            continue
        seen.append(current)
        pending.extend([current.__cause__, current.__context__])
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda: server.get_health_status(),
        lambda: server.list_items(),
        lambda: server.get_item("1"),
        lambda: server.create_item("Test"),
        lambda: server.delete_item("1", confirm=True),
        lambda: server.bulk_delete_items(["1"], dry_run=False, confirm=True),
    ],
    ids=[
        "get_health_status",
        "list_items",
        "get_item",
        "create_item",
        "delete_item",
        "bulk_delete_items_batch_failed",
    ],
)
async def test_tool_error_original_is_not_reachable_via_context(
    monkeypatch: pytest.MonkeyPatch, call: Any
) -> None:
    """A raw RuntimeError with a bearer token is not on the ToolError's __context__ chain."""
    raw_token = "sk-live-context-abc123"

    async def request(method: str, path: str, **_: Any) -> Any:
        raise RuntimeError(f"upstream rejected Authorization: Bearer {raw_token}")

    monkeypatch.setattr(server.client, "request", request)
    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", True)
    with pytest.raises(ToolError) as exc_info:
        await call()
    err = exc_info.value
    assert "Bearer [REDACTED]" in str(err)
    assert err.__cause__ is None
    assert err.__context__ is None
    chain = _exception_chain(err)
    assert not any(isinstance(e, RuntimeError) for e in chain)
    assert not any(raw_token in str(e) for e in chain)


@pytest.mark.asyncio
async def test_server_error_handling(monkeypatch):
    """Verify secret redaction and error formatting across tool handlers."""

    def error_transport(request: httpx.Request) -> httpx.Response:
        """Mock transport raising request error with credentials."""
        raise httpx.RequestError("Bearer secret-token-abc failure")

    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(error_transport), base_url="https://api.example.com"
    )
    monkeypatch.setattr(
        server, "client", server.TemplateClient(http_client=async_client, max_retries=0)
    )

    with pytest.raises(ToolError) as health_exc:
        await server.get_health_status()
    assert "Bearer [REDACTED]" in str(health_exc.value)
    assert "secret-token-abc" not in str(health_exc.value)
    assert health_exc.value.__cause__ is None
    assert health_exc.value.__context__ is None
    assert health_exc.value.__suppress_context__

    with pytest.raises(ToolError, match=r"Bearer \[REDACTED\]"):
        await server.list_items()

    with pytest.raises(ToolError, match=r"Bearer \[REDACTED\]"):
        await server.get_item("1")

    with pytest.raises(ToolError, match=r"Bearer \[REDACTED\]"):
        await server.create_item("Test")

    with pytest.raises(ToolError, match=r"Bearer \[REDACTED\]"):
        await server.delete_item("1", confirm=True)

    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", True)
    with pytest.raises(ToolError, match=r"Bearer \[REDACTED\]"):
        await server.bulk_delete_items(["1"], dry_run=False, confirm=True)


@pytest.mark.asyncio
async def test_tool_errors_reach_client_as_is_error(monkeypatch, caplog):
    """An upstream failure reaches the client as isError: true, redacted on the wire and in logs."""

    def error_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.RequestError("Bearer secret-token-abc failure")

    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(error_transport), base_url="https://api.example.com"
    )
    monkeypatch.setattr(
        server, "client", server.TemplateClient(http_client=async_client, max_retries=0)
    )

    caplog.set_level(logging.DEBUG)
    async with Client(server.create_server()) as mcp_client:
        result = await mcp_client.call_tool(
            "items_get_item", {"item_id": "1"}, raise_on_error=False
        )
        refusal = await mcp_client.call_tool(
            "items_delete_item", {"item_id": "1"}, raise_on_error=False
        )

    assert result.is_error
    text = result.content[0].text
    assert "Bearer [REDACTED]" in text
    assert "secret-token-abc" not in text
    assert refusal.is_error
    assert "requires explicit confirm=True" in refusal.content[0].text
    logged = "\n".join(
        record.getMessage()
        + (logging.Formatter().formatException(record.exc_info) if record.exc_info else "")
        for record in caplog.records
    )
    assert "Bearer [REDACTED]" in logged
    assert "secret-token-abc" not in logged


@pytest.mark.asyncio
async def test_mrtr_elicitation(mock_transport, monkeypatch):
    """Verify MRTR interactive client confirmation and fallback paths."""
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))

    # 1. delete_item: MRTR accepted
    ctx_accept = MagicMock()
    ctx_accept.elicit = AsyncMock(
        return_value=SimpleNamespace(action="accept", data=SimpleNamespace(confirm=True))
    )
    res_accept = await server.delete_item("123", confirm=False, ctx=ctx_accept)
    assert res_accept["status"] == "success"
    assert res_accept["deleted"] is True

    # 2. delete_item: MRTR declined
    ctx_decline = MagicMock()
    ctx_decline.elicit = AsyncMock(return_value=SimpleNamespace(action="decline", data=None))
    with pytest.raises(SafetyViolationError):
        await server.delete_item("123", confirm=False, ctx=ctx_decline)

    # 3. delete_item: MRTR error fallback
    ctx_err = MagicMock()
    ctx_err.elicit = AsyncMock(side_effect=RuntimeError("Elicitation unsupported"))
    with pytest.raises(SafetyViolationError):
        await server.delete_item("123", confirm=False, ctx=ctx_err)

    # 4. bulk_delete_items: MRTR accepted
    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", True)
    bulk_accept = await server.bulk_delete_items(
        ["1", "2"], dry_run=False, confirm=False, ctx=ctx_accept
    )
    assert bulk_accept["status"] == "success"
    assert bulk_accept["deleted_count"] == 2

    # 5. bulk_delete_items: MRTR declined
    with pytest.raises(SafetyViolationError):
        await server.bulk_delete_items(["1", "2"], dry_run=False, confirm=False, ctx=ctx_decline)

    # 6. bulk_delete_items: MRTR error fallback
    with pytest.raises(SafetyViolationError):
        await server.bulk_delete_items(["1", "2"], dry_run=False, confirm=False, ctx=ctx_err)


@pytest.mark.asyncio
async def test_input_responses_confirmation(mock_transport, monkeypatch):
    """Verify modern FastMCP 4 / SEP-2322 input_responses confirmation channel."""
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))

    # 1. Accepted via dict content
    ctx_dict = SimpleNamespace(
        input_responses={"confirm": SimpleNamespace(action="accept", content={"confirm": True})},
        elicit=AsyncMock(),
    )
    res_dict = await server.delete_item("123", confirm=False, ctx=ctx_dict)
    assert res_dict["status"] == "success"
    ctx_dict.elicit.assert_not_called()

    # 2. Accepted via object content
    ctx_obj = SimpleNamespace(
        input_responses={
            "confirm": SimpleNamespace(action="accept", content=SimpleNamespace(confirm=True))
        },
        elicit=AsyncMock(),
    )
    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", True)
    res_obj = await server.bulk_delete_items(["1", "2"], dry_run=False, confirm=False, ctx=ctx_obj)
    assert res_obj["status"] == "success"
    assert res_obj["deleted_count"] == 2
    ctx_obj.elicit.assert_not_called()

    # 3. None context returns False
    assert await server._verify_confirmation(None, "msg") is False

    # 4. Context elicit error returns False
    ctx_err = SimpleNamespace(
        input_responses=None,
        elicit=AsyncMock(side_effect=RuntimeError("Modern protocol lacks back-channel")),
    )
    assert await server._verify_confirmation(ctx_err, "msg") is False


def test_server_resources_and_prompts():
    """Verify dynamic resources and prompts metadata and rendering."""
    cap = server.get_capabilities()
    assert "Tax Code MCP Server capabilities" in cap
    assert "MRTR Elicitations" in cap

    p = server.analyze_item_prompt(item_id="item-456")
    assert "item 'item-456'" in p


def test_cache_hints():
    """Verify RFC 9111 catalog cache hints configuration."""
    expected_keys = {
        "tools/list",
        "prompts/list",
        "resources/list",
        "resources/templates/list",
        "server/discover",
    }
    assert set(server.CACHE_HINTS.keys()) == expected_keys
    for _, hint in server.CACHE_HINTS.items():
        assert hint.ttl_ms == 3600000
        assert hint.scope == "public"


def test_server_main_transports(monkeypatch):
    """Verify CLI transport selection, flag parsing, and deprecation warnings."""
    run_args = {}

    def fake_run(**kwargs):
        nonlocal run_args
        run_args = kwargs

    monkeypatch.setattr(server.mcp, "run", fake_run)

    # stdio
    monkeypatch.setattr("sys.argv", ["taxcode-mcp", "--transport", "stdio"])
    server.main()
    assert run_args.get("transport") == "stdio"

    # streamable-http (default stateful)
    monkeypatch.setattr(
        "sys.argv",
        ["taxcode-mcp", "--transport", "streamable-http", "--port", "9000"],
    )
    server.main()
    assert run_args.get("transport") == "streamable-http"
    assert run_args.get("host") == "127.0.0.1"
    assert run_args.get("port") == 9000
    assert run_args.get("stateless_http") is False
    assert run_args.get("json_response") is False
    assert run_args.get("host_origin_protection") is True

    # streamable-http (explicit stateless + json_response)
    monkeypatch.setattr(
        "sys.argv",
        [
            "taxcode-mcp",
            "--transport",
            "streamable-http",
            "--port",
            "9000",
            "--stateless",
            "--json-response",
        ],
    )
    server.main()
    assert run_args.get("transport") == "streamable-http"
    assert run_args.get("stateless_http") is True
    assert run_args.get("json_response") is True

    # stdio with stateless and json_response flags triggers warnings
    monkeypatch.setattr(
        "sys.argv",
        ["taxcode-mcp", "--transport", "stdio", "--stateless", "--json-response"],
    )
    server.main()
    assert run_args.get("transport") == "stdio"

    # sse
    monkeypatch.setattr("sys.argv", ["taxcode-mcp", "--transport", "sse", "--port", "9001"])
    server.main()
    assert run_args.get("transport") == "sse"
    assert run_args.get("host") == "127.0.0.1"
    assert run_args.get("port") == 9001

    # streamable-http with allowed-host and allowed-origin
    monkeypatch.setattr(
        "sys.argv",
        [
            "taxcode-mcp",
            "--transport",
            "streamable-http",
            "--allowed-host",
            "custom.local",
            "--allowed-origin",
            "https://app.example.com",
        ],
    )
    server.main()
    assert run_args.get("allowed_hosts") == ["custom.local"]
    assert run_args.get("allowed_origins") == ["https://app.example.com"]

    # sse with allowed-host and allowed-origin
    monkeypatch.setattr(
        "sys.argv",
        [
            "taxcode-mcp",
            "--transport",
            "sse",
            "--allowed-host",
            "custom.local",
            "--allowed-origin",
            "https://app.example.com",
        ],
    )
    server.main()
    assert run_args.get("allowed_hosts") == ["custom.local"]
    assert run_args.get("allowed_origins") == ["https://app.example.com"]

    # wildcard bind on streamable-http without --allowed-host fails closed with parser.error,
    # even once the bind policy is satisfied (opt-in here; a token behaves the same)
    monkeypatch.setenv("TAXCODE_MCP_ALLOW_UNAUTHENTICATED_BIND", "1")
    monkeypatch.setattr(
        "sys.argv",
        ["taxcode-mcp", "--transport", "streamable-http", "--host", "0.0.0.0"],
    )
    with pytest.raises(SystemExit):
        server.main()

    # wildcard "*" in --allowed-host fails closed with parser.error
    monkeypatch.setattr(
        "sys.argv",
        ["taxcode-mcp", "--transport", "streamable-http", "--allowed-host", "*"],
    )
    with pytest.raises(SystemExit):
        server.main()


def test_streamable_http_app_allowed_hosts_dynamic_port(monkeypatch):
    """Verify _streamable_http_app uses dynamic port binding for default allowed_hosts."""
    captured_kwargs = {}

    def fake_http_app(**kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(server.mcp, "http_app", fake_http_app)
    server.mcp.streamable_http_app(host="0.0.0.0", port=9999)
    assert captured_kwargs["allowed_hosts"] == [
        "0.0.0.0",
        "localhost",
        "0.0.0.0:9999",
        "localhost:9999",
    ]

    captured_kwargs.clear()
    server.mcp.streamable_http_app(allowed_hosts=["explicit.domain"])
    assert captured_kwargs["allowed_hosts"] == ["explicit.domain"]


def test_handle_shutdown():
    """Verify graceful process termination on shutdown signals."""
    with pytest.raises(SystemExit) as exc_info:
        server._handle_shutdown(15, None)
    assert exc_info.value.code == 0


@pytest.mark.asyncio
async def test_server_streamable_http_dispatch(mock_transport, monkeypatch):
    """Verify Streamable HTTP ASGI app dispatches tool calls with MockTransport backend."""
    async with httpx.AsyncClient(
        transport=mock_transport, base_url="https://api.example.com"
    ) as async_client:
        monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))
        app = server.mcp.streamable_http_app(stateless_http=True, json_response=True)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
            ) as http_c:
                meta = {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
                call_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "get_health_status",
                        "arguments": {},
                        "_meta": meta,
                    },
                }
                res = await http_c.post(
                    "/mcp",
                    json=call_payload,
                    headers={
                        "Content-Type": "application/json",
                        "MCP-Protocol-Version": "2026-07-28",
                        "Mcp-Method": "tools/call",
                        "Mcp-Name": "get_health_status",
                    },
                )
                assert res.status_code == 200
                data = res.json()
                assert "result" in data
                assert "content" in data["result"]


@pytest.mark.asyncio
async def test_tool_error_chain_and_logs_hold_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """A chained upstream failure reaches the client and the logs only in redacted form.

    Python keeps a caught exception on ``__context__`` unless the new one is raised outside
    the ``except`` block, and ``from None`` sets ``__suppress_context__``
    (https://docs.python.org/3/library/exceptions.html#exception-context).
    """
    import io
    import traceback

    inner, outer = "hunter2-inner-secret", "sk-outer-secret-12345"

    async def request(method: str, path: str, **_: Any) -> Any:
        try:
            raise RuntimeError(f"pool failed password={inner}")
        except RuntimeError as exc:
            raise ValueError(f"upstream rejected Authorization: Bearer {outer}") from exc

    monkeypatch.setattr(server.client, "request", request)

    with pytest.raises(ToolError) as exc_info:
        await server.get_health_status()
    err = exc_info.value
    assert err.__cause__ is None
    assert err.__context__ is None
    assert err.__suppress_context__
    formatted = "".join(traceback.format_exception(err))
    assert inner not in formatted
    assert outer not in formatted
    assert "Bearer [REDACTED]" in formatted

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    loggers = [logging.getLogger(), logging.getLogger("fastmcp"), logging.getLogger("mcp")]
    for log in loggers:
        log.addHandler(handler)
    try:
        async with Client(server.create_server()) as client:
            result = await client.call_tool("system_get_health_status", {}, raise_on_error=False)
    finally:
        for log in loggers:
            log.removeHandler(handler)
    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    assert text == "upstream rejected Authorization: Bearer [REDACTED]"
    logged = buf.getvalue()
    assert "Bearer [REDACTED]" in logged
    assert inner not in logged
    assert outer not in logged
