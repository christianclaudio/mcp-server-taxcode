"""Statutory retrieval, search, publication, and reference domain sub-server."""

from __future__ import annotations

import logging
from typing import Annotated, Any
from urllib.parse import quote

from fastmcp import FastMCP
from fastmcp.server.transforms import ToolTransform
from fastmcp.tools.tool_transform import ArgTransformConfig, ToolTransformConfig
from mcp.types import ToolAnnotations
from pydantic import Field

from taxcode_mcp.db import PUBLICATION_CATEGORIES, SNIPPET_MAX_CHARS, db
from taxcode_mcp.disclaimers import output_notice, reference_labels
from taxcode_mcp.errors import tool_error
from taxcode_mcp.paging import (
    LIST_MAX_LIMIT,
    RESPONSE_MAX_CHARS,
    TEXT_PAGE_CHARS,
    PageCursorError,
    applied_limit,
    decode_cursor,
    encode_cursor,
    fit_items,
    page_resource_text,
    page_statute,
)

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
        "(pass a result's id as citation_or_section to get its full text). "
        f"Long text comes in pages of up to {TEXT_PAGE_CHARS:,} characters per text field "
        f"(each result stays under {RESPONSE_MAX_CHARS:,} characters): data.text_page gives "
        "the offset and full lengths, and while data.next_cursor is not null, call again "
        "with page_cursor set to it for the next page."
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
    page_cursor: Annotated[
        str | None,
        Field(
            description=(
                "Optional next_cursor from a previous statutes_lookup_statute result, to "
                "read the next page of that same version's text. When set, it decides "
                "the version, language and position; the other arguments are ignored."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Find a statute by record id, exact citation, or section code in DuckDB."""
    try:
        offset = 0
        if page_cursor:
            record_id, lang, offset = decode_cursor(page_cursor)
            result = db.lookup_statute(citation_or_section=record_id, lang=lang)
            if result is not None and result["id"] != record_id:
                result = None
        else:
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
            msg += " not found in indexed corpora."
            if tax_year is not None and not page_cursor:
                # A record id names one version; if its window misses the year, name the
                # row that answers that year.
                other = db.version_for_year(citation_or_section, tax_year, jurisdiction)
                if other is not None:
                    msg += (
                        " That record id names another version; tax year"
                        f" {tax_year} is answered by '{other}'."
                    )
            return {
                "status": "not_found",
                "query": citation_or_section,
                "tax_year": tax_year,
                "jurisdiction": jurisdiction,
                "message": msg,
                **reference_labels(),
                **output_notice(),
            }

        def wrap(data: dict[str, Any]) -> dict[str, Any]:
            return {"status": "success", "data": data, **output_notice()}

        return wrap(page_statute(result, offset, wrap))
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
        f"result's id to read the full text. limit is clamped to 1..{LIST_MAX_LIMIT} "
        "results per call (see limit_applied); while next_offset is not null, call again "
        "with offset set to it for the next page."
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
    limit: Annotated[
        int,
        Field(
            description=(
                f"Results per call (default 5), clamped to 1..{LIST_MAX_LIMIT}: a value "
                f"under 1 is raised to 1 and one over {LIST_MAX_LIMIT} is lowered."
            )
        ),
    ] = 5,
    offset: Annotated[
        int, Field(ge=0, description="Optional number of ranked results to skip (paging).")
    ] = 0,
    client_id: str = "taxcode-client-default",
) -> dict[str, Any]:
    """Search statutes by keywords or concepts in DuckDB."""
    try:
        page_size = applied_limit(limit)
        found = db.search_statutes(
            query=query,
            jurisdiction=jurisdiction,
            corpus=corpus,
            tax_year=tax_year,
            limit=page_size + 1,
            offset=offset,
        )

        def build(matches: list[dict[str, Any]], next_offset: int | None) -> dict[str, Any]:
            return {
                "status": "success",
                "query": query,
                "tax_year": tax_year,
                "jurisdiction": jurisdiction,
                "count": len(matches),
                "results": matches,
                "limit_applied": page_size,
                "offset": offset,
                "next_offset": next_offset,
                "full_text": FULL_TEXT_HINT,
                "client_id": client_id,
                **output_notice(),
            }

        return fit_items(found[:page_size], len(found) > page_size, offset, build)
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


@statutes_server.tool(
    name="list_publications",
    description=(
        "List administrative circulars, tax guidance publications, and bulletins, newest "
        f"first. limit is clamped to 1..{LIST_MAX_LIMIT} per call (see limit_applied); "
        "while next_offset is not null, call again with offset set to it for the next page."
    ),
    annotations=ANNOTATION_READ_ONLY,
    tags={"statutes", "publications", "read_only"},
)
async def list_publications(
    category: Annotated[str | None, Field(description=CATEGORY_DESCRIPTION)] = None,
    since_date: str | None = None,
    limit: Annotated[
        int,
        Field(
            description=(
                f"Publications per call (default 10), clamped to 1..{LIST_MAX_LIMIT}: a "
                f"value under 1 is raised to 1 and one over {LIST_MAX_LIMIT} is lowered."
            )
        ),
    ] = 10,
    offset: Annotated[
        int, Field(ge=0, description="Optional number of publications to skip (paging).")
    ] = 0,
) -> dict[str, Any]:
    """List administrative publications from DuckDB."""
    try:
        page_size = applied_limit(limit)
        found = db.list_publications(
            category=category, since_date=since_date, limit=page_size + 1, offset=offset
        )

        def build(pubs: list[dict[str, Any]], next_offset: int | None) -> dict[str, Any]:
            return {
                "status": "success",
                "count": len(pubs),
                "publications": pubs,
                "limit_applied": page_size,
                "offset": offset,
                "next_offset": next_offset,
                **output_notice(),
            }

        return fit_items(found[:page_size], len(found) > page_size, offset, build)
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


# The root mounts this server under the "statutes" namespace, and FastMCP's mount puts
# the namespace after the scheme (taxcode://{x} becomes taxcode://statutes/{x}). So the
# template below has no statutes/ segment of its own, and clients see
# taxcode://statutes/{jurisdiction}/{section}{?page_cursor}.
STATUTE_RESOURCE_BASE = "taxcode://statutes"


@statutes_server.resource(
    "taxcode://{jurisdiction}/{section}{?page_cursor}",
    description=(
        "Statutory text by jurisdiction and section. Long text comes in pages of up to "
        f"{TEXT_PAGE_CHARS:,} characters (each read stays under {RESPONSE_MAX_CHARS:,} "
        "characters); a page that is not the last ends with the URI of the next page, "
        "whose page_cursor is the same cursor statutes_lookup_statute returns as next_cursor."
    ),
)
async def get_statute_resource(jurisdiction: str, section: str, page_cursor: str = "") -> str:
    """Fetch one page of statutory text as a resource by jurisdiction and section."""
    offset = 0
    if page_cursor:
        try:
            record_id, lang, offset = decode_cursor(page_cursor)
        except PageCursorError as exc:
            return f"{exc}."
        res = db.lookup_statute(citation_or_section=record_id, lang=lang)
        if res is not None and res["id"] != record_id:
            res = None
    else:
        res = db.lookup_statute(citation_or_section=section, jurisdiction=jurisdiction)
    if not res:
        return f"Statute '{section}' in jurisdiction '{jurisdiction}' not found."
    sup = res["superseded_year"]
    superseded = "current / en vigor" if sup is None else str(sup)
    header = (
        f"{res['citation']} - {res['title']}\n"
        f"Jurisdiction: {res['jurisdiction']} ({res['corpus']})\n"
        f"Effective Year: {res['effective_year']}\n"
        f"Superseded Year / Año de sustitución: {superseded}\n"
        f"Validity: {res['validity']['en']}\n"
        f"Vigencia: {res['validity']['es']}\n\n"
    )
    record_id, lang = res["id"], res["language"]

    path = f"{quote(jurisdiction, safe='')}/{quote(section, safe='')}"

    def next_uri(end: int) -> str:
        return f"{STATUTE_RESOURCE_BASE}/{path}?page_cursor={encode_cursor(record_id, lang, end)}"

    return page_resource_text(header, res["operative_text"] or "", offset, next_uri)
