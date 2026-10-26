"""A lookup by record id for a tax year outside that row's window names the row for that year.

The response is still the plain ``not_found`` result: only its message gains one sentence, and
only when another row with the same jurisdiction and citation answers the year.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from taxcode_mcp import server
from taxcode_mcp.db import INITIAL_STATUTES, TaxDatabase, db
from taxcode_mcp.disclaimers import output_notice, reference_labels
from taxcode_mcp.paging import encode_cursor

YEARS = range(2015, 2029)
POINTER = " That record id names another version; tax year {year} is answered by '{other}'."
NOT_FOUND_KEYS = {
    "status",
    "query",
    "tax_year",
    "jurisdiction",
    "message",
    *reference_labels(),
    *output_notice(),
}
_CITATIONS = Counter((r["jurisdiction"], r["citation"]) for r in INITIAL_STATUTES)
# Rows that share their citation with another version: the only ids a pointer can name.
VERSIONED = [r for r in INITIAL_STATUTES if _CITATIONS[(r["jurisdiction"], r["citation"])] > 1]

# 6020.10 rows: tax year -> the row that answers it (no row before 2019).
SPLIT_ENACTED = "pr-ley60-6020-10-2019-as-enacted"
SPLIT_2019 = "pr-ley60-6020-10-2019"
SPLIT_YEARS = {
    2019: SPLIT_ENACTED,
    **dict.fromkeys(range(2020, 2022), SPLIT_2019),
    **dict.fromkeys(range(2022, 2024), "pr-ley60-6020-10-2022"),
    2024: "pr-ley60-6020-10-2024",
    2025: "pr-ley60-6020-10-2025",
    **dict.fromkeys(range(2026, 2029), "pr-ley60-6020-10"),
}


def _plain(query: str, year: int | None = None, jurisdiction: str | None = None) -> str:
    """The not-found message without a pointer."""
    msg = f"Statute matching '{query}'"
    if year:
        msg += f" for tax year {year}"
    if jurisdiction:
        msg += f" in jurisdiction '{jurisdiction}'"
    return f"{msg} not found in indexed corpora."


def _assert_not_found(res: dict[str, Any], query: str, year: int | None, jur: str | None) -> None:
    assert res["status"] == "not_found"
    assert set(res) == NOT_FOUND_KEYS
    assert (res["query"], res["tax_year"], res["jurisdiction"]) == (query, year, jur)
    assert isinstance(res["message"], str)


@pytest.mark.asyncio
async def test_an_out_of_window_id_names_the_row_a_citation_lookup_gives_that_year() -> None:
    pointed: Counter[str] = Counter()
    for row in VERSIONED:
        for year in YEARS:
            for jur in (None, row["jurisdiction"]):
                res = await server.lookup_statute(row["id"], jur, year)
                if db.lookup_statute(row["id"], jur, year) is not None:
                    assert res["status"] == "success" and res["data"]["id"] == row["id"]
                    continue
                _assert_not_found(res, row["id"], year, jur)
                by_citation = db.lookup_statute(row["citation"], row["jurisdiction"], year)
                expected = _plain(row["id"], year, jur)
                if by_citation is not None:
                    expected += POINTER.format(year=year, other=by_citation["id"])
                    pointed[row["id"]] += 1
                assert res["message"] == expected, (row["id"], year, jur)
    assert pointed[SPLIT_2019] and len(pointed) > 10


@pytest.mark.asyncio
async def test_each_year_outside_the_2020_window_points_to_its_6020_10_row() -> None:
    for year in YEARS:
        res = await server.lookup_statute(SPLIT_2019, tax_year=year)
        if year in (2020, 2021):
            assert res["status"] == "success" and res["data"]["id"] == SPLIT_2019
            continue
        _assert_not_found(res, SPLIT_2019, year, None)
        expected = _plain(SPLIT_2019, year)
        if year in SPLIT_YEARS:
            expected += POINTER.format(year=year, other=SPLIT_YEARS[year])
        assert res["message"] == expected, year


@pytest.mark.asyncio
async def test_messages_without_another_version_are_unchanged() -> None:
    cases: list[tuple[str, str | None, int | None]] = [
        ("pr-nonexistent-0000", None, 2024),  # unknown id
        ("pr-nonexistent-0000", None, None),
        ("Ley 60-2019 Sec. 6020.10", None, 2015),  # citation miss
        ("6020.10", "PR", 2018),
        (SPLIT_2019, "PR", 2018),  # no row answers 2018
        (SPLIT_2019, "US", 2024),  # another jurisdiction
    ]
    for query, jur, year in cases:
        res = await server.lookup_statute(query, jur, year)
        _assert_not_found(res, query, year, jur)
        assert res["message"] == _plain(query, year, jur), (query, jur, year)


@pytest.mark.asyncio
async def test_a_page_cursor_miss_gets_no_pointer() -> None:
    gone = encode_cursor("us-irc-0000", "en", 0)
    res = await server.lookup_statute(SPLIT_2019, tax_year=2024, page_cursor=gone)
    _assert_not_found(res, SPLIT_2019, 2024, None)
    assert res["message"] == _plain(SPLIT_2019, 2024)


@pytest.mark.asyncio
async def test_the_pointer_keeps_the_query_as_given() -> None:
    query = " PR-LEY60-6020-10-2019 "
    res = await server.lookup_statute(query, "pr", 2024)
    _assert_not_found(res, query, 2024, "pr")
    expected = _plain(query, 2024, "pr") + POINTER.format(year=2024, other="pr-ley60-6020-10-2024")
    assert res["message"] == expected


def _check_version_for_year(tdb: TaxDatabase) -> None:
    for year in YEARS:
        other = SPLIT_YEARS.get(year) if year not in (2020, 2021) else None
        assert tdb.version_for_year(SPLIT_2019, year) == other, year
        assert tdb.version_for_year(SPLIT_2019, year, " pr ") == other, year
        assert tdb.version_for_year(SPLIT_2019, year, "US") is None
    assert tdb.version_for_year("pr-nonexistent-0000", 2024) is None
    assert tdb.version_for_year("Ley 60-2019 Sec. 6020.10", 2024) is None  # a citation, not an id


def test_version_for_year_in_memory() -> None:
    _check_version_for_year(TaxDatabase(":memory:"))


def test_version_for_year_file_backed(tmp_path: Path) -> None:
    _check_version_for_year(TaxDatabase(str(tmp_path / "tax.duckdb")))
