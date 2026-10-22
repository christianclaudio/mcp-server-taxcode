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
LONGEST_EN = max(INITIAL_STATUTES, key=lambda s: len(s["text_en"]))


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
        1,
        1,
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
    # Newest first; ``id`` breaks doc_date ties, so pages never repeat or skip a row.
    by_id = sorted(INITIAL_PUBLICATIONS, key=lambda p: p["id"])
    expected = sorted(by_id, key=lambda p: p["doc_date"], reverse=True)
    assert [p["id"] for p in seen] == [p["id"] for p in expected]


LIST_TOOLS = [
    ("statutes_search_tax_code", {"query": ""}, "results", len(INITIAL_STATUTES)),
    ("statutes_list_publications", {}, "publications", len(INITIAL_PUBLICATIONS)),
]


@pytest.mark.parametrize("limit", [0, -1])
@pytest.mark.parametrize(("tool", "args", "key", "total"), LIST_TOOLS)
async def test_a_limit_under_one_is_raised_to_one_and_paging_terminates(
    tool: str, args: dict[str, Any], key: str, total: int, limit: int
) -> None:
    async with Client(mcp) as client:
        seen: list[str] = []
        offset: int | None = 0
        while offset is not None:
            res = await _call(client, tool, {**args, "limit": limit, "offset": offset})
            assert res["limit_applied"] == 1 and res["count"] == 1
            assert res["next_offset"] is None or res["next_offset"] > offset
            seen += [item["id"] for item in res[key]]
            offset = res["next_offset"]
            assert len(seen) <= total
    assert len(seen) == len(set(seen)) == total


@pytest.mark.parametrize("limit", [-5, 0, 1, 2, 3, 7, LIST_MAX_LIMIT, LIST_MAX_LIMIT + 1])
@pytest.mark.parametrize(("tool", "args", "key", "total"), LIST_TOOLS)
async def test_every_page_advances_from_every_offset(
    tool: str, args: dict[str, Any], key: str, total: int, limit: int
) -> None:
    """For every limit and every start offset, a page with rows left after it advances."""
    async with Client(mcp) as client:
        for offset in range(total + 2):
            res = await _call(client, tool, {**args, "limit": limit, "offset": offset})
            left = max(total - offset, 0)
            # The response cap may shorten a page, but never to nothing while rows remain.
            assert min(left, 1) <= res["count"] <= min(left, applied_limit(limit))
            if res["next_offset"] is None:
                assert offset + res["count"] >= total
            else:
                assert res["next_offset"] == offset + res["count"] > offset


def test_fit_items_refuses_an_empty_page_with_rows_after_it() -> None:
    with pytest.raises(AssertionError, match="must not be empty"):
        fit_items([], True, 4, lambda page, nxt: {"items": page, "next_offset": nxt})


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


async def _read(client: Client, uri: str) -> str:
    contents = await client.read_resource(uri)
    text = getattr(contents[0], "text", None)
    assert isinstance(text, str)
    return text


async def test_statute_resource_pages_round_trip_under_the_cap() -> None:
    """The resource template advertises page_cursor; following it rebuilds the exact text."""
    base = f"taxcode://statutes/statutes/{LONGEST_EN['jurisdiction']}/{LONGEST_EN['id']}"
    async with Client(mcp) as client:
        templates = [t.uri_template for t in await client.list_resource_templates()]
        assert "taxcode://statutes/statutes/{jurisdiction}/{section}{?page_cursor}" in templates
        pages = [await _read(client, base)]
        while "Next page: " in pages[-1]:
            query = pages[-1].rsplit("Next page: read this resource again with ", 1)[1][:-1]
            pages.append(await _read(client, base + query))
    assert len(pages) > 1
    assert all(len(page) <= RESPONSE_MAX_CHARS for page in pages)
    assert pages[-1].endswith(f"of {len(LONGEST_EN['text_en'])}. Last page.]")
    header = pages[0].split("\n\n", 1)[0] + "\n\n"
    assert all(page.startswith(header) for page in pages)
    texts = [page[len(header) :].rsplit("\n\n[Text page: ", 1)[0] for page in pages]
    assert all(len(text) <= TEXT_PAGE_CHARS for text in texts)
    assert "".join(texts) == LONGEST_EN["text_en"]


async def test_statute_resource_short_text_has_no_page_note_and_bad_cursors_say_so() -> None:
    short = min(INITIAL_STATUTES, key=lambda s: len(s["text_en"]))
    async with Client(mcp) as client:
        text = await _read(
            client, f"taxcode://statutes/statutes/{short['jurisdiction']}/{short['id']}"
        )
        assert text.endswith(short["text_en"]) and "[Text page:" not in text
        base = "taxcode://statutes/statutes/US/933?page_cursor="
        bad = await _read(client, base + "not-a-cursor")
        assert bad == "page_cursor is not a cursor from statutes_lookup_statute."
        gone = await _read(client, base + encode_cursor("zz-missing", "en", 0))
        assert gone == "Statute '933' in jurisdiction 'US' not found."
        # An id that only matches as a section code is not the version the cursor named.
        other = await _read(client, base + encode_cursor("933", "en", 0))
        assert other == gone


def test_statute_resource_window_shrinks_to_fit_the_cap() -> None:
    header = "h" * (RESPONSE_MAX_CHARS - 1_000)
    page = paging.page_resource_text(header, "x" * 20_000, 0, lambda end: f"c{end}")
    assert len(page) <= RESPONSE_MAX_CHARS
    assert page.endswith("Next page: read this resource again with ?page_cursor=c500]")
