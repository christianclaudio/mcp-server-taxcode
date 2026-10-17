"""Statutory retrieval, search, publication, and reference domain sub-server."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.server.transforms import ToolTransform
from fastmcp.tools.tool_transform import ArgTransformConfig, ToolTransformConfig
from mcp.types import ToolAnnotations
from pydantic import Field

from taxcode_mcp.db import PUBLICATION_CATEGORIES, SNIPPET_MAX_CHARS, db
from taxcode_mcp.disclaimers import output_notice, reference_labels
from taxcode_mcp.errors import tool_error

logger = logging.getLogger(__name__)

CATEGORY_DESCRIPTION = (
    "Optional publication category. Valid values: "
    + ", ".join(PUBLICATION_CATEGORIES)
    + ". Case, spaces, hyphens and underscores are ignored ('carta circular' matches "
    "'carta-circular'); an unknown category returns no results."
)

# Search returns snippets; this tells the model how to get one result's full text.
FULL_TEXT_HINT: dict[str, str] = {
    "tool": "statutes_lookup_statute",
    "argument": "citation_or_section",
    "value_from": "id",
    "en": (
        "Results are snippets. For a result's full text, call statutes_lookup_statute "
        "with citation_or_section set to its id."
    ),
    "es": (
        "Los resultados son extractos. Para el texto completo de un resultado, llame a "
        "statutes_lookup_statute con citation_or_section igual a su id."
    ),
}

ANNOTATION_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

statutes_server = FastMCP(
    "taxcode-statutes",
    instructions="Authoritative statutory search, citation retrieval, and circulars.",
)

statutes_server.add_transform(
    ToolTransform(
        {
            "search_tax_code": ToolTransformConfig(
                arguments={
                    "client_id": ArgTransformConfig(hide=True, default="taxcode-client-default")
                }
            )
        }
    )
)


@statutes_server.tool(
    name="lookup_statute",
    description=(
        "Retrieve exact statutory language, temporal version, and metadata by "
        "citation, section code, or the record id from statutes_search_tax_code "
        "(pass a result's id as citation_or_section to get its full text)."
    ),
    annotations=ANNOTATION_READ_ONLY,
    tags={"statutes", "read_only"},
)
async def lookup_statute(
    citation_or_section: Annotated[
        str,
        Field(
            description=(
                "Citation ('26 U.S.C. § 933'), section code ('933'), or a record id "
                "from statutes_search_tax_code results ('us-irc-933')."
            )
        ),
    ],
    jurisdiction: str | None = None,
    tax_year: int | None = None,
    lang: str = "en",
) -> dict[str, Any]:
    """Find a statute by record id, exact citation, or section code in DuckDB."""
    try:
        result = db.lookup_statute(
            citation_or_section=citation_or_section,
            jurisdiction=jurisdiction,
            tax_year=tax_year,
            lang=lang,
        )
        if result is None:
            msg = f"Statute matching '{citation_or_section}'"
            if tax_year:
                msg += f" for tax year {tax_year}"
            if jurisdiction:
                msg += f" in jurisdiction '{jurisdiction}'"
            return {
                "status": "not_found",
                "query": citation_or_section,
                "tax_year": tax_year,
                "jurisdiction": jurisdiction,
                "message": f"{msg} not found in indexed corpora.",
                **reference_labels(),
                **output_notice(),
            }
        return {"status": "success", "data": result, **output_notice()}
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


@statutes_server.tool(
    name="search_tax_code",
    description=(
        "Keyword and concept search across Federal and State statutory corpora. "
        "Multi-word queries match individual terms: rows with the whole phrase rank "
        "first, then by how many terms match, then newer effective year first "
        "(empty or non-numeric years last), then id. Each result is a short snippet "
        f"(up to {SNIPPET_MAX_CHARS} characters around the first matched term) with its "
        "id, citation, title, jurisdiction, year, validity and content labels, not the "
        "full text: call statutes_lookup_statute with citation_or_section set to the "
        "result's id to read the full text."
    ),
    annotations=ANNOTATION_READ_ONLY,
    tags={"statutes", "search", "read_only"},
)
async def search_tax_code(
    query: str,
    jurisdiction: Annotated[
        str | None,
        Field(
            description=(
                "Optional jurisdiction code or full name, e.g. 'WA' or 'Washington', "
                "'PR' or 'Puerto Rico', 'US' or 'federal'."
            )
        ),
    ] = None,
    corpus: Annotated[
        str | None,
        Field(
            description=(
                "Optional corpus code, e.g. 'IRC', 'RCW', 'LEY60'. A jurisdiction code "
                "or name ('washington') is also accepted and filters by jurisdiction."
            )
        ),
    ] = None,
    tax_year: int | None = None,
    limit: int = 5,
    client_id: str = "taxcode-client-default",
) -> dict[str, Any]:
    """Search statutes by keywords or concepts in DuckDB."""
    try:
        matches = db.search_statutes(
            query=query,
            jurisdiction=jurisdiction,
            corpus=corpus,
            tax_year=tax_year,
            limit=limit,
        )
        return {
            "status": "success",
            "query": query,
            "tax_year": tax_year,
            "jurisdiction": jurisdiction,
            "count": len(matches),
            "results": matches,
            "full_text": FULL_TEXT_HINT,
            "client_id": client_id,
            **output_notice(),
        }
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


@statutes_server.tool(
    name="list_publications",
    description="List administrative circulars, tax guidance publications, and bulletins.",
    annotations=ANNOTATION_READ_ONLY,
    tags={"statutes", "publications", "read_only"},
)
async def list_publications(
    category: Annotated[str | None, Field(description=CATEGORY_DESCRIPTION)] = None,
    since_date: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """List administrative publications from DuckDB."""
    try:
        pubs = db.list_publications(category=category, since_date=since_date, limit=limit)
        return {
            "status": "success",
            "count": len(pubs),
            "publications": pubs,
            **output_notice(),
        }
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


@statutes_server.resource("taxcode://statutes/{jurisdiction}/{section}")
async def get_statute_resource(jurisdiction: str, section: str) -> str:
    """Fetch statutory text as a resource by jurisdiction and section."""
    res = db.lookup_statute(citation_or_section=section, jurisdiction=jurisdiction)
    if not res:
        return f"Statute '{section}' in jurisdiction '{jurisdiction}' not found."
    sup = res["superseded_year"]
    superseded = "current / en vigor" if sup is None else str(sup)
    return (
        f"{res['citation']} - {res['title']}\n"
        f"Jurisdiction: {res['jurisdiction']} ({res['corpus']})\n"
        f"Effective Year: {res['effective_year']}\n"
        f"Superseded Year / Año de sustitución: {superseded}\n"
        f"Validity: {res['validity']['en']}\n"
        f"Vigencia: {res['validity']['es']}\n\n"
        f"{res['operative_text']}"
    )
