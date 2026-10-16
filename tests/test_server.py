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

    # 2b. Lookup statute with tax_year (historical audit point-in-time)
    statute_ga_2024 = await server.lookup_statute("O.C.G.A. § 48-7-20", tax_year=2024)
    assert statute_ga_2024["status"] == "success"
    assert statute_ga_2024["data"]["effective_year"] == 2024
    assert statute_ga_2024["data"]["superseded_year"] == 2025

    # 2b2. Lookup Louisiana 2024 vs 2025 temporal transition
    stat_la_2024 = await server.lookup_statute("La. Rev. Stat. Ann. § 47:32", tax_year=2024)
    assert stat_la_2024["status"] == "success"
    assert stat_la_2024["data"]["superseded_year"] == 2025

    stat_la_2025 = await server.lookup_statute("La. Rev. Stat. Ann. § 47:32", tax_year=2025)
    assert stat_la_2025["status"] == "success"
    assert stat_la_2025["data"]["effective_year"] == 2025
    assert "Act 11" in stat_la_2025["data"]["title"]

    # 2c. Lookup statute not found with tax_year and jurisdiction
    statute_miss = await server.lookup_statute("Nonexistent 9999", jurisdiction="GA", tax_year=2024)
    assert statute_miss["status"] == "not_found"
    assert statute_miss["tax_year"] == 2024
    assert statute_miss["jurisdiction"] == "GA"

    # 3. Search tax code
    search_res = await server.search_tax_code("bona fide resident")
    assert search_res["status"] == "success"
    assert len(search_res["results"]) > 0

    # 3b. Search tax code with tax_year and jurisdiction
    search_ga = await server.search_tax_code(
        "Individual Income Tax Rates", jurisdiction="GA", tax_year=2024
    )
    assert search_ga["status"] == "success"
    assert search_ga["tax_year"] == 2024
    assert search_ga["jurisdiction"] == "GA"
    assert len(search_ga["results"]) > 0

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
    assert "NOT TAX OR LEGAL ADVICE" in residency["disclaimer"]["en"]
    assert "NO CONSTITUYE ASESORAMIENTO CONTRIBUTIVO NI LEGAL" in residency["disclaimer"]["es"]
    assert "unofficial statutory reference summaries" in residency["content_notice"]

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
    assert "NOT TAX OR LEGAL ADVICE" in big["disclaimer"]["en"]

    # 7. Evaluate Georgia residency & tax
    ga_res = server.evaluate_georgia_residency(
        days_present_ga=200,
        is_domiciled_in_ga=True,
        federal_agi=100000.0,
        filing_status="single",
        age=66,
        retirement_income=40000.0,
        tax_year=2024,
        ga_homestead_exemption_claimed=True,
        ga_driver_license_active=True,
        ga_voter_registration_active=True,
    )
    assert ga_res["status"] == "success"
    assert ga_res["is_georgia_resident"] is True
    assert ga_res["residency_tests"]["183_day_presence_test_satisfied"] is True
    assert ga_res["domicile_audit_risk"]["risk_level"] == "high"
    assert len(ga_res["domicile_audit_risk"]["flags"]) == 3
    assert ga_res["tax_computation"]["georgia_taxable_net_income"] == 48000.0
    assert ga_res["tax_computation"]["estimated_georgia_tax"] == 2635.20
    assert "NOT TAX OR LEGAL ADVICE" in ga_res["disclaimer"]["en"]


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
    assert "Superseded Year / Año de sustitución: current / en vigor" in res
    assert "Vigencia: Vigente desde" in res

    old = await server.get_statute_resource("GA", "48-7-20")
    assert "Superseded Year" in old

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

    # 12. Unconfigured offline health mode
    orig_custom = server.client._custom_client
    server.client._custom_client = None
    try:
        offline_health = await server.get_health_status()
        assert offline_health["status"] == "success"
        assert offline_health["upstream"]["mode"] == "duckdb_offline"
    finally:
        server.client._custom_client = orig_custom


