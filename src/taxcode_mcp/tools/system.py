"""System domain sub-server: health status, diagnostics, and capabilities."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from taxcode_mcp.errors import tool_error

logger = logging.getLogger(__name__)

ANNOTATION_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

system_server = FastMCP(
    "taxcode-system",
    instructions="System diagnostics and capabilities domain.",
)


@system_server.tool(
    name="get_health_status",
    description="Check the connectivity and operational health of the upstream API.",
    annotations=ANNOTATION_READ_ONLY,
    tags={"system", "health", "read_only"},
)
async def get_health_status() -> dict[str, Any]:
    """Verify connectivity to upstream service."""
    from taxcode_mcp import server

    try:
        data = await server.client.request("GET", "health")
        return {"status": "success", "data": data}
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


@system_server.resource(
    "taxcode://reference/capabilities",
    name="system_capabilities",
    description="Return static system capabilities reference.",
)
def get_capabilities() -> str:
    return (
        "Tax Code MCP Server capabilities: Health, Items, Bulk, MRTR Elicitations, "
        "Server Composition."
    )
