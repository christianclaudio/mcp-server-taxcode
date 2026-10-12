"""Items domain sub-server: CRUD operations, bulk management, and prompts."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.server.transforms import ToolTransform
from fastmcp.tools.tool_transform import ArgTransformConfig, ToolTransformConfig
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from taxcode_mcp.config import settings
from taxcode_mcp.errors import SafetyViolationError, redact_secrets, tool_error, tool_failure
from taxcode_mcp.middleware import ItemsDomainGuardMiddleware

logger = logging.getLogger(__name__)

ANNOTATION_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

ANNOTATION_MUTATION = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)

ANNOTATION_DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)


class ConfirmationResponse(BaseModel):
    """Schema for Multi Round-Trip Request (MRTR) user confirmation per SEP-2322."""

    confirm: bool = Field(description="Explicit confirmation to execute the requested operation.")


async def _verify_confirmation(ctx: Context | None, message: str) -> bool:
    """Verify explicit confirmation via multi-round-trip input_responses or interactive elicit."""
    if ctx is None:
        return False

    # 1. Modern multi-round-trip input_responses channel (SEP-2322)
    if ctx.input_responses:
        confirm_res = ctx.input_responses.get("confirm")
        if confirm_res and getattr(confirm_res, "action", None) == "accept":
            res_content = getattr(confirm_res, "content", None) or getattr(
                confirm_res, "data", None
            )
            if isinstance(res_content, dict) and res_content.get("confirm"):
                return True
            if getattr(res_content, "confirm", False):
                return True

    # 2. Interactive elicitation fallback (retained for interactive clients)
    try:
        elicit_res = await ctx.elicit(
            message=message,
            response_type=ConfirmationResponse,
        )
        action = getattr(elicit_res, "action", None)
        data = getattr(elicit_res, "data", None)
        if action == "accept" and data is not None and getattr(data, "confirm", False):
            return True
    except Exception:
        pass

    return False


items_server = FastMCP(
    "taxcode-items",
    instructions="Resource item CRUD and bulk lifecycle domain.",
)

# Attach child domain guard middleware
items_server.add_middleware(ItemsDomainGuardMiddleware())

# FastMCP 4 Tool Transformation: Hide internal infrastructure arguments from client schema
items_server.add_transform(
    ToolTransform(
        {
            "create_item": ToolTransformConfig(
                arguments={
                    "client_id": ArgTransformConfig(
                        hide=True,
                        default="taxcode-client-default",
                        description="Internal client identifier injected automatically.",
                    )
                }
            )
        }
    )
)


@items_server.tool(
    name="list_items",
    description="List available resource items with optional pagination.",
    annotations=ANNOTATION_READ_ONLY,
    tags={"items", "read_only"},
)
async def list_items(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Query items from the upstream service."""
    from taxcode_mcp import server

    try:
        params = {"limit": limit, "offset": offset}
        data = await server.client.request("GET", "items", params=params)
        return {"status": "success", "data": data}
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


@items_server.tool(
    name="get_item",
    description="Retrieve details for a specific item by ID.",
    annotations=ANNOTATION_READ_ONLY,
    tags={"items", "read_only"},
)
async def get_item(item_id: str) -> dict[str, Any]:
    """Fetch single item by sanitized ID."""
    from taxcode_mcp import server

    try:
        sanitized_id = server.client.sanitize_path_param(item_id)
        data = await server.client.request("GET", f"items/{sanitized_id}")
        return {"status": "success", "data": data}
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


@items_server.tool(
    name="create_item",
    description="Create a new item with specified attributes.",
    annotations=ANNOTATION_MUTATION,
    tags={"items", "mutation"},
)
async def create_item(
    name: str,
    payload: dict[str, Any] | None = None,
    client_id: str = "taxcode-client-default",
) -> dict[str, Any]:
    """Create a new resource item with client_id injected via ToolTransform."""
    from taxcode_mcp import server

    if settings.MCP_READONLY:
        raise SafetyViolationError("Server running in read-only mode (MCP_READONLY=1).")
    try:
        body = {"name": name, "client_id": client_id, **(payload or {})}
        data = await server.client.request("POST", "items", json_data=body)
        return {"status": "success", "data": data}
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


@items_server.tool(
    name="delete_item",
    description="Delete a single resource item. Requires confirm=True or interactive confirmation.",
    annotations=ANNOTATION_DESTRUCTIVE,
    tags={"items", "destructive"},
)
async def delete_item(
    item_id: str,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Delete single item with mandatory confirmation gate and MRTR elicitation support."""
    from taxcode_mcp import server

    if settings.MCP_READONLY:
        raise SafetyViolationError("Server running in read-only mode (MCP_READONLY=1).")

    # MRTR interactive elicitation or input_responses check
    if not confirm:
        confirm = await _verify_confirmation(
            ctx, f"Are you sure you want to delete item '{item_id}'?"
        )

    if not confirm:
        raise SafetyViolationError(
            f"Safety Gate: Deleting item '{item_id}' requires explicit confirm=True."
        )
    try:
        sanitized_id = server.client.sanitize_path_param(item_id)
        data = await server.client.request("DELETE", f"items/{sanitized_id}")
        return {"status": "success", "deleted": True, "data": data}
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


@items_server.tool(
    name="bulk_delete_items",
    description="Batch delete multiple items. Gated by TAXCODE_MCP_ALLOW_BULK_DESTRUCTIVE.",
    annotations=ANNOTATION_DESTRUCTIVE,
    tags={"items", "destructive"},
)
async def bulk_delete_items(
    item_ids: list[str],
    dry_run: bool = True,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Bulk delete with dry-run default, environment gating, and MRTR confirmation."""
    from taxcode_mcp import server

    if settings.MCP_READONLY:
        raise SafetyViolationError("Server running in read-only mode.")
    if not settings.MCP_ALLOW_BULK_DESTRUCTIVE:
        raise SafetyViolationError(
            "Bulk destructive operations disabled. Set TAXCODE_MCP_ALLOW_BULK_DESTRUCTIVE=1."
        )
    if dry_run:
        return {
            "status": "dry_run",
            "message": f"Simulated deletion of {len(item_ids)} items.",
            "item_ids": item_ids,
        }

    # MRTR interactive elicitation or input_responses check
    if not confirm:
        confirm = await _verify_confirmation(
            ctx, f"Are you sure you want to permanently bulk delete {len(item_ids)} items?"
        )

    if not confirm:
        raise SafetyViolationError("Live bulk deletion requires explicit confirm=True.")

    deleted: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for i_id in item_ids:
        try:
            sanitized_id = server.client.sanitize_path_param(i_id)
            res = await server.client.request("DELETE", f"items/{sanitized_id}")
            deleted.append({"id": i_id, "status": "deleted", "result": res})
        except Exception as exc:
            errors.append(
                {"id": i_id, "status": "failed", "error": {"message": redact_secrets(str(exc))}}
            )

    if errors and not deleted:
        raise tool_failure(
            "batch_failed",
            f"All {len(errors)} item deletions failed; nothing was deleted.",
            failed_count=len(errors),
            errors=errors,
        ) from None
    return {
        "status": "success" if not errors else "partial_success",
        "deleted_count": len(deleted),
        "failed_count": len(errors),
        "results": deleted,
        "errors": errors,
    }


@items_server.prompt("analyze_item")
def analyze_item_prompt(item_id: str) -> str:
    """Generate prompt template for analyzing a resource."""
    return f"Please retrieve item '{item_id}' using get_item and analyze its configuration."
