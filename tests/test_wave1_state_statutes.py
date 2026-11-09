"""Wave 1 depth, honest source labels, dated windows and insertion-only searches."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastmcp import Client

from scripts.build_corrected_pins import full_sha
from scripts.build_prior_years_pins import answer, plan
from taxcode_mcp.catalog import load_catalog, manifest_errors, render_docs
from taxcode_mcp.db import db, is_official_text
from taxcode_mcp.server import mcp
from taxcode_mcp.tools.compliance import SUPPORTED_STATE_JURISDICTIONS

FIXTURES = Path(__file__).parent / "fixtures" / "wave1"
SOURCES = json.loads((FIXTURES / "sources.json").read_text())
WAVE1_IDS = frozenset(SOURCES)
SEARCHES = json.loads((FIXTURES / "searches.json").read_text())
COUNTS = {"NY": 18, "NJ": 15, "CA": 18, "IL": 16, "MA": 14}
# Each list covers residency, source, imposition, deductions, resident credit, PTET, withholding.
COVERAGE = {
    "NY": [
        "ny-tax-605",
        "ny-tax-631",
        "ny-tax-601",
        "ny-tax-614",
        "ny-tax-620",
        "ny-tax-861",
        "ny-tax-671",
    ],
    "NJ": [
        "nj-stat-54a-1-2",
        "nj-stat-54a-5-8",
        "nj-stat-54a-2-1",
        "nj-stat-54a-3-1",
        "nj-stat-54a-4-1",
        "nj-stat-54a-12-3",
        "nj-stat-54a-7-1",
    ],
    "CA": [
        "ca-rtc-17014",
        "ca-rtc-17951",
        "ca-rtc-17043",
        "ca-rtc-17054",
        "ca-rtc-18001",
        "ca-rtc-19900",
        "ca-rtc-18662",
    ],
    "IL": [
        "il-stat-35-ilcs-5-1501",
        "il-stat-35-ilcs-5-302",
        "il-stat-35-ilcs-5-201",
        "il-stat-35-ilcs-5-204",
        "il-stat-35-ilcs-5-601-b-3",
        "il-stat-35-ilcs-5-201-p",
        "il-stat-35-ilcs-5-701",
    ],
    "MA": [
        "ma-gl-62-1f",
        "ma-gl-62-5a",
        "ma-const-amend-44",
        "ma-gl-62-3-exemptions",
        "ma-gl-62-6-a",
        "ma-gl-63d-2",
        "ma-gl-62b-2",
    ],
}


@pytest.mark.parametrize("state", COUNTS)
def test_current_section_counts_cover_all_seven_topics(state: str) -> None:
    rows = [
        s
        for s in load_catalog().statutes
        if s["jurisdiction"] == state and s["superseded_year"] is None
    ]
    assert len(rows) == COUNTS[state]
    assert 12 <= len(rows) <= 20
    assert set(COVERAGE[state]) <= {s["id"] for s in rows}


def test_existing_rows_are_byte_for_byte_unchanged_in_both_languages() -> None:
    expected = json.loads((FIXTURES / "existing_rows.json").read_text())
    rows = {s["id"]: s for s in load_catalog().statutes}
    assert len(expected) == 17
    # Canada (#84) adds an empty text_fr on every loaded statute. The pins cover the
    # fields those rows had before that key, English and Spanish included.
    assert {
        row_id: full_sha({k: v for k, v in rows[row_id].items() if k != "text_fr"})
        for row_id in expected
    } == expected


def test_new_rows_have_honest_labels_and_exact_manifest_source_pins() -> None:
    catalog = load_catalog(with_manifest=True)
    assert manifest_errors(catalog) == []
    manifest = {e["id"]: e for e in catalog.manifest}
    rows = {s["id"]: s for s in catalog.statutes}
    assert len(WAVE1_IDS) == 67
    for row_id, expected in SOURCES.items():
        row, entry = rows[row_id], manifest[row_id]
        assert len(row["text_en"]) > 150 and len(row["text_es"]) > 150
        assert row["source_note"] and row["breadcrumbs"] and row["cross_references"]
        assert entry["retrieved"] in {"2026-11-05", "not_recorded"}
        assert {
            "url": entry["url"],
            "sha256": entry["content_sha256"],
            "verification": entry["verification"],
        } == expected
        assert row["source_url"] == entry["url"]
        assert not is_official_text(row["source_url"])
        for lang in ("en", "es"):
            found = db.lookup_statute(row_id, row["jurisdiction"], lang=lang)
            assert found is not None
            assert found["content_type"] == "reference_summary"
            assert found["unofficial_summary"] is True
        if entry["verification"] == "summary":
            assert len(entry["content_sha256"]) == 64
        else:
            assert entry["verification"] == "not_recorded"
            assert entry["content_sha256"] == "not_recorded"
    unofficial = [
        e
        for e in catalog.manifest
        if e["id"] in WAVE1_IDS and e["url"].startswith("https://malegislature.gov/")
    ]
    assert len(unofficial) == 5
    assert all(e["official"] is False and e["verification"] == "not_recorded" for e in unofficial)
    assert all(
        manifest[r]["edition"] == "as_enacted"
        for r in ("nj-stat-54a-5-8", "nj-stat-54a-5-8-2022", "nj-stat-54a-4-1-1")
    )


@pytest.mark.parametrize(
    "state,section,old_year,new_year,old_id,new_id",
    [
        ("NJ", "54A:5-8", 2022, 2025, "nj-stat-54a-5-8-2022", "nj-stat-54a-5-8"),
        ("CA", "17043", 2024, 2025, "ca-rtc-17043-2024", "ca-rtc-17043"),
        ("MA", "62-3(B)(a)(9)", 2022, 2025, "ma-gl-62-3-rent-2022", "ma-gl-62-3-rent"),
    ],
)
def test_dated_windows_resolve_only_verified_years(
    state: str, section: str, old_year: int, new_year: int, old_id: str, new_id: str
) -> None:
    for year, expected in ((old_year, old_id), (new_year, new_id)):
        row = db.lookup_statute(section, state, tax_year=year)
        assert row is not None and row["id"] == expected
        assert {v["id"] for v in row["available_versions"]} == {old_id, new_id}
    assert db.lookup_statute(section, state, tax_year=old_year - 1) is None
    assert db.lookup_statute(section, state)["id"] == new_id


def test_source_corrections_and_summary_docs_are_explicit() -> None:
    catalog = load_catalog(with_manifest=True)
    rows = {s["id"]: s for s in catalog.statutes}
    assert "subsection (e)" in rows["nj-stat-54a-5-8"]["text_en"]
    assert "54A:4-1.1" in rows["nj-stat-54a-4-1"]["text_en"]
    assert "4 U.S.C. § 114" in rows["ca-rtc-17952-5"]["text_en"]
    assert "not former subsection (y)" in rows["ma-gl-62-6-x"]["text_en"]
    docs = render_docs(catalog.statutes, catalog.publications)
    for state in COUNTS:
        pages = [
            v for k, v in docs.items() if k.startswith(state.lower() + "/") and k.endswith(".md")
        ]
        assert all("## Unofficial Reference Summary (English)" in p for p in pages)
        assert all("## Resumen de referencia no oficial (Español)" in p for p in pages)
    il = next(e for e in catalog.manifest if e["id"] == "il-rates")
    source = next(s for s in il["year_sources"] if s["url"].endswith("fy-2024-18.html"))
    assert source["values"] == {"exemption.single": 2775.0}


async def test_each_changed_search_is_an_exact_insertion_only_change() -> None:
    calls = plan(load_catalog().statutes, SUPPORTED_STATE_JURISDICTIONS)
    ranked = db.search_statutes
    # Canada rows (#84) rank into the same pages. The fixture was recorded before them.
    canada_ids = frozenset(s["id"] for s in load_catalog().statutes if s["jurisdiction"] == "CAN")

    def without(drop: frozenset[str]) -> Any:
        def search(*, limit: int = 5, offset: int = 0, **kw: Any) -> list[dict[str, Any]]:
            rows = ranked(**kw, limit=offset + limit + len(drop), offset=0)
            kept = [r for r in rows if r["id"] not in drop]
            return kept[offset : offset + limit]

        return search

    async with Client(mcp) as client:
        for key, expected in SEARCHES.items():
            assert set(expected["inserted"]) <= WAVE1_IDS
            assert expected["inserted"]
            with patch.object(db, "search_statutes", without(canada_ids)):
                expanded = await answer(client, calls[key])
            assert full_sha(expanded) == expected["after"], key
            with patch.object(db, "search_statutes", without(canada_ids | WAVE1_IDS)):
                rebuilt = await answer(client, calls[key])
            assert full_sha(rebuilt) == expected["before"], key
