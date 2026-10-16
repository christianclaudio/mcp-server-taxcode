"""Category normalization, jurisdiction names and ranked multi-term statute search.

Inputs come from the Langfuse eval runs, where models sent these and got 0 results.
"""

from __future__ import annotations

import pytest

from taxcode_mcp import server
from taxcode_mcp.db import (
    INITIAL_PUBLICATIONS,
    INITIAL_STATUTES,
    JURISDICTION_NAMES,
    PUBLICATION_CATEGORIES,
    TaxDatabase,
    normalize_category,
    resolve_jurisdiction,
    search_terms,
    year_sort_key,
)

WA_CAPITAL_GAINS = {"RCW 82.87.020", "RCW 82.87.040"}


@pytest.fixture
def db() -> TaxDatabase:
    return TaxDatabase(":memory:")


def _citations(rows: list[dict[str, object]]) -> list[object]:
    return [r["citation"] for r in rows]


# --- publication categories ---------------------------------------------------


@pytest.mark.parametrize(
    "value", ["carta circular", "Carta-Circular", "CARTA_CIRCULAR", "  carta -_ circular  "]
)
def test_category_variants_find_carta_circular(db: TaxDatabase, value: str) -> None:
    """'carta circular' (Nexus's failing input) and its variants all match the slug."""
    pubs = db.list_publications(category=value, limit=50)
    expected = {p["id"] for p in INITIAL_PUBLICATIONS if p["category"] == "carta-circular"}
    assert expected
    assert {p["id"] for p in pubs} == expected


def test_unknown_category_returns_no_results(db: TaxDatabase) -> None:
    assert db.list_publications(category="not a category") == []


def test_normalize_category() -> None:
    assert normalize_category(" IRS_Publication ") == "irs-publication"
    assert normalize_category("-policy  bulletin-") == "policy-bulletin"


def test_publication_categories_derived_from_data() -> None:
    assert PUBLICATION_CATEGORIES == tuple(sorted({p["category"] for p in INITIAL_PUBLICATIONS}))


async def test_list_publications_schema_lists_categories() -> None:
    mcp = server.create_server()
    tool = next(t for t in await mcp.list_tools() if t.name.endswith("list_publications"))
    desc = tool.parameters["properties"]["category"]["description"]
    for cat in PUBLICATION_CATEGORIES:
        assert cat in desc
    # Still a free string, so variants pass schema validation and get normalized.
    assert "enum" not in str(tool.parameters["properties"]["category"])


async def test_list_publications_tool_normalizes_category() -> None:
    res = await server.list_publications(category="carta circular")
    assert res["count"] == 2


# --- jurisdiction names -------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("washington", "WA"),
        ("Washington", "WA"),
        ("WA", "WA"),
        ("wa", "WA"),
        ("Puerto Rico", "PR"),
        ("puerto_rico", "PR"),
        ("federal", "US"),
        ("United States", "US"),
        ("new-york", "NY"),
        ("Washington DC", "DC"),
        ("atlantis", "ATLANTIS"),
    ],
)
def test_resolve_jurisdiction(value: str, code: str) -> None:
    assert resolve_jurisdiction(value) == code


def test_every_db_jurisdiction_has_a_name() -> None:
    assert {s["jurisdiction"] for s in INITIAL_STATUTES} <= set(JURISDICTION_NAMES.values())


def test_corpus_washington_filters_to_wa(db: TaxDatabase) -> None:
    """corpus='washington' (Nexus's failing input) maps to jurisdiction WA."""
    rows = db.search_statutes(query="capital gains", corpus="washington", limit=20)
    assert set(_citations(rows)) == WA_CAPITAL_GAINS
    assert {r["jurisdiction"] for r in rows} == {"WA"}
    assert db.search_statutes(query="capital gains", corpus="Washington", limit=20) == rows


def test_jurisdiction_full_name(db: TaxDatabase) -> None:
    by_name = db.search_statutes(query="capital gains", jurisdiction="washington", limit=20)
    by_code = db.search_statutes(query="capital gains", jurisdiction="WA", limit=20)
    assert by_name == by_code
    assert set(_citations(by_name)) == WA_CAPITAL_GAINS


def test_corpus_code_still_filters_by_corpus(db: TaxDatabase) -> None:
    rows = db.search_statutes(query="", corpus="rcw", limit=20)
    assert {r["corpus"] for r in rows} == {"RCW"}


def test_corpus_name_conflicting_with_jurisdiction_matches_nothing(db: TaxDatabase) -> None:
    assert db.search_statutes(query="tax", jurisdiction="PR", corpus="washington") == []
    same = db.search_statutes(query="tax", jurisdiction="WA", corpus="washington", limit=20)
    assert same and {r["jurisdiction"] for r in same} == {"WA"}


def test_unknown_corpus_matches_nothing(db: TaxDatabase) -> None:
    assert db.search_statutes(query="tax", corpus="atlantis") == []


# --- ranked multi-term search -------------------------------------------------


