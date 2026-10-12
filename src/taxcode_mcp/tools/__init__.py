"""Modular domain tools and sub-servers for FastMCP 4 Server Composition."""

from __future__ import annotations

from taxcode_mcp.tools.items import (
    _verify_confirmation,
    analyze_item_prompt,
    bulk_delete_items,
    create_item,
    delete_item,
    get_item,
    items_server,
    list_items,
)
from taxcode_mcp.tools.system import (
    get_capabilities,
    get_health_status,
    system_server,
)

__all__ = [
    "_verify_confirmation",
    "analyze_item_prompt",
    "bulk_delete_items",
    "create_item",
    "delete_item",
    "get_capabilities",
    "get_health_status",
    "get_item",
    "items_server",
    "list_items",
    "system_server",
]
