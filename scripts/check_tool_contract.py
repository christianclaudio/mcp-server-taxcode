#!/usr/bin/env python3
"""Validate tool registration counts and MCP 2.0 behavioral annotations."""

import asyncio
import sys

from taxcode_mcp.server import mcp

EXPECTED_TOOLS = {
    "system_get_health_status": {"read_only": True, "destructive": False},
    "items_list_items": {"read_only": True, "destructive": False},
    "items_get_item": {"read_only": True, "destructive": False},
    "items_create_item": {"read_only": False, "destructive": False},
    "items_delete_item": {"read_only": False, "destructive": True},
    "items_bulk_delete_items": {"read_only": False, "destructive": True},
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
