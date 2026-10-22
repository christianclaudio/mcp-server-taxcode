"""Search returns snippets plus the record id; the full text comes from lookup by id.

Langfuse eval runs on v0.6.1 found each search result carried the full statute text
(10-23K tokens), which fed a 551K-token search loop. These tests pin the snippet
contract, the id round-trip, and that ranking is unchanged.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from fastmcp import Client

from taxcode_mcp import server
from taxcode_mcp.db import (
    SNIPPET_LEAD_CHARS,
    SNIPPET_MAX_CHARS,
    TaxDatabase,
    make_snippet,
    pick_snippet,
    search_terms,
)
from taxcode_mcp.server import create_server
from taxcode_mcp.tools import statutes
from taxcode_mcp.tools.compliance import compliance_server

ELLIPSIS = "\u2026"
QUERIES = [
    "933",
    "bona fide residence",
    "capital gains washington",
    "residente de buena fe",
    "net investment income",
    "convenience of the employer",
    "Form 8898",
]


@pytest.fixture
def db() -> TaxDatabase:
    return TaxDatabase(":memory:")


def _flat(text: str | None) -> str:
    return " ".join((text or "").split())


def _body(snippet: str) -> str:
    return snippet.removeprefix(ELLIPSIS).removesuffix(ELLIPSIS)


def _words(n: int, word: str = "lorem") -> str:
    return " ".join(f"{word}{i}" for i in range(n))


# --- make_snippet ---------------------------------------------------------------


def test_short_text_is_returned_whole() -> None:
    assert make_snippet("  short   text\nhere ", "text", ["text"]) == ("short text here", True)
    assert make_snippet(None, "x", ["x"]) == ("", False)


def test_snippet_is_bounded_and_contains_match_on_word_boundaries() -> None:
    text = f"{_words(80)} needle {_words(80, 'ipsum')}"
    snippet, matched = make_snippet(text, "needle", ["needle"])
    assert matched
    assert len(snippet) <= SNIPPET_MAX_CHARS
    assert "needle" in snippet
    assert snippet.startswith(ELLIPSIS) and snippet.endswith(ELLIPSIS)
    body = _body(snippet)
    words = set(text.split())
    # Cut on word boundaries: the first and last words are whole words of the text.
    assert body.split()[0] in words and body.split()[-1] in words
    assert body in text
    # The match sits near the start, after up to SNIPPET_LEAD_CHARS of context.
    assert body.index("needle") <= SNIPPET_LEAD_CHARS


def test_phrase_beats_earlier_single_term() -> None:
    text = f"alpha {_words(100)} alpha beta {_words(100)}"
    snippet, _ = make_snippet(text, "alpha beta", ["alpha", "beta"])
    assert "alpha beta" in snippet


def test_earliest_matched_term_when_phrase_absent() -> None:
    text = f"{_words(60)} beta {_words(60)} alpha {_words(60)}"
    snippet, matched = make_snippet(text, "alpha beta", ["alpha", "beta"])
    assert matched
    assert "beta" in snippet and " alpha " not in snippet


def test_no_match_gives_opening_words() -> None:
    text = _words(200)
    snippet, matched = make_snippet(text, "zzz", ["zzz"])
    assert not matched
    assert snippet.startswith("lorem0 ") and snippet.endswith(ELLIPSIS)
    assert len(snippet) <= SNIPPET_MAX_CHARS


def test_match_near_end_keeps_a_full_window() -> None:
    text = f"{_words(200)} needle"
    snippet, _ = make_snippet(text, "needle", ["needle"])
    assert snippet.endswith("needle") and snippet.startswith(ELLIPSIS)
    assert len(snippet) > SNIPPET_MAX_CHARS - 20


def test_match_past_the_lead_window_still_fits() -> None:
    long_phrase = " ".join(["needle"] * 40)  # 279 chars, longer than budget - lead
    text = f"{_words(100)} {long_phrase} {_words(100)}"
    snippet, _ = make_snippet(text, long_phrase, [])
    assert long_phrase in snippet
    assert len(snippet) <= SNIPPET_MAX_CHARS


def test_match_longer_than_the_window_is_cut() -> None:
    giant = "x" * 1000
    text = f"{_words(50)} {giant} {_words(50)}"
    snippet, matched = make_snippet(text, giant, [])
    assert matched
    assert snippet == f"{ELLIPSIS}{'x' * (SNIPPET_MAX_CHARS - 2)}{ELLIPSIS}"


def test_pick_snippet_falls_back_by_language() -> None:
    en, es = "income from Puerto Rico", "ingreso de Puerto Rico residente"
    assert pick_snippet([("en", en), ("es", es)], "residente", ["residente"]) == (es, "es")
    assert pick_snippet([("en", en), ("es", es)], "zzz", ["zzz"]) == (en, "en")
    assert pick_snippet([("en", None), ("es", es)], "zzz", ["zzz"]) == (es, "es")
    assert pick_snippet([("en", None), ("es", "")], "zzz", ["zzz"]) == ("", "en")


# --- search_statutes ------------------------------------------------------------


@pytest.mark.parametrize("query", QUERIES)
def test_search_rows_carry_bounded_snippet_not_full_text(db: TaxDatabase, query: str) -> None:
    rows = db.search_statutes(query=query, limit=50)
    assert rows
    phrase = " ".join(query.lower().split())
    terms = search_terms(phrase)
    for row in rows:
        assert {"operative_text", "text_en", "text_es"}.isdisjoint(row)
        assert len(row["snippet"]) <= SNIPPET_MAX_CHARS
        assert row["snippet_language"] in {"en", "es"}
        full = db.lookup_statute(row["id"], lang=row["snippet_language"])
        assert full is not None
        flat = _flat(full["operative_text"])
        assert _body(row["snippet"]) in flat
        # When the text holds the phrase or a term, the snippet shows one of them.
        if any(t in flat.lower() for t in [phrase, *terms]):
            assert any(t in row["snippet"].lower() for t in [phrase, *terms])


def test_search_spanish_preference(db: TaxDatabase) -> None:
    rows = db.search_statutes(query="residente de buena fe", jurisdiction="US", lang="es")
    assert rows[0]["snippet_language"] == "es"
    assert "residente de buena fe" in rows[0]["snippet"]
    # Spanish preferred, but the match is only in the English text: English snippet.
    row = db.search_statutes(query="933", jurisdiction="US", lang="es", limit=1)[0]
    assert row["id"] == "us-irc-24"
    assert (row["snippet_language"], "933" in row["snippet"]) == ("en", True)


# Catalog-only rows seeded after the orders below were recorded, and rows whose summary
# was later replaced by the full statutory text (pr-ley60-2022-01, by Act 38-2026).
SEEDED_AFTER_735477D = frozenset(
    {
        "us-irc-957",
        "us-irc-6688",
        "us-cfr-1-937-3",
        "pr-cri-1031-02",
        "pr-cri-1062-01",
        "pr-ley60-6070-55",
        "pr-ley60-6020-10",
        "pr-ley60-6020-10-2019",
        "pr-ley60-6020-10-2025",
        "pr-cri-1062-03",
        "pr-ley60-1020-02",
        "pr-ley60-2022-01",
        "pr-ley60-2022-02",
        "pr-ley60-6020-03",
    }
)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("933", ["us-irc-24", "us-irc-865", "us-irc-933"]),
        (
            "bona fide residence",
            ["us-irc-911", "us-irc-937", "us-irc-933", "us-cfr-1-937-1", "us-cfr-1-937-2"],
        ),
        (
            "residente de buena fe",
            ["us-cfr-1-937-1", "us-irc-933", "az-stat-43-104", "ca-rtc-17014", "ca-rtc-17951"],
        ),
        ("§933 Puerto Rico", ["us-irc-24", "us-irc-865", "us-irc-933"]),
    ],
)
def test_ranking_unchanged(db: TaxDatabase, query: str, expected: list[str]) -> None:
    """Orders recorded on main 735477d, before snippets; matching reads the full text.

    Rows seeded later slot in between; the earlier rows keep their relative order.
    """
    # Wide enough that the later rows slotting in cannot push a recorded row off the page.
    ids = [r["id"] for r in db.search_statutes(query=query, limit=20)]
    assert [i for i in ids if i not in SEEDED_AFTER_735477D][: len(expected)] == expected


# --- id round-trip --------------------------------------------------------------


@pytest.mark.parametrize("query", QUERIES)
async def test_every_result_id_works_in_lookup_tool(query: str) -> None:
    res = await server.search_tax_code(query, limit=10)
    assert res["results"]
    for row in res["results"]:
        found = await server.lookup_statute(row["id"])
        assert found["status"] == "success"
        data = found["data"]
        assert data["id"] == row["id"]
        assert data["citation"] == row["citation"]
        field = "text_en" if row["snippet_language"] == "en" else "text_es"
        # Long text comes in pages; join them to search the whole section.
        text = data[field]
        while data["next_cursor"]:
            data = (await server.lookup_statute("", page_cursor=data["next_cursor"]))["data"]
            text += data[field]
        assert _body(row["snippet"]) in _flat(text)


async def test_superseded_version_id_returns_that_version() -> None:
    res = await server.search_tax_code("Individual Income Tax Rates", jurisdiction="GA", limit=10)
    ids = [r["id"] for r in res["results"]]
    assert "ga-ocga-48-7-20-2024" in ids
    found = await server.lookup_statute("GA-OCGA-48-7-20-2024")
    assert found["data"]["id"] == "ga-ocga-48-7-20-2024"
    assert found["data"]["effective_year"] == 2024
    # A citation lookup still returns the newest version.
    newest = await server.lookup_statute("O.C.G.A. § 48-7-20")
    assert newest["data"]["id"] == "ga-ocga-48-7-20-2026"


async def test_id_round_trip_over_mcp() -> None:
    app = create_server(profile="readonly", enable_tool_search=False)
    async with Client(app) as client:
        search = await client.call_tool("statutes_search_tax_code", {"query": "933"})
        payload: dict[str, Any] = search.structured_content or {}
        hint = payload["full_text"]
        row = next(r for r in payload["results"] if r["id"] == "us-irc-933")
        got = await client.call_tool(hint["tool"], {hint["argument"]: row[hint["value_from"]]})
        data = (got.structured_content or {})["data"]
        assert data["id"] == "us-irc-933"
        assert data["citation"] == row["citation"]


async def test_resource_accepts_record_id() -> None:
    text = await statutes.get_statute_resource("US", "us-irc-933")
    assert text.startswith("26 U.S.C. § 933")


# --- tool output and tools/list -------------------------------------------------


async def test_search_output_has_no_full_text() -> None:
    res = await server.search_tax_code("933")
    full = (await server.lookup_statute("us-irc-933"))["data"]["operative_text"]
    dumped = json.dumps(res, ensure_ascii=False)
    assert "operative_text" not in dumped
    assert _flat(full) not in dumped
    assert len(full) > SNIPPET_MAX_CHARS
    hint = res["full_text"]
    assert hint == statutes.FULL_TEXT_HINT
    assert hint["tool"] == "statutes_lookup_statute"
    assert set(hint) >= {"en", "es"}


@pytest.mark.parametrize("profile", ["full", "statutes", "readonly", "researcher"])
async def test_full_text_tool_is_listed_wherever_search_is(profile: str) -> None:
    names = {t.name for t in await create_server(profile=profile).list_tools()}
    assert "statutes_search_tax_code" in names
    assert statutes.FULL_TEXT_HINT["tool"] in names


async def test_tools_list_descriptions() -> None:
    async with Client(create_server(profile="full")) as client:
        tools = {t.name: t for t in await client.list_tools()}
    search = tools["statutes_search_tax_code"].description or ""
    assert "snippet" in search and "statutes_lookup_statute" in search and "id" in search
    lookup = tools["statutes_lookup_statute"]
    assert "record id" in (lookup.description or "")
    assert "us-irc-933" in lookup.input_schema["properties"]["citation_or_section"]["description"]
    residency = tools["compliance_evaluate_pr_bona_fide_residency"].description or ""
    assert "Form 8898" in residency
    assert "worldwide gross income is more than $75,000" in residency
    assert "reporting_requirements" in residency


async def test_8898_threshold_in_description_matches_output() -> None:
    """The description and the reporting_requirements output state the same threshold."""
    out = server.evaluate_pr_bona_fide_residency(200, 30, tax_year=2025)
    form = next(r for r in out["reporting_requirements"] if "Form 8898" in r)
    in_output = re.search(r"\$(\d+)k", form)
    assert in_output is not None
    tool = await compliance_server.get_tool("evaluate_pr_bona_fide_residency")
    assert tool is not None
    in_description = re.search(r"\$([\d,]+)", tool.description or "")
    assert in_description is not None
    assert int(in_output.group(1)) * 1000 == int(in_description.group(1).replace(",", ""))
