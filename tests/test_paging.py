"""Hard response cap and paging on the statutes tools."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from taxcode_mcp import paging
from taxcode_mcp.db import INITIAL_PUBLICATIONS, INITIAL_STATUTES
from taxcode_mcp.paging import (
    LIST_MAX_LIMIT,
    RESPONSE_MAX_CHARS,
    TEXT_PAGE_CHARS,
    PageCursorError,
    applied_limit,
    decode_cursor,
    encode_cursor,
    fit_items,
    response_chars,
)
from taxcode_mcp.server import mcp

LONGEST = max(INITIAL_STATUTES, key=lambda s: max(len(s["text_en"]), len(s["text_es"])))


async def _call(client: Client, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(tool, args)
    assert isinstance(result.structured_content, dict)
    return result.structured_content


async def _all_pages(client: Client, rid: str, lang: str) -> list[dict[str, Any]]:
    args: dict[str, Any] = {"citation_or_section": rid, "lang": lang}
    pages = [await _call(client, "statutes_lookup_statute", args)]
    while pages[-1]["data"]["next_cursor"]:
        cursor = pages[-1]["data"]["next_cursor"]
        args = {"citation_or_section": "ignored", "page_cursor": cursor}
        pages.append(await _call(client, "statutes_lookup_statute", args))
    return pages


def test_cursor_round_trips_and_rejects_foreign_tokens() -> None:
    cursor = encode_cursor("pr-cri-1031-02", "es", 8000)
    assert "=" not in cursor
    assert decode_cursor(cursor) == ("pr-cri-1031-02", "es", 8000)
    bad = [
        "not a cursor!",
        "e30",  # {}
        encode_cursor("x", "en", 0).replace("e", "f"),
        "eyJ2IjoxLCJpZCI6IngiLCJsYW5nIjoiZW4iLCJvZmZzZXQiOi0xfQ",  # offset -1
        "W10",  # []
        "_w",  # invalid UTF-8
    ]
    for token in bad:
        with pytest.raises(PageCursorError):
            decode_cursor(token)


def test_applied_limit_clamps() -> None:
    assert [applied_limit(n) for n in (-3, 0, 5, LIST_MAX_LIMIT, 10_000)] == [
        0,
        0,
        5,
        LIST_MAX_LIMIT,
        LIST_MAX_LIMIT,
    ]


async def test_lookup_pages_round_trip_to_the_exact_text_under_the_cap() -> None:
    assert max(len(LONGEST["text_en"]), len(LONGEST["text_es"])) > 3 * TEXT_PAGE_CHARS
    async with Client(mcp) as client:
        for lang in ("en", "es"):
            pages = await _all_pages(client, LONGEST["id"], lang)
            assert len(pages) > 3
            for page in pages:
                assert response_chars(page) <= RESPONSE_MAX_CHARS
                assert page["data"]["id"] == LONGEST["id"]
                assert page["data"]["language"] == lang
            assert "".join(p["data"]["text_en"] for p in pages) == LONGEST["text_en"]
            assert "".join(p["data"]["text_es"] for p in pages) == LONGEST["text_es"]
            joined = "".join(p["data"]["operative_text"] for p in pages)
            assert joined == LONGEST[f"text_{lang}"]
            last = pages[-1]["data"]
            assert last["next_cursor"] is None and last["text_page"]["is_last_page"] is True
            first = pages[0]["data"]["text_page"]
            assert first == {
                "offset": 0,
                "page_chars": TEXT_PAGE_CHARS,
                "text_en_chars": len(LONGEST["text_en"]),
                "text_es_chars": len(LONGEST["text_es"]),
                "is_last_page": False,
            }


async def test_every_row_fits_the_cap_and_short_rows_are_one_page() -> None:
    async with Client(mcp) as client:
        for row in INITIAL_STATUTES:
            args = {"citation_or_section": row["id"]}
            first = await _call(client, "statutes_lookup_statute", args)
            assert response_chars(first) <= RESPONSE_MAX_CHARS, row["id"]
            if max(len(row["text_en"]), len(row["text_es"])) <= TEXT_PAGE_CHARS:
                assert first["data"]["text_en"] == row["text_en"]
                assert first["data"]["next_cursor"] is None


async def test_cursor_for_a_missing_record_is_not_found_and_a_bad_cursor_is_an_error() -> None:
    async with Client(mcp) as client:
        gone = encode_cursor("us-irc-0000", "en", 8000)
        res = await _call(
            client, "statutes_lookup_statute", {"citation_or_section": "x", "page_cursor": gone}
        )
        assert res["status"] == "not_found"
        # An id that only matches as a section code is not the version the cursor named.
        other = encode_cursor("933", "en", 0)
        res = await _call(
            client, "statutes_lookup_statute", {"citation_or_section": "x", "page_cursor": other}
        )
        assert res["status"] == "not_found"
        with pytest.raises(ToolError, match="page_cursor is not a cursor"):
            await client.call_tool(
                "statutes_lookup_statute", {"citation_or_section": "x", "page_cursor": "zz"}
            )


async def test_offset_past_the_end_is_an_empty_last_page() -> None:
    async with Client(mcp) as client:
        cursor = encode_cursor("us-irc-933", "en", 10**6)
        res = await _call(
            client, "statutes_lookup_statute", {"citation_or_section": "x", "page_cursor": cursor}
        )
        data = res["data"]
        assert data["text_en"] == data["text_es"] == data["operative_text"] == ""
        assert data["next_cursor"] is None


async def test_window_shrinks_when_the_cap_would_be_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paging, "RESPONSE_MAX_CHARS", 12_000)
    async with Client(mcp) as client:
        res = await _call(client, "statutes_lookup_statute", {"citation_or_section": "us-irc-1"})
    page = res["data"]["text_page"]
    assert page["page_chars"] < TEXT_PAGE_CHARS
    assert response_chars(res) <= 12_000
    monkeypatch.setattr(paging, "RESPONSE_MAX_CHARS", 10)
    async with Client(mcp) as client:
        res = await _call(client, "statutes_lookup_statute", {"citation_or_section": "us-irc-1"})
    assert res["data"]["text_page"]["page_chars"] == paging.MIN_TEXT_PAGE_CHARS


async def test_search_pages_cover_every_match_once() -> None:
    async with Client(mcp) as client:
        everything = await _call(client, "statutes_search_tax_code", {"query": "", "limit": 1000})
        assert everything["limit_applied"] == LIST_MAX_LIMIT
        assert everything["count"] <= LIST_MAX_LIMIT
        assert response_chars(everything) <= RESPONSE_MAX_CHARS
        seen: list[str] = []
        offset: int | None = 0
        while offset is not None:
            res = await _call(
                client, "statutes_search_tax_code", {"query": "", "limit": 10, "offset": offset}
            )
            assert res["offset"] == offset and res["limit_applied"] == 10
            seen += [r["id"] for r in res["results"]]
            offset = res["next_offset"]
    assert sorted(seen) == sorted(s["id"] for s in INITIAL_STATUTES)
    assert len(seen) == len(set(seen))


async def test_publication_pages_cover_every_row_once_newest_first() -> None:
    async with Client(mcp) as client:
        seen: list[dict[str, Any]] = []
        offset: int | None = 0
        while offset is not None:
            res = await _call(client, "statutes_list_publications", {"limit": 7, "offset": offset})
            seen += res["publications"]
            offset = res["next_offset"]
        last = await _call(client, "statutes_list_publications", {"limit": 0})
    # Newest first; ``id`` breaks doc_date ties, so pages never repeat or skip a row.
    by_id = sorted(INITIAL_PUBLICATIONS, key=lambda p: p["id"])
    expected = sorted(by_id, key=lambda p: p["doc_date"], reverse=True)
    assert [p["id"] for p in seen] == [p["id"] for p in expected]
    assert last["count"] == 0 and last["next_offset"] == 0


def test_fit_items_drops_trailing_items_to_fit_and_keeps_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [{"text": "x" * 100} for _ in range(5)]

    def build(page: list[dict[str, Any]], next_offset: int | None) -> dict[str, Any]:
        return {"items": page, "next_offset": next_offset}

    full = fit_items(items, False, 0, build)
    assert len(full["items"]) == 5 and full["next_offset"] is None
    small = len(json.dumps(build(items[:2], 2)))
    monkeypatch.setattr(paging, "RESPONSE_MAX_CHARS", small)
    res = fit_items(items, False, 3, build)
    assert len(res["items"]) == 2 and res["next_offset"] == 5
    monkeypatch.setattr(paging, "RESPONSE_MAX_CHARS", 1)
    res = fit_items(items, True, 0, build)
    assert len(res["items"]) == 1 and res["next_offset"] == 1
    assert fit_items([], False, 0, build) == {"items": [], "next_offset": None}
