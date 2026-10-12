"""Statutory retrieval, search, publication, and management domain sub-server."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.server.transforms import ToolTransform
from fastmcp.tools.tool_transform import ArgTransformConfig, ToolTransformConfig
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from taxcode_mcp.config import settings
from taxcode_mcp.db import db
from taxcode_mcp.errors import SafetyViolationError, redact_secrets, tool_error, tool_failure
from taxcode_mcp.middleware import StatutesDomainGuardMiddleware

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
    idempotent_hint=True,
    open_world_hint=False,
)

statutes_server = FastMCP(
    "taxcode-statutes",
    instructions="Authoritative statutory search, citation retrieval, and circulars.",
)

# Attach child domain guard middleware
statutes_server.add_middleware(StatutesDomainGuardMiddleware())

# FastMCP 4 Tool Transformation: Hide internal infrastructure arguments from client schema
statutes_server.add_transform(
    ToolTransform(
        {
            "create_statute": ToolTransformConfig(
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


class StatuteInput(BaseModel):
    """Schema for creating a statutory provision."""

    id: str = Field(description="Unique internal identifier (e.g. pr-cri-1031-01)")
    citation: str = Field(description="Official legal citation (e.g. PR CRI Sec. 1031.01)")
    title: str = Field(description="Statutory caption or title")
    text_es: str = Field(default="", description="Operative legal text in Spanish")
    text_en: str = Field(default="", description="Unofficial or official English translation")
    jurisdiction: str = Field(default="PR", description="Jurisdiction code ('US' or 'PR')")
    corpus: str = Field(default="CRI", description="Code name ('IRC', 'CFR', 'CRI', 'LEY60')")
    section: str = Field(default="", description="Section number or code")
    effective_year: int = Field(default=2026, description="Effective or enactment year")
    source_url: str = Field(default="", description="Official publication URL")
    cross_references: list[str] = Field(default_factory=list, description="Cross references")


class ConfirmationResponse(BaseModel):
    confirm: bool = Field(description="Confirm operation execution")


async def _verify_confirmation(
    ctx: Context | None,
    message: str,
) -> bool:
    """Enforce confirmation gate via input_responses or interactive elicitation."""
    if ctx is None:
        return False

    # 1. Modern multi-round-trip input_responses channel (SEP-2322)
    responses = getattr(ctx, "input_responses", None)
    if responses is not None:
        confirm_res = responses.get("confirm")
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


@statutes_server.tool(
    name="lookup_statute",
    description="Retrieve exact statutory language and metadata by citation or section code.",
    annotations=ANNOTATION_READ_ONLY,
    tags={"statutes", "read_only"},
)
async def lookup_statute(
    citation_or_section: str,
    jurisdiction: str | None = None,
    lang: str = "en",
) -> dict[str, Any]:
    """Find a statute by exact citation or section code in DuckDB."""
    try:
        result = db.lookup_statute(
            citation_or_section=citation_or_section,
            jurisdiction=jurisdiction,
            lang=lang,
        )
        if result is None:
            return {
                "status": "not_found",
                "query": citation_or_section,
                "message": f"Statute '{citation_or_section}' not found in index.",
            }
        return {"status": "success", "data": result}
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


@statutes_server.tool(
    name="search_tax_code",
    description="Search across statutes by keyword or legal concept with optional filters.",
    annotations=ANNOTATION_READ_ONLY,
    tags={"statutes", "read_only"},
)
async def search_tax_code(
    query: str,
    jurisdiction: str | None = None,
    corpus: str | None = None,
    lang: str = "en",
    limit: int = 5,
) -> dict[str, Any]:
    """Execute search across titles, citations, and statutory text in DuckDB."""
    try:
        results = db.search_statutes(
            query=query,
            jurisdiction=jurisdiction,
            corpus=corpus,
            lang=lang,
            limit=limit,
        )
        return {"status": "success", "count": len(results), "results": results}
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


@statutes_server.tool(
    name="list_publications",
    description="List administrative circulars, determinations, and bulletins with date filter.",
    annotations=ANNOTATION_READ_ONLY,
    tags={"statutes", "read_only"},
)
async def list_publications(
    category: str | None = None,
    since_date: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """List administrative publications from DuckDB."""
    try:
        pubs = db.list_publications(category=category, since_date=since_date, limit=limit)
        return {"status": "success", "count": len(pubs), "publications": pubs}
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


@statutes_server.tool(
    name="create_statute",
    description="Add or index a statutory provision in the DuckDB database.",
    annotations=ANNOTATION_MUTATION,
    tags={"statutes", "mutation"},
)
async def create_statute(
    payload: StatuteInput,
    client_id: str = "taxcode-client-default",
) -> dict[str, Any]:
    """Insert a statutory provision into DuckDB."""
    if settings.MCP_READONLY:
        raise SafetyViolationError("Server running in read-only mode (MCP_READONLY=1).")
    try:
        statute_dict = payload.model_dump()
        db.insert_statute(statute_dict)
        return {
            "status": "success",
            "created": True,
            "data": {"created": True, "id": payload.id, "client_id": client_id},
        }
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


@statutes_server.tool(
    name="delete_statute",
    description="Delete a statute by ID. Gated by confirm=True.",
    annotations=ANNOTATION_DESTRUCTIVE,
    tags={"statutes", "destructive"},
)
async def delete_statute(
    statute_id: str,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Delete a statute with mandatory confirmation gate and MRTR elicitation support."""
    if settings.MCP_READONLY:
        raise SafetyViolationError("Server running in read-only mode (MCP_READONLY=1).")

    if not confirm:
        confirm = await _verify_confirmation(
            ctx, f"Are you sure you want to delete statute '{statute_id}'?"
        )

    if not confirm:
        raise SafetyViolationError(
            f"Safety Gate: Deleting statute '{statute_id}' requires explicit confirm=True."
        )

    try:
        deleted = db.delete_statute(statute_id)
        if not deleted:
            raise SafetyViolationError(f"Statute '{statute_id}' not found for deletion.")
        return {"status": "success", "deleted": True, "statute_id": statute_id}
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


