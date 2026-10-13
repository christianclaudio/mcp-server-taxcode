#!/usr/bin/env python3
"""Validate tool registration counts and MCP 2.0 behavioral annotations."""

import asyncio
import sys

from taxcode_mcp.server import mcp

EXPECTED_TOOLS = {
    "system_get_health_status": {"read_only": True, "destructive": False},
    "statutes_lookup_statute": {"read_only": True, "destructive": False},
    "statutes_search_tax_code": {"read_only": True, "destructive": False},
    "statutes_list_publications": {"read_only": True, "destructive": False},
    "compliance_evaluate_pr_bona_fide_residency": {"read_only": True, "destructive": False},
    "compliance_calculate_built_in_gain_sourcing": {"read_only": True, "destructive": False},
    "compliance_evaluate_georgia_residency": {"read_only": True, "destructive": False},
}


async def verify_contracts() -> int:
    tools = await mcp.list_tools()
    tool_map = {t.name: t for t in tools}

    print(f"[*] Validating {len(tools)} registered MCP tools...")

    for name, expected in EXPECTED_TOOLS.items():
        if name not in tool_map:
            print(f"[x] Error: Missing expected tool '{name}'")
            return 1
        t = tool_map[name]
        ann = t.annotations
        if ann is None:
            print(f"[x] Error: Tool '{name}' has no MCP 2.0 annotations!")
            return 1
        read_only = (
            ann.read_only_hint
            if hasattr(ann, "read_only_hint")
            else getattr(ann, "readOnlyHint", None)
        )
        destructive = (
            ann.destructive_hint
            if hasattr(ann, "destructive_hint")
            else getattr(ann, "destructiveHint", None)
        )
        if read_only != expected["read_only"]:
            print(
                f"[x] Error: Tool '{name}' read_only_hint mismatch: "
                f"{read_only} != {expected['read_only']}"
            )
            return 1
        if destructive != expected["destructive"]:
            print(
                f"[x] Error: Tool '{name}' destructive_hint mismatch: "
                f"{destructive} != {expected['destructive']}"
            )
            return 1

    print("[✓] All tool contracts and MCP 2.0 annotations verified successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(verify_contracts()))