def test_evaluate_georgia_residency_cases() -> None:
    """Test edge cases, exemptions, rate reductions, and safety gates for Georgia tax tool."""
    # 1. Nonresident
    non_res = server.evaluate_georgia_residency(
        days_present_ga=0,
        is_domiciled_in_ga=False,
        maintains_permanent_abode_ga=False,
        federal_agi=50000.0,
    )
    assert non_res["is_georgia_resident"] is False
    assert non_res["tax_computation"]["estimated_georgia_tax"] == 0.0

    # 2. Married filing jointly & age 62-64 retirement exclusion
    mfj_res = server.evaluate_georgia_residency(
        days_present_ga=190,
        is_domiciled_in_ga=False,
        maintains_permanent_abode_ga=True,
        federal_agi=100000.0,
        filing_status="married_joint",
        age=63,
        retirement_income=50000.0,
        tax_year=2025,
    )
    assert mfj_res["is_georgia_resident"] is True
    assert mfj_res["tax_computation"]["standard_deduction"] == 24000.0
    assert mfj_res["tax_computation"]["retirement_exclusion_applied"] == 35000.0
    assert mfj_res["tax_computation"]["applicable_rate"] == 0.0539
    # Taxable: 100k - 24k - 35k = 41k
    assert mfj_res["tax_computation"]["georgia_taxable_net_income"] == 41000.0
    assert mfj_res["tax_computation"]["estimated_georgia_tax"] == round(41000.0 * 0.0539, 2)

    # 3. Tax year 2026+ flat rate
    ga_2026 = server.evaluate_georgia_residency(
        days_present_ga=100,
        is_domiciled_in_ga=True,
        federal_agi=50000.0,
        filing_status="single",
        age=40,
        tax_year=2026,
    )
    assert ga_2026["tax_computation"]["applicable_rate"] == 0.0499

    # 4. Safety violations
    with pytest.raises(SafetyViolationError, match="Day count must be between 0 and 366"):
        server.evaluate_georgia_residency(days_present_ga=-5)

    with pytest.raises(SafetyViolationError, match="Day count must be between 0 and 366"):
        server.evaluate_georgia_residency(days_present_ga=400)

    with pytest.raises(SafetyViolationError, match="Federal AGI cannot be negative"):
        server.evaluate_georgia_residency(days_present_ga=100, federal_agi=-1000.0)

    with pytest.raises(SafetyViolationError, match="Retirement income cannot be negative"):
        server.evaluate_georgia_residency(days_present_ga=100, retirement_income=-500.0)