@statutes_server.tool(
    name="bulk_delete_statutes",
    description="Batch delete statutes. Gated by TAXCODE_MCP_ALLOW_BULK_DESTRUCTIVE=1.",
    annotations=ANNOTATION_DESTRUCTIVE,
    tags={"statutes", "destructive", "bulk"},
)
async def bulk_delete_statutes(
    statute_ids: list[str],
    dry_run: bool = True,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Batch delete statutes with safety confirmation and bulk environment check."""
    if settings.MCP_READONLY:
        raise SafetyViolationError("Server running in read-only mode (MCP_READONLY=1).")

    if not settings.MCP_ALLOW_BULK_DESTRUCTIVE:
        raise SafetyViolationError(
            "Bulk destructive operations disabled. "
            "Set TAXCODE_MCP_ALLOW_BULK_DESTRUCTIVE=1 to unlock."
        )

    if dry_run:
        return {
            "status": "dry_run",
            "message": "Dry run preview: statutes that would be deleted.",
            "statute_ids": statute_ids,
            "total_count": len(statute_ids),
        }

    if not confirm:
        confirm = await _verify_confirmation(
            ctx, f"Are you sure you want to bulk-delete {len(statute_ids)} statutes?"
        )

    if not confirm:
        raise SafetyViolationError(
            f"Safety Gate: Bulk deleting {len(statute_ids)} statutes "
            "requires explicit confirm=True."
        )

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for sid in statute_ids:
        try:
            ok = db.delete_statute(sid)
            if ok:
                results.append({"id": sid, "status": "deleted"})
            else:
                errors.append(
                    {
                        "id": sid,
                        "status": "failed",
                        "error": {"message": f"Statute '{sid}' not found"},
                    }
                )
        except Exception as exc:
            errors.append(
                {
                    "id": sid,
                    "status": "failed",
                    "error": {"message": redact_secrets(str(exc))},
                }
            )

    deleted_count = len(results)
    failed_count = len(errors)

    if deleted_count == 0 and failed_count > 0:
        failure = tool_failure(
            "batch_failed",
            f"Failed to delete any statutes out of {len(statute_ids)} requested: {errors}",
            failed_count=failed_count,
            errors=errors,
        )
        raise failure from None

    status = "success" if failed_count == 0 else "partial_success"
    return {
        "status": status,
        "deleted_count": deleted_count,
        "failed_count": failed_count,
        "results": results,
        "errors": errors,
    }


@statutes_server.resource("taxcode://statutes/{jurisdiction}/{section}")
async def get_statute_resource(jurisdiction: str, section: str) -> str:
    """Fetch statutory text as a resource by jurisdiction and section."""
    res = db.lookup_statute(citation_or_section=section, jurisdiction=jurisdiction)
    if not res:
        return f"Statute '{section}' in jurisdiction '{jurisdiction}' not found."
    return (
        f"{res['citation']} - {res['title']}\n"
        f"Jurisdiction: {res['jurisdiction']} ({res['corpus']})\n"
        f"Effective Year: {res['effective_year']}\n\n"
        f"{res['operative_text']}"
    )
