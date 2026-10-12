"""Unit tests for the DuckDB database layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from taxcode_mcp.db import INITIAL_PUBLICATIONS, INITIAL_STATUTES, TaxDatabase


def test_initial_db_seeds_statutes_and_publications() -> None:
    """A fresh in-memory database initializes with default seed data."""
    test_db = TaxDatabase(":memory:")
    statutes = test_db.search_statutes(query="", limit=100)
    assert len(statutes) == len(INITIAL_STATUTES)

    pubs = test_db.list_publications(limit=100)
    assert len(pubs) == len(INITIAL_PUBLICATIONS)

    # Test get_connection on memory mode
    conn = test_db.get_connection()
    assert conn is not None


def test_lookup_statute_by_citation_and_section() -> None:
    """Lookup finds statutes by exact citation, section, or normalized format."""
    test_db = TaxDatabase(":memory:")

    # Lookup 26 U.S.C. § 933
    res_en = test_db.lookup_statute("26 U.S.C. § 933", lang="en")
    assert res_en is not None
    assert res_en["citation"] == "26 U.S.C. § 933"
    assert res_en["section"] == "933"
    assert "bona fide resident" in res_en["operative_text"]
    assert res_en["language"] == "en"
    assert res_en["official_language"] == "en"

    # Spanish language lookup
    res_es = test_db.lookup_statute("933", lang="es")
    assert res_es is not None
    assert "residente de buena fe" in res_es["operative_text"]
    assert res_es["language"] == "es"

    # Normalized spacing lookup
    res_norm = test_db.lookup_statute("26U.S.C.§933")
    assert res_norm is not None

    # Puerto Rico official language flag
    res_pr = test_db.lookup_statute("PR CRI Sec. 1031.01")
    assert res_pr is not None
    assert res_pr["jurisdiction"] == "PR"
    assert res_pr["official_language"] == "es"

    # Lookup with jurisdiction filter
    res_filtered = test_db.lookup_statute("937", jurisdiction="US")
    assert res_filtered is not None
    assert res_filtered["jurisdiction"] == "US"

    res_miss_filter = test_db.lookup_statute("937", jurisdiction="PR")
    assert res_miss_filter is None

    # Non-existent statute
    assert test_db.lookup_statute("Non-existent 9999") is None


def test_search_statutes_filters_and_limits() -> None:
    """Search matches keywords and respects jurisdiction, corpus, and limits."""
    test_db = TaxDatabase(":memory:")

    # General search
    results = test_db.search_statutes("residence", limit=3)
    assert 1 <= len(results) <= 3

    # Filter by jurisdiction and corpus simultaneously
    pr_results = test_db.search_statutes(
        "exención", jurisdiction="PR", corpus="LEY60", lang="es", limit=5
    )
    assert len(pr_results) >= 1
    assert all(r["jurisdiction"] == "PR" and r["corpus"] == "LEY60" for r in pr_results)


def test_list_publications_filters() -> None:
    """List publications filters by category and date."""
    test_db = TaxDatabase(":memory:")

    all_pubs = test_db.list_publications(limit=10)
    assert len(all_pubs) >= 1

    # Filter by category
    cc_pubs = test_db.list_publications(category="carta-circular")
    assert len(cc_pubs) >= 1
    assert all(p["category"] == "carta-circular" for p in cc_pubs)

    # Filter by since_date
    recent_pubs = test_db.list_publications(since_date="2025-01-01")
    assert len(recent_pubs) >= 1
    assert all(p["doc_date"] >= "2025-01-01" for p in recent_pubs)


def test_insert_and_delete_statute() -> None:
    """Insert and delete operations modify the database correctly."""
    test_db = TaxDatabase(":memory:")
    new_statute: dict[str, Any] = {
        "id": "custom-1",
        "jurisdiction": "US",
        "corpus": "IRC",
        "citation": "26 U.S.C. § 1",
        "section": "1",
        "title": "Tax imposed",
    }

    test_db.insert_statute(new_statute)
    found = test_db.lookup_statute("26 U.S.C. § 1")
    assert found is not None
    assert found["id"] == "custom-1"
    assert found["effective_year"] == 2026

    deleted = test_db.delete_statute("custom-1")
    assert deleted is True
    assert test_db.lookup_statute("26 U.S.C. § 1") is None

    # Deleting non-existent ID returns False
    assert test_db.delete_statute("non-existent") is False


def test_file_based_duckdb_lifecycle(tmp_path: Path) -> None:
    """A file-backed DuckDB instance can be initialized, queried, modified, and closed."""
    db_file = str(tmp_path / "test_tax.duckdb")
    file_db = TaxDatabase(db_file)

    conn = file_db.get_connection()
    try:
        rows = conn.execute("SELECT COUNT(*) FROM statutes;").fetchone()
        assert rows is not None and rows[0] == len(INITIAL_STATUTES)
    finally:
        conn.close()

    # Re-instantiating on the same file does not re-insert duplicate seeds
    second_db = TaxDatabase(db_file)
    assert len(second_db.search_statutes(query="", limit=100)) == len(INITIAL_STATUTES)

    # Insert on file db
    second_db.insert_statute(
        {
            "id": "file-1",
            "jurisdiction": "US",
            "corpus": "IRC",
            "citation": "26 U.S.C. § 2",
            "section": "2",
            "title": "Definitions",
        }
    )
    assert second_db.lookup_statute("26 U.S.C. § 2") is not None
    assert len(second_db.list_publications()) == len(INITIAL_PUBLICATIONS)
    assert second_db.delete_statute("file-1") is True
