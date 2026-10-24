"""Batch read rules: parallel and concurrent statutory lookups and search."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastmcp import Client

from taxcode_mcp import server


@pytest.mark.asyncio
async def test_batch_parallel_statute_lookups() -> None:
    """Verify concurrent batch lookups across multiple citations execute safely."""
    citations = [
        "26 U.S.C. § 933",
        "13 L.P.R.A. § 30041",
        "26 U.S.C. § 937",
        "Act 60-2019",
    ]
    async with Client(server.create_server()) as mcp_client:
        tasks = [
            mcp_client.call_tool(
                "statutes_lookup_statute",
                {"citation_or_section": citation},
                raise_on_error=False,
            )
            for citation in citations
        ]
        results = await asyncio.gather(*tasks)

    assert len(results) == len(citations)
    for res in results:
        assert not res.is_error
        data = json.loads(res.content[0].text)
        assert data["status"] in ("success", "not_found")


@pytest.mark.asyncio
async def test_batch_parallel_search() -> None:
    """Verify concurrent full-text and concept searches against DuckDB execute safely."""
    queries = ["residency", "capital gain", "corporation", "source of income"]
    async with Client(server.create_server()) as mcp_client:
        tasks = [
            mcp_client.call_tool(
                "statutes_search_tax_code",
                {"query": q, "limit": 3},
                raise_on_error=False,
            )
            for q in queries
        ]
        results = await asyncio.gather(*tasks)

    assert len(results) == len(queries)
    for res in results:
        assert not res.is_error
        data = json.loads(res.content[0].text)
        assert data["status"] == "success"
        assert "count" in data
        assert "results" in data


@pytest.mark.asyncio
async def test_batch_lookup_mixed_found_and_not_found() -> None:
    """Verify batch lookups accurately partition into found statutes and not_found responses."""
    queries = [
        "26 U.S.C. § 933",
        "NONEXISTENT-STATUTE-XYZ-999",
        "26 U.S.C. § 865",
        "FAKE-CITATION-000",
    ]
    async with Client(server.create_server()) as mcp_client:
        tasks = [
            mcp_client.call_tool(
                "statutes_lookup_statute",
                {"citation_or_section": q},
                raise_on_error=False,
            )
            for q in queries
        ]
        results = await asyncio.gather(*tasks)

    statuses: list[str] = []
    for res in results:
        assert not res.is_error
        data = json.loads(res.content[0].text)
        statuses.append(data["status"])

    assert statuses == ["success", "not_found", "success", "not_found"]


@pytest.mark.asyncio
async def test_batch_concurrent_compliance_evaluations() -> None:
    """Verify concurrent compliance residency evaluations return deterministic results."""
    scenarios: list[dict[str, Any]] = [
        {"days_present_pr": 185, "days_present_us": 20},
        {"days_present_pr": 120, "days_present_us": 120},
        {"days_present_pr": 200, "days_present_us": 10},
    ]
    async with Client(server.create_server()) as mcp_client:
        tasks = [
            mcp_client.call_tool(
                "compliance_evaluate_pr_bona_fide_residency",
                scenario,
                raise_on_error=False,
            )
            for scenario in scenarios
        ]
        results = await asyncio.gather(*tasks)

    assert len(results) == len(scenarios)
    for res in results:
        assert not res.is_error
        data = json.loads(res.content[0].text)
        assert data["status"] == "success"
        assert "presence_test" in data


@pytest.mark.asyncio
async def test_batch_parallel_cancellation() -> None:
    """Cancellation of a single batch task propagates cleanly without breaking others."""
    async with Client(server.create_server()) as mcp_client:
        t1 = asyncio.create_task(
            mcp_client.call_tool(
                "statutes_lookup_statute",
                {"citation_or_section": "26 U.S.C. § 933"},
                raise_on_error=False,
            )
        )
        t2 = asyncio.create_task(
            mcp_client.call_tool(
                "statutes_lookup_statute",
                {"citation_or_section": "26 U.S.C. § 865"},
                raise_on_error=False,
            )
        )
        t1.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t1

        res2 = await t2
        assert not res2.is_error
        data2 = json.loads(res2.content[0].text)
        assert data2["status"] == "success"


@pytest.mark.asyncio
async def test_batch_state_residency_matches_the_direct_call_in_out_years() -> None:
    """Concurrent state-residency calls give the same out-year answers as a direct call."""
    from taxcode_mcp.tools import compliance

    scenarios: list[dict[str, Any]] = [
        {"jurisdiction": code, "tax_year": year, "federal_agi": agi, "filing_status": "single"}
        for code, year, agi in (
            ("MS", 2028, 100000.0),
            ("UT", 2026, 60000.0),
            ("NE", 2027, 58850.0),
            ("OH", 2028, 68450.0),
        )
    ]
    common = {"days_in_state": 300, "is_domiciled": True, "maintains_permanent_abode": True}
    async with Client(server.create_server()) as mcp_client:
        results = await asyncio.gather(
            *(
                mcp_client.call_tool(
                    "compliance_evaluate_state_residency", {**s, **common}, raise_on_error=False
                )
                for s in scenarios
            )
        )
    taxes = []
    for scenario, res in zip(scenarios, results, strict=True):
        assert not res.is_error
        data = json.loads(res.content[0].text)
        direct = compliance.evaluate_state_residency(**scenario, **common)
        assert data["tax_computation"] == direct["tax_computation"]
        taxes.append(data["tax_computation"]["estimated_state_tax"])
    assert taxes == [3069.50, 2268.23, 1832.79, 1432.00]
