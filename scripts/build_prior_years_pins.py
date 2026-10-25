#!/usr/bin/env python3
"""Build ``tests/fixtures/prior_years/f4f0d5d_pins.json.gz``, the existing-years pin.

The fixture records how the server answered at the base commit (``BASE_COMMIT``, the last
commit before the prior-year statute versions), so ``tests/test_prior_years_pin.py`` can
prove that those versions change no answer outside their own tax years. Build it from this
checkout against a clean checkout of the base commit::

    git worktree add --detach ../taxcode-base f4f0d5d6396fd5a5a8187f63732af16471690848
    uv run python scripts/build_prior_years_pins.py ../taxcode-base
    git worktree remove ../taxcode-base

The script imports ``taxcode_mcp`` from ``<base>/src`` (it refuses a checkout that is
dirty or not at ``BASE_COMMIT``), runs every call in :func:`plan` through an in-memory
client, and writes one entry per call: the first 16 hex digits of the sha256 of the answer
with every ``available_versions`` removed, and of those version lists alone. It uses no
network, and the output is the same bytes on every run.

The calls cover lookups by id, citation and bare section for every row, with and without
its jurisdiction, with no tax year and for 2015-2028, in English and Spanish; a resource
read for every row; nine searches (Spanish ones included) across all/US/PR, the same years
and two offsets; publications; and the state, Georgia, Puerto Rico and built-in gain tools
over their year and filing grids.

Usage::

    uv run python scripts/build_prior_years_pins.py <base checkout> [--out PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

BASE_COMMIT = "f4f0d5d6396fd5a5a8187f63732af16471690848"
FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "prior_years"
    / "f4f0d5d_pins.json.gz"
)
YEARS: list[int | None] = [None, *range(2015, 2029)]
QUERIES = (
    "residency",
    "standard deduction",
    "income",
    "Puerto Rico",
    "credit",
    "exempt",
    "residente",
    "deducción",
    "ingreso",
)
# (kind, tool name or resource URI, arguments)
Call = tuple[str, str, dict[str, Any]]


def plan(statutes: Iterable[Mapping[str, Any]], states: Iterable[str]) -> dict[str, Call]:
    """Every pinned call, by key."""
    calls: dict[str, Call] = {}

    def add(key: str, kind: str, name: str, args: dict[str, Any]) -> None:
        call = (kind, name, {k: v for k, v in args.items() if v is not None})
        assert calls.setdefault(key, call) == call, key

    add("tools|health", "tool", "system_get_health_status", {})
    for st in statutes:
        j = st["jurisdiction"]
        for q in (st["id"], st["citation"], st["section"]):
            for jj in (j, None):
                for y in YEARS:
                    for lang in ("en", "es"):
                        args = {
                            "citation_or_section": q,
                            "jurisdiction": jj,
                            "tax_year": y,
                            "lang": lang,
                        }
                        add(f"lookup|{q}|{jj}|{y}|{lang}", "tool", "statutes_lookup_statute", args)
        uri = f"taxcode://statutes/{j}/{st['section']}"
        add(f"resource|{j}|{st['section']}", "resource", uri, {})
    for q in QUERIES:
        for jur in (None, "US", "PR"):
            for y in YEARS:
                for off in (0, 5):
                    args = {"query": q, "jurisdiction": jur, "tax_year": y, "offset": off}
                    add(f"search|{q}|{jur}|{y}|{off}", "tool", "statutes_search_tax_code", args)
    for off in (0, 20):
        add(f"pubs|{off}", "tool", "statutes_list_publications", {"limit": 20, "offset": off})
    grid = [(fs, agi) for fs in ("single", "mfj") for agi in (0.0, 50000.0, 250000.0, 1500000.0)]
    for s in sorted(states):
        for y in range(2018, 2029):
            for fs, agi in grid:
                args = {
                    "jurisdiction": s,
                    "days_in_state": 200,
                    "is_domiciled": True,
                    "federal_agi": agi,
                    "filing_status": fs,
                    "age": 66,
                    "retirement_income": 30000.0,
                    "capital_gains": 100000.0,
                    "tax_year": y,
                }
                add(
                    f"state|{s}|{y}|{fs}|{agi}", "tool", "compliance_evaluate_state_residency", args
                )
    for y in range(2018, 2029):
        for fs in ("single", "mfj"):
            for agi in (0.0, 80000.0, 400000.0):
                args = {
                    "days_present_ga": 200,
                    "is_domiciled_in_ga": True,
                    "federal_agi": agi,
                    "filing_status": fs,
                    "age": 66,
                    "retirement_income": 40000.0,
                    "tax_year": y,
                }
                add(f"ga|{y}|{fs}|{agi}", "tool", "compliance_evaluate_georgia_residency", args)
    for y in YEARS:
        for d in ((200, 30), (150, 100), (60, 20)):
            args = {
                "days_present_pr": d[0],
                "days_present_us": d[1],
                "prior_year_1_pr_days": 200,
                "prior_year_2_pr_days": 200,
                "tax_year": y,
            }
            add(f"pr937|{y}|{d}", "tool", "compliance_evaluate_pr_bona_fide_residency", args)
    for m in (True, False):
        args = {
            "is_marketable": m,
            "total_gain": 100000.0,
            "fmv_on_residency_date": 150000.0,
            "adjusted_basis": 100000.0,
            "days_held_as_resident": 400,
            "total_days_held": 1000,
            "years_since_residency": 2.0,
        }
        add(f"big|{m}", "tool", "compliance_calculate_built_in_gain_sourcing", args)
    return calls


def split_versions(o: Any, found: list[Any]) -> Any:
    """``o`` without its ``available_versions`` entries, which are appended to ``found``."""
    if isinstance(o, dict):
        kept = {}
        for k, v in o.items():
            if k == "available_versions":
                found.append(v)
            else:
                kept[k] = split_versions(v, found)
        return kept
    if isinstance(o, list):
        return [split_versions(v, found) for v in o]
    return o


def sha16(o: Any) -> str:
    text = json.dumps(o, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def answer(client: Any, call: Call) -> Any:
    """One call's answer as plain data: structured content, texts, or the error text."""
    kind, name, args = call
    if kind == "tool":
        res = await client.call_tool(name, args, raise_on_error=False)
        if res.is_error:
            return {"error": "".join(getattr(c, "text", "") for c in res.content)}
        if res.structured_content is not None:
            return res.structured_content
        return ["".join(getattr(c, "text", "")) for c in res.content]
    try:
        return ["".join(getattr(x, "text", "")) for x in await client.read_resource(name)]
    except Exception as exc:  # noqa: BLE001 - the error is the pinned answer
        return {"error": type(exc).__name__ + ": " + str(exc)}


