"""Tests for FastMCP server tools, resources, prompts, MRTR elicitation, and safety gates."""

import logging
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from taxcode_mcp import server
from taxcode_mcp.config import settings
from taxcode_mcp.db import db
from taxcode_mcp.errors import SafetyViolationError


@pytest.mark.asyncio
async def test_server_tools(mock_transport, monkeypatch):
    """Verify server tool execution, input validation, and safety gates."""
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))

    # 1. Health
    health = await server.get_health_status()
    assert health["status"] == "success"
    assert health["database"]["backend"] == "DuckDB"

    # 2. Lookup statute
    statute = await server.lookup_statute("26 U.S.C. § 933")
    assert statute["status"] == "success"
    assert statute["data"]["section"] == "933"

    # 3. Search tax code
    search_res = await server.search_tax_code("bona fide resident")
    assert search_res["status"] == "success"
    assert len(search_res["results"]) > 0

    # 4. List publications
    pubs = await server.list_publications()
    assert pubs["status"] == "success"
    assert len(pubs["publications"]) > 0

    # 5. Evaluate bona fide residency
    residency = server.evaluate_pr_bona_fide_residency(
        days_present_pr=185,
        days_present_us=30,
        has_us_tax_home=False,
        closer_connection_jurisdiction="PR",
    )
    assert residency["status"] == "success"
    assert residency["is_bona_fide_resident"] is True

    # 6. Calculate built-in gain sourcing
    big = server.calculate_built_in_gain_sourcing(
        is_marketable=True,
        total_gain=700000.0,
        fmv_on_residency_date=500000.0,
        adjusted_basis=100000.0,
        years_since_residency=4.0,
    )
    assert big["status"] == "success"
    assert big["us_source_gain"] == 400000.0
    assert big["pr_source_gain"] == 300000.0