def test_extra_words_still_find_wa_capital_gains(db: TaxDatabase) -> None:
    """'capital gains tax RCW' on WA (Nexus's failing input) returns the RCW rows first."""
    rows = db.search_statutes(query="capital gains tax RCW", jurisdiction="WA", limit=5)
    assert set(_citations(rows)[:2]) == WA_CAPITAL_GAINS


def test_exact_phrase_ranks_first(db: TaxDatabase) -> None:
    rows = db.search_statutes(query="net investment income", jurisdiction="US", limit=50)
    assert rows[0]["id"] == "us-irc-1411"


def test_rank_by_term_count_then_year_then_id(db: TaxDatabase) -> None:
    rows = db.search_statutes(query="capital gains washington", limit=200)
    ids = [r["id"] for r in rows]
    hay = {
        s["id"]: " ".join(
            [s["title"], s["citation"], s["text_en"] or "", s["text_es"] or ""]
        ).lower()
        for s in INITIAL_STATUTES
    }
    terms = ["capital", "gains", "washington"]
    keys = [
        (-sum(t in hay[i] for t in terms), -r["effective_year"], i)
        for i, r in zip(ids, rows, strict=True)
    ]
    assert keys == sorted(keys)
    assert rows[0]["jurisdiction"] == "WA"
    # Deterministic across calls.
    assert db.search_statutes(query="capital gains washington", limit=200) == rows


def test_single_term_order_unchanged(db: TaxDatabase) -> None:
    rows = db.search_statutes(query="residence", limit=50)
    keys = [(-r["effective_year"], r["id"]) for r in rows]
    assert keys == sorted(keys)


def test_empty_query_still_lists_rows(db: TaxDatabase) -> None:
    assert len(db.search_statutes(query="", limit=500)) == len(INITIAL_STATUTES)


def test_limit_zero_or_negative(db: TaxDatabase) -> None:
    assert db.search_statutes(query="tax", limit=0) == []
    assert db.search_statutes(query="tax", limit=-1) == []


def test_search_terms_drops_stopwords_and_short_terms() -> None:
    assert search_terms("The tax on capital gains, for WA (RCW) tax") == [
        "tax",
        "capital",
        "gains",
        "rcw",
    ]
    assert search_terms("section 1 of § 933") == ["section", "1", "933"]
    assert search_terms("a of the") == []


def test_stopword_only_query_uses_phrase(db: TaxDatabase) -> None:
    # No usable terms: falls back to the whole phrase, which matches nothing here.
    assert db.search_statutes(query="of the and", limit=5) == []


async def test_search_tool_accepts_names() -> None:
    res = await server.search_tax_code(query="capital gains tax RCW", corpus="washington")
    assert set(_citations(res["results"])[:2]) == WA_CAPITAL_GAINS


# --- empty years and missing titles -------------------------------------------


def _row(sid: str, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": sid,
        "jurisdiction": "WA",
        "corpus": "RCW",
        "citation": f"RCW zz.{sid}",
        "section": sid,
        "title": "Zebra levy",
        "text_es": None,
        "text_en": "zebra quokka levy text",
        "effective_year": 2020,
        "superseded_year": None,
        "source_url": "https://example.test",
        "cross_references": [],
    }
    base.update(over)
    return base


def test_empty_effective_year_sorts_last_without_crash(db: TaxDatabase) -> None:
    db.insert_statute(_row("zz-new", effective_year=2024))
    db.insert_statute(_row("zz-null", effective_year=None))
    db.insert_statute(_row("zz-old", effective_year=2001))
    rows = db.search_statutes(query="zebra quokka levy", limit=10)
    # All three match every term, so year decides; real rows match fewer terms.
    assert [r["id"] for r in rows][:3] == ["zz-new", "zz-old", "zz-null"]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([2001, None, 2024, "abc", "", " 2010 "], [2024, " 2010 ", 2001]),
    ],
)
def test_year_sort_key_puts_empty_and_non_numeric_last(
    values: list[object], expected: list[object]
) -> None:
    ordered = sorted(values, key=year_sort_key)
    assert ordered[:3] == expected
    assert set(map(str, ordered[3:])) == {"None", "abc", ""}
    assert year_sort_key(None) == year_sort_key("abc") == year_sort_key("")


def test_row_without_title_or_citation_still_matches(db: TaxDatabase) -> None:
    db.insert_statute(_row("zz-bare", title=None, citation=None))
    rows = db.search_statutes(query="quokka", limit=10)
    assert [r["id"] for r in rows] == ["zz-bare"]


@pytest.mark.parametrize("query", ["income from §933 Puerto Rico", "income from § 933 Puerto Rico"])
def test_section_sign_terms_find_933(db: TaxDatabase, query: str) -> None:
    """A leading '§' is stripped from terms, and a lone '§' token is dropped."""
    assert "933" in search_terms(query)
    assert "§" not in search_terms(query) and "§933" not in search_terms(query)
    rows = db.search_statutes(query=query, jurisdiction="US", limit=5)
    assert "us-irc-933" in [r["id"] for r in rows]