def pin(out: Any) -> list[str]:
    """``[out, versions]`` hashes of one answer."""
    found: list[Any] = []
    body = split_versions(out, found)
    return [sha16(body), sha16(found)]


def fixture_bytes(head: str, pins: Mapping[str, list[str]]) -> bytes:
    body = json.dumps({"head": head, "pins": pins}, sort_keys=True, separators=(",", ":"))
    return gzip.compress(body.encode(), compresslevel=9, mtime=0)


def read_fixture(path: Path = FIXTURE) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(gzip.decompress(path.read_bytes()))
    return data


async def _pins_from_base() -> dict[str, list[str]]:
    from fastmcp import Client

    from taxcode_mcp.catalog import load_catalog
    from taxcode_mcp.server import mcp
    from taxcode_mcp.tools.compliance import SUPPORTED_STATE_JURISDICTIONS

    calls = plan(load_catalog().statutes, SUPPORTED_STATE_JURISDICTIONS)
    pins: dict[str, list[str]] = {}
    async with Client(mcp) as client:
        for key in sorted(calls):
            pins[key] = pin(await answer(client, calls[key]))
    return pins


def _git(base: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(base), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("base", type=Path, help="a clean checkout of BASE_COMMIT")
    parser.add_argument("--out", type=Path, default=FIXTURE, help="fixture to write")
    args = parser.parse_args(argv)
    base = args.base.resolve()
    head = _git(base, "rev-parse", "HEAD")
    if head != BASE_COMMIT or _git(base, "status", "--porcelain"):
        print(f"[!] {base} must be a clean checkout of {BASE_COMMIT}", file=sys.stderr)
        return 2
    if "taxcode_mcp" in sys.modules:
        print("[!] taxcode_mcp is already imported; run this script directly", file=sys.stderr)
        return 2
    sys.path.insert(0, str(base / "src"))
    import taxcode_mcp

    module = Path(taxcode_mcp.__file__).resolve()
    if not module.is_relative_to(base / "src"):
        print(f"[!] imported {module}, not the base checkout", file=sys.stderr)
        return 2
    pins = asyncio.run(_pins_from_base())
    args.out.write_bytes(fixture_bytes(head, pins))
    print(f"[*] wrote {len(pins)} pins from {head} to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