def test_evaluate_state_residency_cases() -> None:
    """Test multi-state residency determinations, rates, brackets, and audit gates."""
    # 1. Unsupported jurisdiction
    with pytest.raises(SafetyViolationError, match="Unsupported jurisdiction 'ZZ'"):
        server.evaluate_state_residency(jurisdiction="ZZ", days_in_state=100)

    # 2. Validation error handling
    with pytest.raises(SafetyViolationError, match="Day count must be between 0 and 366"):
        server.evaluate_state_residency(jurisdiction="CA", days_in_state=-1)
    with pytest.raises(SafetyViolationError, match="Day count must be between 0 and 366"):
        server.evaluate_state_residency(jurisdiction="CA", days_in_state=400)
    with pytest.raises(SafetyViolationError, match="Federal AGI cannot be negative"):
        server.evaluate_state_residency(jurisdiction="CA", days_in_state=100, federal_agi=-1.0)
    with pytest.raises(SafetyViolationError, match="Retirement income cannot be negative"):
        server.evaluate_state_residency(
            jurisdiction="GA", days_in_state=100, retirement_income=-1.0
        )
    with pytest.raises(SafetyViolationError, match="Capital gains cannot be negative"):
        server.evaluate_state_residency(jurisdiction="WA", days_in_state=100, capital_gains=-1.0)
    with pytest.raises(SafetyViolationError, match="Telecommuting day count cannot be negative"):
        server.evaluate_state_residency(
            jurisdiction="NY", days_in_state=50, telecommuting_days_out_of_state=-5
        )

    # 3. Domicile audit risk flags
    flagged = server.evaluate_state_residency(
        jurisdiction="FL",
        days_in_state=200,
        is_domiciled=True,
        has_active_homestead_exemption=True,
        has_active_driver_license=True,
        has_active_voter_registration=True,
    )
    assert flagged["domicile_audit_risk"]["risk_level"] == "high"
    assert len(flagged["domicile_audit_risk"]["flags"]) == 3
    assert flagged["tax_computation"]["estimated_state_tax"] == 0.0
    assert "NOT TAX OR LEGAL ADVICE" in flagged["disclaimer"]["en"]
    assert "unofficial statutory reference summaries" in flagged["content_notice"]

    # 4. Georgia (GA)
    ga_sr = server.evaluate_state_residency(
        jurisdiction="ga",
        days_in_state=190,
        federal_agi=150000.0,
        filing_status="married_joint",
        age=66,
        retirement_income=80000.0,
        tax_year=2024,
    )
    assert ga_sr["is_resident"] is True
    # 150k - 24k - 65k = 61k * 0.0549
    assert ga_sr["tax_computation"]["taxable_net_income"] == 61000.0
    assert ga_sr["tax_computation"]["estimated_state_tax"] == round(61000.0 * 0.0549, 2)

    # 5. New York (NY) - non-resident convenience rule & progressive brackets
    ny_nonres = server.evaluate_state_residency(
        jurisdiction="NY",
        days_in_state=40,
        is_domiciled=False,
        telecommuting_days_out_of_state=120,
    )
    assert ny_nonres["is_resident"] is False
    ny_flags = ny_nonres["domicile_audit_risk"]["flags"]
    assert any("Convenience of the Employer" in f for f in ny_flags)
    assert ny_nonres["tax_computation"]["estimated_state_tax"] == 0.0

    for inc, expected_pct in [
        (6000000.0, 0.109),
        (2000000.0, 0.0965),
        (500000.0, 0.0685),
        (150000.0, 0.060),
        (50000.0, 0.055),
    ]:
        ny_res = server.evaluate_state_residency(
            jurisdiction="NY",
            days_in_state=200,
            maintains_permanent_abode=True,
            federal_agi=inc,
            filing_status="single",
        )
        assert ny_res["is_resident"] is True
        taxable = inc - 8000.0
        assert ny_res["tax_computation"]["estimated_state_tax"] == round(taxable * expected_pct, 2)

    # 6. California (CA) - 9 month presumption & progressive brackets
    for inc, expected_pct in [
        (2000000.0, 0.133),
        (500000.0, 0.113),
        (100000.0, 0.093),
        (40000.0, 0.060),
    ]:
        ca_res = server.evaluate_state_residency(
            jurisdiction="CA",
            days_in_state=280,
            federal_agi=inc,
            filing_status="single",
        )
        assert ca_res["is_resident"] is True
        assert any("RTC § 17016" in f for f in ca_res["domicile_audit_risk"]["flags"])
        taxable = inc - 5363.0
        assert ca_res["tax_computation"]["estimated_state_tax"] == round(taxable * expected_pct, 2)

    # 7. Texas (TX)
    tx_res = server.evaluate_state_residency(
        jurisdiction="TX",
        days_in_state=200,
        is_domiciled=True,
        federal_agi=250000.0,
    )
    assert tx_res["is_resident"] is True
    assert tx_res["tax_computation"]["estimated_state_tax"] == 0.0

    # 8. Washington (WA) - Capital gains excise tax
    wa_res = server.evaluate_state_residency(
        jurisdiction="WA",
        days_in_state=200,
        is_domiciled=True,
        capital_gains=500000.0,
    )
    assert wa_res["is_resident"] is True
    # Taxable: 500k - 262k = 238k * 0.07 = 16,660.0
    assert wa_res["tax_computation"]["estimated_state_tax"] == round(238000.0 * 0.07, 2)

    # 9. Massachusetts (MA) - Fair Share Surtax
    ma_res_high = server.evaluate_state_residency(
        jurisdiction="MA",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=2000000.0,
        filing_status="single",
    )
    assert ma_res_high["is_resident"] is True
    # Taxable: 2M - 4400 = 1,995,600. Base: 5%, Surtax on 995,600: 4%
    taxable_ma = 2000000.0 - 4400.0
    expected_ma = round((taxable_ma * 0.05) + ((taxable_ma - 1000000.0) * 0.04), 2)
    assert ma_res_high["tax_computation"]["estimated_state_tax"] == expected_ma

    # 10. New Jersey (NJ) - brackets & convenience rule
    nj_nonres = server.evaluate_state_residency(
        jurisdiction="NJ",
        days_in_state=30,
        is_domiciled=False,
        telecommuting_days_out_of_state=100,
    )
    assert any("NJ Reciprocal Convenience" in f for f in nj_nonres["domicile_audit_risk"]["flags"])

    for inc, expected_pct in [
        (2000000.0, 0.1075),
        (800000.0, 0.0897),
        (200000.0, 0.0637),
        (50000.0, 0.035),
    ]:
        nj_res = server.evaluate_state_residency(
            jurisdiction="NJ",
            days_in_state=200,
            maintains_permanent_abode=True,
            federal_agi=inc,
            filing_status="single",
        )
        assert nj_res["is_resident"] is True
        taxable_nj = inc - 1000.0
        exp_nj = round(taxable_nj * expected_pct, 2)
        assert nj_res["tax_computation"]["estimated_state_tax"] == exp_nj

    # 11. Illinois (IL) - flat 4.95% rate
    il_res = server.evaluate_state_residency(
        jurisdiction="IL",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=100000.0,
        filing_status="married_joint",
    )
    assert il_res["is_resident"] is True
    # Taxable: 100k - 5550 = 94450 * 0.0495
    assert il_res["tax_computation"]["estimated_state_tax"] == round(94450.0 * 0.0495, 2)

    # 12. Pennsylvania (PA) - flat 3.07% rate and convenience rule
    pa_nonres = server.evaluate_state_residency(
        jurisdiction="PA",
        days_in_state=30,
        is_domiciled=False,
        telecommuting_days_out_of_state=80,
    )
    assert pa_nonres["is_resident"] is False
    assert any(
        "PA Convenience of the Employer Rule" in f
        for f in pa_nonres["domicile_audit_risk"]["flags"]
    )
    assert pa_nonres["tax_computation"]["estimated_state_tax"] == 0.0

    pa_res = server.evaluate_state_residency(
        jurisdiction="PA",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=100000.0,
    )
    assert pa_res["is_resident"] is True
    assert pa_res["tax_computation"]["estimated_state_tax"] == round(100000.0 * 0.0307, 2)

    # 13. Connecticut (CT) - brackets and reciprocal convenience rule
    ct_nonres = server.evaluate_state_residency(
        jurisdiction="CT",
        days_in_state=40,
        is_domiciled=False,
        telecommuting_days_out_of_state=90,
    )
    assert ct_nonres["is_resident"] is False
    assert any(
        "CT Reciprocal Convenience Rule" in f for f in ct_nonres["domicile_audit_risk"]["flags"]
    )

    for inc, expected_ct_pct in [
        (600000.0, 0.0699),
        (200000.0, 0.06),
        (80000.0, 0.05),
        (20000.0, 0.03),
    ]:
        ct_res = server.evaluate_state_residency(
            jurisdiction="CT",
            days_in_state=200,
            maintains_permanent_abode=True,
            federal_agi=inc,
            filing_status="single",
        )
        assert ct_res["is_resident"] is True
        taxable_ct = inc - 15000.0
        assert ct_res["tax_computation"]["estimated_state_tax"] == round(
            taxable_ct * expected_ct_pct, 2
        )

    # 14. North Carolina (NC) - 183-day presumption and rate phase-ins
    nc_presumed = server.evaluate_state_residency(
        jurisdiction="NC",
        days_in_state=190,
        federal_agi=100000.0,
        filing_status="married_joint",
        tax_year=2024,
    )
    assert nc_presumed["is_resident"] is True
    # Taxable: 100k - 25,500 = 74,500 * 0.045
    assert nc_presumed["tax_computation"]["estimated_state_tax"] == round(74500.0 * 0.045, 2)

    nc_2025 = server.evaluate_state_residency(
        jurisdiction="NC",
        days_in_state=190,
        federal_agi=100000.0,
        filing_status="single",
        tax_year=2025,
    )
    # Taxable: 100k - 12,750 = 87,250 * 0.0425
    assert nc_2025["tax_computation"]["estimated_state_tax"] == round(87250.0 * 0.0425, 2)

    nc_2026 = server.evaluate_state_residency(
        jurisdiction="NC",
        days_in_state=190,
        federal_agi=100000.0,
        filing_status="single",
        tax_year=2026,
    )
    # Taxable: 100k - 12,750 = 87,250 * 0.0399
    assert nc_2026["tax_computation"]["estimated_state_tax"] == round(87250.0 * 0.0399, 2)

    # 15. Virginia (VA) - actual residency and graduated brackets
    va_nonres = server.evaluate_state_residency(
        jurisdiction="VA",
        days_in_state=50,
        is_domiciled=False,
    )
    assert va_nonres["is_resident"] is False
    assert va_nonres["tax_computation"]["estimated_state_tax"] == 0.0

    va_res_high = server.evaluate_state_residency(
        jurisdiction="VA",
        days_in_state=200,
        federal_agi=50000.0,
        filing_status="single",
    )
    assert va_res_high["is_resident"] is True
    # Taxable: 50,000 - 8,500 = 41,500.
    # Tax: 720 + 0.0575 * (41500 - 17000) = 2128.75
    assert va_res_high["tax_computation"]["estimated_state_tax"] == 2128.75

    for inc, expected_va in [
        (18500.0, round(120.0 + 0.05 * 5000.0, 2)),
        (12500.0, round(60.0 + 0.03 * 1000.0, 2)),
        (10500.0, round(0.02 * 2000.0, 2)),
    ]:
        va_res_bracket = server.evaluate_state_residency(
            jurisdiction="VA",
            days_in_state=200,
            federal_agi=inc,
            filing_status="single",
        )
        assert va_res_bracket["tax_computation"]["estimated_state_tax"] == expected_va

    # 16. Maryland (MD) - county piggyback tax notice and graduated brackets
    md_nonres = server.evaluate_state_residency(
        jurisdiction="MD",
        days_in_state=50,
        is_domiciled=False,
    )
    assert md_nonres["is_resident"] is False

    md_res_top = server.evaluate_state_residency(
        jurisdiction="MD",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=400000.0,
        filing_status="married_joint",
    )
    assert md_res_top["is_resident"] is True
    assert any("MD County Piggyback Tax" in f for f in md_res_top["domicile_audit_risk"]["flags"])
    # MFJ deduction capped at 5450. Taxable: 400,000 - 5450 = 394,550 (>300k) -> 5.75%
    assert md_res_top["tax_computation"]["estimated_state_tax"] == round(394550.0 * 0.0575, 2)

    for inc, expected_rate in [(200000.0, 0.05), (80000.0, 0.0475)]:
        md_res_sub = server.evaluate_state_residency(
            jurisdiction="MD",
            days_in_state=200,
            maintains_permanent_abode=True,
            federal_agi=inc,
            filing_status="single",
        )
        taxable_md = inc - 2700.0
        assert md_res_sub["tax_computation"]["estimated_state_tax"] == round(
            taxable_md * expected_rate, 2
        )

    # 17. Colorado (CO) - Proposition 121 flat 4.40%
    co_nonres = server.evaluate_state_residency(
        jurisdiction="CO",
        days_in_state=30,
        is_domiciled=False,
    )
    assert co_nonres["is_resident"] is False
    assert co_nonres["tax_computation"]["estimated_state_tax"] == 0.0

    co_res = server.evaluate_state_residency(
        jurisdiction="CO",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=100000.0,
        filing_status="single",
    )
    assert co_res["is_resident"] is True
    # Taxable: 100,000 - 14,600 = 85,400 * 0.044
    assert co_res["tax_computation"]["estimated_state_tax"] == round(85400.0 * 0.044, 2)

    # 18. Arizona (AZ) - SB 1828 flat 2.50% & 9-month presumption
    az_presumption_flag = server.evaluate_state_residency(
        jurisdiction="AZ",
        days_in_state=200,
        is_domiciled=False,
        maintains_permanent_abode=False,
    )
    assert az_presumption_flag["is_resident"] is False
    assert any(
        "AZ Rebuttable Presumption" in f
        for f in az_presumption_flag["domicile_audit_risk"]["flags"]
    )

    az_res = server.evaluate_state_residency(
        jurisdiction="AZ",
        days_in_state=280,
        federal_agi=100000.0,
        filing_status="single",
    )
    assert az_res["is_resident"] is True
    # Taxable: 100,000 - 14,600 = 85,400 * 0.025
    assert az_res["tax_computation"]["estimated_state_tax"] == round(85400.0 * 0.025, 2)

    # 19. Ohio (OH) - bright-line contact periods & consolidated brackets
    oh_contact_flag = server.evaluate_state_residency(
        jurisdiction="OH",
        days_in_state=200,
        is_domiciled=False,
    )
    assert oh_contact_flag["is_resident"] is False
    assert any(
        "OH Bright-Line Contact Period Rule" in f
        for f in oh_contact_flag["domicile_audit_risk"]["flags"]
    )

    for inc, expected_calc in [
        (150000.0, round(2033.63 + 0.035 * (150000.0 - 2400.0 - 100000.0), 2)),
        (50000.0, round(0.0275 * (50000.0 - 2400.0 - 26050.0), 2)),
        (25000.0, 0.0),
    ]:
        oh_res = server.evaluate_state_residency(
            jurisdiction="OH",
            days_in_state=220,
            federal_agi=inc,
            filing_status="single",
        )
        assert oh_res["is_resident"] is True
        assert oh_res["tax_computation"]["estimated_state_tax"] == expected_calc

    # 20. Alaska (AK) - No personal income tax
    ak_res = server.evaluate_state_residency(
        jurisdiction="AK",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=250000.0,
        filing_status="single",
    )
    assert ak_res["is_resident"] is True
    assert ak_res["tax_computation"]["taxable_net_income"] == 0.0
    assert ak_res["tax_computation"]["estimated_state_tax"] == 0.0

    # 21. Delaware (DE) - Graduated & Convenience of Employer Rule
    de_telecommute = server.evaluate_state_residency(
        jurisdiction="DE",
        days_in_state=50,
        is_domiciled=False,
        telecommuting_days_out_of_state=40,
    )
    assert de_telecommute["is_resident"] is False
    assert any(
        "DE Convenience of the Employer Doctrine" in f
        for f in de_telecommute["domicile_audit_risk"]["flags"]
    )
    assert de_telecommute["tax_computation"]["estimated_state_tax"] == 0.0

    de_res_sub = server.evaluate_state_residency(
        jurisdiction="DE",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=50000.0,
        filing_status="single",
    )
    assert de_res_sub["is_resident"] is True
    # Taxable: 50,000 - 3250 = 46,750 (<= 60,000 top bracket) -> 46,750 * (0.066 * 0.65)
    expected_de_sub = round(46750.0 * (0.066 * 0.65), 2)
    assert de_res_sub["tax_computation"]["estimated_state_tax"] == expected_de_sub

    de_res_top = server.evaluate_state_residency(
        jurisdiction="DE",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=150000.0,
        filing_status="married_joint",
    )
    assert de_res_top["is_resident"] is True
    # Taxable: 150,000 - 6500 = 143,500 (> 60,000 top bracket)
    base_de = 60000.0 * (0.066 * 0.65)
    expected_de_top = round(base_de + 0.066 * (143500.0 - 60000.0), 2)
    assert de_res_top["tax_computation"]["estimated_state_tax"] == expected_de_top

    # 22. Nebraska (NE) - Convenience of Employer Rule
    ne_telecommute = server.evaluate_state_residency(
        jurisdiction="NE",
        days_in_state=30,
        is_domiciled=False,
        telecommuting_days_out_of_state=15,
    )
    assert any(
        "NE Convenience of the Employer Doctrine" in f
        for f in ne_telecommute["domicile_audit_risk"]["flags"]
    )

    # 23. Indiana (IN) - Flat rate & LIT county tax flag
    in_res = server.evaluate_state_residency(
        jurisdiction="IN",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=100000.0,
        filing_status="single",
    )
    assert in_res["is_resident"] is True
    assert any("IN Local Income Tax" in f for f in in_res["domicile_audit_risk"]["flags"])
    # Taxable: 100,000 - 1000 = 99,000 * 0.0305
    assert in_res["tax_computation"]["estimated_state_tax"] == round(99000.0 * 0.0305, 2)

    in_res_mfj = server.evaluate_state_residency(
        jurisdiction="IN",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=100000.0,
        filing_status="mfj",
    )
    # Taxable: 100,000 - 2000 = 98,000 * 0.0305
    assert in_res_mfj["tax_computation"]["estimated_state_tax"] == round(98000.0 * 0.0305, 2)

    in_nonres = server.evaluate_state_residency(
        jurisdiction="IN",
        days_in_state=30,
        is_domiciled=False,
    )
    assert in_nonres["is_resident"] is False
    assert in_nonres["tax_computation"]["estimated_state_tax"] == 0.0

    # 24. New Mexico (NM) - requires_abode = False & inclusive threshold (185 days or more)
    nm_day_185 = server.evaluate_state_residency(
        jurisdiction="NM",
        days_in_state=185,
        is_domiciled=False,
        maintains_permanent_abode=False,
    )
    assert nm_day_185["is_resident"] is True

    nm_day_184 = server.evaluate_state_residency(
        jurisdiction="NM",
        days_in_state=184,
        is_domiciled=False,
        maintains_permanent_abode=False,
    )
    assert nm_day_184["is_resident"] is False

    # Compare with a state requiring abode (e.g. UT)
    ut_no_abode = server.evaluate_state_residency(
        jurisdiction="UT",
        days_in_state=190,
        is_domiciled=False,
        maintains_permanent_abode=False,
    )
    assert ut_no_abode["is_resident"] is False

    # 25. North Dakota (ND) - 0% zero-rate bottom bracket ($47,150 single in 2024 / $48,475 in 2025)
    nd_zero = server.evaluate_state_residency(
        jurisdiction="ND",
        days_in_state=250,
        maintains_permanent_abode=True,
        federal_agi=30000.0,
        filing_status="single",
        tax_year=2024,
    )
    assert nd_zero["is_resident"] is True
    assert nd_zero["tax_computation"]["estimated_state_tax"] == 0.0

    nd_mid = server.evaluate_state_residency(
        jurisdiction="ND",
        days_in_state=250,
        maintains_permanent_abode=True,
        federal_agi=70000.0,
        filing_status="single",
        tax_year=2024,
    )
    # Taxable: 70,000 - 14,600 = 55,400. Above zero bracket (47,150)
    expected_nd_mid = round((55400.0 - 47150.0) * 0.0195, 2)
    assert nd_mid["tax_computation"]["estimated_state_tax"] == expected_nd_mid

    nd_top = server.evaluate_state_residency(
        jurisdiction="ND",
        days_in_state=250,
        maintains_permanent_abode=True,
        federal_agi=300000.0,
        filing_status="single",
        tax_year=2024,
    )
    # Taxable: 300,000 - 14,600 = 285,400. Above top bracket (238,200)
    base_nd = (238200.0 - 47150.0) * 0.0195
    expected_nd_top = round(base_nd + 0.025 * (285400.0 - 238200.0), 2)
    assert nd_top["tax_computation"]["estimated_state_tax"] == expected_nd_top

    # 2025 ND: Zero-rate threshold rises to $48,475
    nd_2025 = server.evaluate_state_residency(
        jurisdiction="ND",
        days_in_state=250,
        maintains_permanent_abode=True,
        federal_agi=62000.0,
        filing_status="single",
        tax_year=2025,
    )
    # Taxable: 62,000 - 14,600 = 47,400 <= 48,475 -> 0 tax
    assert nd_2025["tax_computation"]["estimated_state_tax"] == 0.0

    nd_top_2025 = server.evaluate_state_residency(
        jurisdiction="ND",
        days_in_state=250,
        maintains_permanent_abode=True,
        federal_agi=300000.0,
        filing_status="single",
        tax_year=2025,
    )
    # Taxable: 300,000 - 14,600 = 285,400 (> 244,825)
    base_nd_2025 = (244825.0 - 48475.0) * 0.0195
    expected_nd_top_2025 = round(base_nd_2025 + 0.025 * (285400.0 - 244825.0), 2)
    assert nd_top_2025["tax_computation"]["estimated_state_tax"] == expected_nd_top_2025

    # 26. Iowa (IA) - 2024 graduated vs 2025 flat 3.80%
    ia_2024 = server.evaluate_state_residency(
        jurisdiction="IA",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=100000.0,
        filing_status="single",
        tax_year=2024,
    )
    # 2024 taxable: 100,000 - 14,600 = 85,400 (> 31,590) -> graduated
    base_ia = 31590.0 * (0.057 * 0.65)
    expected_ia_2024 = round(base_ia + 0.057 * (85400.0 - 31590.0), 2)
    assert ia_2024["tax_computation"]["estimated_state_tax"] == expected_ia_2024

    ia_2025 = server.evaluate_state_residency(
        jurisdiction="IA",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=100000.0,
        filing_status="single",
        tax_year=2025,
    )
    assert ia_2025["tax_computation"]["estimated_state_tax"] == round(85400.0 * 0.038, 2)

    # 27. Louisiana (LA) - 2024 graduated vs 2025 flat 3.00% (Act 11 $12.5k deduction)
    la_2024 = server.evaluate_state_residency(
        jurisdiction="LA",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=100000.0,
        filing_status="single",
        tax_year=2024,
    )
    # Taxable: 100,000 - 4500 = 95,500 (> 50,000)
    base_la = 50000.0 * (0.0425 * 0.65)
    expected_la_2024 = round(base_la + 0.0425 * (95500.0 - 50000.0), 2)
    assert la_2024["tax_computation"]["estimated_state_tax"] == expected_la_2024

    la_2025 = server.evaluate_state_residency(
        jurisdiction="LA",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=100000.0,
        filing_status="single",
        tax_year=2025,
    )
    # Taxable: 100,000 - 12500 (Act 11) = 87,500 * 0.03 = 2,625.00
    assert la_2025["tax_computation"]["estimated_state_tax"] == 2625.0

    la_2026 = server.evaluate_state_residency(
        jurisdiction="LA",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=100000.0,
        filing_status="single",
        tax_year=2026,
    )
    # Taxable: 100,000 - 12838 (RIB 26-019) = 87,162 * 0.03 = 2,614.86
    assert la_2026["tax_computation"]["estimated_state_tax"] == 2614.86
    assert any("RIB 26-019" in flag for flag in la_2026["domicile_audit_risk"]["flags"])

    la_2027 = server.evaluate_state_residency(
        jurisdiction="LA",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=100000.0,
        filing_status="single",
        tax_year=2027,
    )
    assert la_2027["tax_computation"]["estimated_state_tax"] == 2614.86
    assert "PROVISIONAL" in la_2027["tax_computation"]["rate_description"]
    assert any("provisional" in flag.lower() for flag in la_2027["domicile_audit_risk"]["flags"])

    # 28. Indiana (IN) - multi-year rates (2024=3.05%, 2025=3.00%, 2026=2.95%, 2027+=2.90%)
    in_2024 = server.evaluate_state_residency(
        jurisdiction="IN",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=100000.0,
        filing_status="single",
        tax_year=2024,
    )
    assert in_2024["tax_computation"]["estimated_state_tax"] == round(99000.0 * 0.0305, 2)

    in_2025 = server.evaluate_state_residency(
        jurisdiction="IN",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=100000.0,
        filing_status="single",
        tax_year=2025,
    )
    assert in_2025["tax_computation"]["estimated_state_tax"] == round(99000.0 * 0.03, 2)

    in_2026 = server.evaluate_state_residency(
        jurisdiction="IN",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=100000.0,
        filing_status="single",
        tax_year=2026,
    )
    assert in_2026["tax_computation"]["estimated_state_tax"] == round(99000.0 * 0.0295, 2)

    in_2027 = server.evaluate_state_residency(
        jurisdiction="IN",
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=100000.0,
        filing_status="single",
        tax_year=2027,
    )
    assert in_2027["tax_computation"]["estimated_state_tax"] == round(99000.0 * 0.029, 2)

    # 29. All 51 jurisdictions can be called without exception
    for jur in server.SUPPORTED_STATE_JURISDICTIONS:
        res = server.evaluate_state_residency(
            jurisdiction=jur,
            days_in_state=10,
            is_domiciled=False,
        )
        assert res["jurisdiction"] == jur
        assert res["status"] == "success"