@pytest.mark.asyncio
async def test_server_read_only_mode(mock_transport, monkeypatch):
    """Verify read-only mode restricts write operations."""
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))
    monkeypatch.setattr(settings, "MCP_READONLY", True)

    # In pure read-only mode, read tools succeed under MCP_READONLY=True
    health = await server.get_health_status()
    assert health["status"] == "success"
    statute = await server.lookup_statute("933")
    assert statute["status"] == "success"


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
        lambda: server.lookup_statute("933"),
        lambda: server.search_tax_code("residency"),
        lambda: server.list_publications(),
    ],
    ids=[
        "get_health_status",
        "lookup_statute",
        "search_tax_code",
        "list_publications",
    ],
)
async def test_tool_error_original_is_not_reachable_via_context(
    monkeypatch: pytest.MonkeyPatch, call: Any
) -> None:
    """A raw RuntimeError with a bearer token is not on the ToolError's __context__ chain."""
    raw_token = "sk-live-context-abc123"

    async def request(method: str, path: str, **_: Any) -> Any:
        raise RuntimeError(f"upstream rejected Authorization: Bearer {raw_token}")

    def fake_db_err(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"storage rejected Authorization: Bearer {raw_token}")

    monkeypatch.setattr(server.client, "request", request)
    monkeypatch.setattr(db, "lookup_statute", fake_db_err)
    monkeypatch.setattr(db, "search_statutes", fake_db_err)
    monkeypatch.setattr(db, "list_publications", fake_db_err)
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

    def fake_db_err(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Bearer secret-token-abc failure")

    monkeypatch.setattr(db, "lookup_statute", fake_db_err)
    monkeypatch.setattr(db, "search_statutes", fake_db_err)
    monkeypatch.setattr(db, "list_publications", fake_db_err)

    with pytest.raises(ToolError, match=r"Bearer \[REDACTED\]"):
        await server.lookup_statute("933")

    with pytest.raises(ToolError, match=r"Bearer \[REDACTED\]"):
        await server.search_tax_code("test")

    with pytest.raises(ToolError, match=r"Bearer \[REDACTED\]"):
        await server.list_publications()


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
        result = await mcp_client.call_tool("system_get_health_status", {}, raise_on_error=False)

    assert result.is_error
    text = result.content[0].text
    assert "Bearer [REDACTED]" in text
    assert "secret-token-abc" not in text
    logged = "\n".join(
        record.getMessage()
        + (logging.Formatter().formatException(record.exc_info) if record.exc_info else "")
        for record in caplog.records
    )
    assert "Bearer [REDACTED]" in logged
    assert "secret-token-abc" not in logged


@pytest.mark.asyncio
async def test_server_resources_and_prompts():
    """Verify dynamic resources and prompts metadata and rendering."""
    cap = server.get_capabilities()
    assert "Tax Code MCP Server capabilities" in cap
    assert "Act 60-2019" in cap

    res = await server.get_statute_resource("US", "933")
    assert "933" in res

    p = server.cross_border_tax_analysis(taxpayer_scenario="Scenario 456")
    assert "Scenario 456" in p


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


@pytest.mark.asyncio
async def test_compliance_and_statutes_coverage_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise remaining branching lines in modules for 100% coverage."""
    # 1. Negative day count in evaluate_pr_bona_fide_residency
    with pytest.raises(SafetyViolationError, match="cannot be negative"):
        server.evaluate_pr_bona_fide_residency(days_present_pr=-5, days_present_us=10)

    # 2. Non-bona-fide residency warning in evaluate_pr_bona_fide_residency
    non_bf = server.evaluate_pr_bona_fide_residency(
        days_present_pr=100,
        days_present_us=120,
        has_us_tax_home=True,
        closer_connection_jurisdiction="US",
    )
    assert non_bf["is_bona_fide_resident"] is False
    assert any("Caution: Worldwide income" in r for r in non_bf["reporting_requirements"])

    # 3. Negative total gain in calculate_built_in_gain_sourcing
    with pytest.raises(ToolError):
        server.calculate_built_in_gain_sourcing(is_marketable=True, total_gain=-1000.0)

    # 4. 10-year expired lookback in calculate_built_in_gain_sourcing
    expired = server.calculate_built_in_gain_sourcing(
        is_marketable=True,
        total_gain=50000.0,
        years_since_residency=11.5,
    )
    assert expired["lookback_expired"] is True
    assert expired["us_source_gain"] == 0.0

    # 5. Marketable missing fmv or basis
    with pytest.raises(ToolError):
        server.calculate_built_in_gain_sourcing(
            is_marketable=True,
            total_gain=50000.0,
            fmv_on_residency_date=None,
            adjusted_basis=None,
        )

    # 6. Marketable with fmv < basis (loss/no gain pre-move)
    loss_pre_move = server.calculate_built_in_gain_sourcing(
        is_marketable=True,
        total_gain=50000.0,
        fmv_on_residency_date=10000.0,
        adjusted_basis=20000.0,
        years_since_residency=2.0,
    )
    assert loss_pre_move["us_source_gain"] == 0.0
    assert loss_pre_move["pr_source_gain"] == 50000.0

    # 7. Non-marketable missing days or invalid days
    with pytest.raises(ToolError):
        server.calculate_built_in_gain_sourcing(
            is_marketable=False,
            total_gain=50000.0,
            days_held_as_resident=None,
            total_days_held=0,
        )

    # 8. Non-marketable valid linear apportionment
    non_marketable = server.calculate_built_in_gain_sourcing(
        is_marketable=False,
        total_gain=100000.0,
        days_held_as_resident=365,
        total_days_held=730,
        years_since_residency=1.0,
    )
    assert non_marketable["status"] == "success"
    assert non_marketable["us_source_gain"] == 50000.0
    assert non_marketable["pr_source_gain"] == 50000.0

    # 9. lookup_statute not found
    nf = await server.lookup_statute("NONEXISTENT_STATUTE_9999")
    assert nf["status"] == "not_found"

    # 10. get_statute_resource not found
    res_nf = await server.get_statute_resource("US", "NONEXISTENT_SEC")
    assert "not found" in res_nf

    # 11. File-backed TaxDatabase close path in get_stats
    import tempfile
    from pathlib import Path

    from taxcode_mcp.db import TaxDatabase

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_db_path = Path(tmp_dir) / "test_tax.duckdb"
        file_db = TaxDatabase(db_path=file_db_path)
        s_cnt, p_cnt = file_db.get_stats()
        assert s_cnt >= 0
        assert p_cnt >= 0
